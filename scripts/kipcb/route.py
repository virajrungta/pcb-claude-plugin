"""Autorouting with Freerouting (Specctra DSN/SES round trip) and ground pours."""

import glob
import os
import re
import shutil
import subprocess
import time

from . import checks, kienv, render as rendermod
from .kicad import mm, pcbnew

# Freerouting >= 2.2 needs Java 25; 2.1.0 runs on Java 21+.
LATEST_FOR_JAVA = [(25, "2.4.1"), (21, "2.1.0")]
URL = "https://github.com/freerouting/freerouting/releases/download/v{v}/freerouting-{v}.jar"


def java_version():
    java = shutil.which("java")
    if not java:
        return None
    try:
        p = subprocess.run([java, "-version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=20)
    except Exception:
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', p.stdout)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major


def find_jar():
    env = os.environ.get("KIPCB_FREEROUTING_JAR")
    if env and os.path.exists(env):
        return env
    jars = sorted(glob.glob(os.path.join(kienv.cache_dir(), "freerouting-*.jar")))
    return jars[-1] if jars else None


def setup(version=None):
    jv = java_version()
    if not jv:
        print("Java not found. Install a JRE (Java 21+; Java 25 for the newest Freerouting), "
              "e.g. `brew install --cask temurin`.")
        return 1
    if version is None:
        for need, v in LATEST_FOR_JAVA:
            if jv >= need:
                version = v
                break
    if version is None:
        print("Java %d is too old; Freerouting needs Java 21+." % jv)
        return 1
    dest = os.path.join(kienv.cache_dir(), "freerouting-%s.jar" % version)
    if os.path.exists(dest):
        print("already installed: %s" % dest)
        return 0
    url = URL.format(v=version)
    print("downloading %s (Java %d detected)" % (url, jv))
    tmp = dest + ".part"
    try:
        from urllib.request import urlopen
        with urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:
        if shutil.which("curl"):
            subprocess.run(["curl", "-fsSL", "-o", tmp, url], check=True)
        else:
            raise RuntimeError("download failed: %s" % e)
    os.replace(tmp, dest)
    print("installed: %s (%.1f MB)" % (dest, os.path.getsize(dest) / 1e6))
    return 0


def _delete(board, item):
    # Delete() frees the item on the C++ side; Remove() leaves SWIG owning a dangling wrapper
    if hasattr(board, "Delete"):
        board.Delete(item)
    else:
        board.Remove(item)


def _zones(board):
    return [board.GetArea(i) for i in range(board.GetAreaCount())]


def _strip_routing(board):
    for t in list(board.GetTracks()):
        _delete(board, t)
    for z in _zones(board):
        if not z.GetIsRuleArea():
            _delete(board, z)


def _outline_poly(board):
    pn = pcbnew()
    poly = pn.SHAPE_POLY_SET()
    ok = board.GetBoardPolygonOutlines(poly)
    if not ok or poly.OutlineCount() == 0:
        bb = board.GetBoardEdgesBoundingBox()
        poly = pn.SHAPE_POLY_SET()
        poly.NewOutline()
        for x, y in ((bb.GetLeft(), bb.GetTop()), (bb.GetRight(), bb.GetTop()),
                     (bb.GetRight(), bb.GetBottom()), (bb.GetLeft(), bb.GetBottom())):
            poly.Append(x, y)
    return poly


def add_pours(board, net_name, clearance, layers=None):
    pn = pcbnew()
    net = board.FindNet(net_name)
    if net is None:
        return 0
    poly = _outline_poly(board)
    count = 0
    ids = layers or [pn.F_Cu, pn.B_Cu]
    for layer in ids:
        z = pn.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(net)
        # write into the zone's own outline: SetOutline() takes ownership of a
        # pointer that Python would garbage-collect underneath it
        chain = poly.Outline(0)
        outline = z.Outline()
        outline.NewOutline()
        for i in range(chain.PointCount()):
            pt = chain.CPoint(i)
            outline.Append(pt.x, pt.y)
        z.SetLocalClearance(mm(clearance))
        z.SetMinThickness(mm(0.25))
        z.SetThermalReliefGap(mm(0.5))
        z.SetThermalReliefSpokeWidth(mm(0.5))
        z.SetPadConnection(pn.ZONE_CONNECTION_THERMAL)
        z.SetIslandRemovalMode(pn.ISLAND_REMOVAL_MODE_ALWAYS)
        z.SetAssignedPriority(0)
        z.SetZoneName("%s_%s" % (net_name.strip("/"), board.GetLayerName(layer)))
        board.Add(z)
        count += 1
    _fill(board)
    return count


def _fill(board):
    pn = pcbnew()
    board.BuildConnectivity()
    zones = board.Zones()          # keep a reference for the duration of the fill
    filler = pn.ZONE_FILLER(board)
    filler.Fill(zones)


def add_stitching_vias(board, net_name, pitch=5.0, drill=0.3, dia=0.6):
    """Sprinkle vias joining top and bottom pours where both layers are filled."""
    pn = pcbnew()
    net = board.FindNet(net_name)
    if net is None:
        return 0
    zones = [z for z in _zones(board) if z.GetNetname() == net_name and not z.GetIsRuleArea()]
    top = [z for z in zones if z.GetLayer() == pn.F_Cu]
    bot = [z for z in zones if z.GetLayer() == pn.B_Cu]
    if not top or not bot:
        return 0
    bb = board.GetBoardEdgesBoundingBox()
    r = mm(dia / 2 + 0.6)   # needs this much solid copper around it on both layers
    added = 0
    y = bb.GetTop() + mm(pitch / 2)
    while y < bb.GetBottom():
        x = bb.GetLeft() + mm(pitch / 2)
        while x < bb.GetRight():
            ok = True
            for z in (top[0], bot[0]):
                fp = z.GetFilledPolysList(z.GetLayer())
                for dx, dy in ((0, 0), (r, 0), (-r, 0), (0, r), (0, -r)):
                    if not fp.Contains(pn.VECTOR2I(x + dx, y + dy)):
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                v = pn.PCB_VIA(board)
                v.SetPosition(pn.VECTOR2I(x, y))
                v.SetDrill(mm(drill)); v.SetWidth(mm(dia))
                v.SetNet(net)
                board.Add(v)
                added += 1
            x += mm(pitch)
        y += mm(pitch)
    if added:
        _fill(board)
    return added


def _rules_file(board, path, bottom_cost):
    """Freerouting autoroute settings. Making the bottom layer expensive keeps
    signals on top so the bottom stays an unbroken ground plane (less noise)."""
    pn = pcbnew()
    names = [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()]
    lines = ["(rules PCB board", "  (snap_angle fortyfive_degree)", "  (autoroute_settings",
             "    (fanout off) (autoroute on) (postroute on) (vias on)",
             "    (via_costs 50) (plane_via_costs 5) (start_ripup_costs 100) (start_pass_no 1)"]
    for i, n in enumerate(names):
        bottom = (i == len(names) - 1)
        pref, against = (bottom_cost, bottom_cost * 1.5) if bottom else (1.0, 2.0)
        lines.append("    (layer_rule %s (active on) (preferred_direction %s)"
                     " (preferred_direction_trace_costs %.2f) (against_preferred_direction_trace_costs %.2f))"
                     % (n, "horizontal" if i % 2 == 0 else "vertical", pref, against))
    lines += ["  )", ")"]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _freeroute(pn, board, jar, dsn, ses, out, passes, timeout, bottom_cost=None):
    """Export DSN, run Freerouting, return the number of unrouted connections."""
    if not pn.ExportSpecctraDSN(board, dsn):
        raise RuntimeError("DSN export failed")
    rules = dsn[:-4] + ".rules"
    if os.path.exists(rules):
        os.remove(rules)
    extra = []
    if bottom_cost:
        _rules_file(board, rules, bottom_cost)
        extra = ["-dr", rules]
    if os.path.exists(ses):
        os.remove(ses)
    budget = max(30, timeout - 30)
    cmd = ["java", "-jar", jar, "-de", dsn, "-do", ses] + extra + ["-mp", str(passes),
           "--router.max_passes=%d" % passes,
           "--router.job_timeout=%02d:%02d:%02d" % (budget // 3600, budget % 3600 // 60, budget % 60),
           "--gui.enabled=false"]
    print("routing with %s (max %d passes, time budget %ds)..." % (os.path.basename(jar), passes, budget))
    t0 = time.time()
    log_path = os.path.join(out, "freerouting.log")
    with open(log_path, "w") as log:
        try:
            p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, cwd=out)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    with open(log_path, errors="replace") as f:
        text = f.read()
    print("freerouting finished in %.0fs (exit %s), log: %s" % (time.time() - t0, rc, log_path))
    if not os.path.exists(ses):
        print(text[-2000:])
        raise RuntimeError("Freerouting produced no session file")
    m = re.findall(r'"incomplete_count":\s*(\d+)', text) or re.findall(r"\((\d+) unrouted\)", text)
    return int(m[-1]) if m else 0


BOTTOM_COST = 3.0


def bottom_signal_length(board, ground=None):
    """mm of non-ground track on the bottom copper layer (each mm slices the ground plane)."""
    pn = pcbnew()
    gnd = ground or _guess_ground(board)
    return sum(t.GetLength() for t in board.GetTracks()
               if t.GetClass() == "PCB_TRACK" and t.GetLayer() == pn.B_Cu and t.GetNetname() != gnd) / 1e6


def heal_dangling(board, max_gap=1.5):
    """Bridge track ends that stop just short of a same-net pad.

    Freerouting's pad geometry can differ slightly from KiCad's for stacked or
    custom pads, leaving a stub that misses the pad by a fraction of a mm."""
    pn = pcbnew()
    tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    tol = mm(0.001)

    def touches(pt, net, layer, skip):
        for p in pads:
            if p.GetNetCode() == net and p.IsOnLayer(layer) and p.HitTest(pt):
                return True
        for v in vias:
            if v.GetNetCode() == net and v.HitTest(pt):
                return True
        for t in tracks:
            if t is skip or t.GetNetCode() != net or t.GetLayer() != layer:
                continue
            for q in (t.GetStart(), t.GetEnd()):
                if abs(q.x - pt.x) <= tol and abs(q.y - pt.y) <= tol:
                    return True
            if t.HitTest(pt, 0):
                return True
        return False

    clearance = board.GetDesignSettings().m_MinClearance

    def nearest_on_pad(p, pt):
        # closest point of the pad's bounding box, pulled slightly inside it
        bb = p.GetBoundingBox()
        inset = mm(0.05)
        x = min(max(pt.x, bb.GetLeft() + inset), bb.GetRight() - inset)
        y = min(max(pt.y, bb.GetTop() + inset), bb.GetBottom() - inset)
        q = pn.VECTOR2I(int(x), int(y))
        return q if p.HitTest(q) else p.GetPosition()

    def collides(a, b, net, layer, width):
        steps = max(2, int(((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5 / mm(0.05)))
        reach = int(clearance + width / 2)
        for i in range(steps + 1):
            q = pn.VECTOR2I(int(a.x + (b.x - a.x) * i / steps), int(a.y + (b.y - a.y) * i / steps))
            for p in pads:
                if p.GetNetCode() != net and p.IsOnLayer(layer) and p.HitTest(q, reach):
                    return True
            for o in tracks:
                if o.GetNetCode() != net and o.GetLayer() == layer and o.HitTest(q, reach):
                    return True
            for v in vias:
                if v.GetNetCode() != net and v.HitTest(q, reach):
                    return True
        return False

    healed = 0
    for t in list(tracks):
        for pt in (t.GetStart(), t.GetEnd()):
            if touches(pt, t.GetNetCode(), t.GetLayer(), t):
                continue
            cands = []
            for p in pads:
                if p.GetNetCode() != t.GetNetCode() or not p.IsOnLayer(t.GetLayer()):
                    continue
                q = nearest_on_pad(p, pt)
                d = ((q.x - pt.x) ** 2 + (q.y - pt.y) ** 2) ** 0.5
                if d <= mm(max_gap):
                    cands.append((d, p, q))
            for d, p, q in sorted(cands, key=lambda c: c[0]):
                w = min(t.GetWidth(), p.GetSize().x, p.GetSize().y)
                if collides(pt, q, t.GetNetCode(), t.GetLayer(), w):
                    continue
                seg = pn.PCB_TRACK(board)
                seg.SetStart(pt)
                seg.SetEnd(q)
                seg.SetWidth(w)
                seg.SetLayer(t.GetLayer())
                seg.SetNet(t.GetNet())
                board.Add(seg)
                tracks.append(seg)
                healed += 1
                break
    return healed


def route(pdir, name, passes=100, timeout=600, pour=True, attempts_max=4):
    pn = pcbnew()
    jar = find_jar()
    if not jar:
        print("Freerouting is not installed. Run `kipcb setup-router` (downloads ~60 MB from GitHub).")
        return 2
    pcb = os.path.join(pdir, name + ".kicad_pcb")
    out = os.path.join(pdir, "out")
    os.makedirs(out, exist_ok=True)
    dsn = os.path.join(out, name + ".dsn")
    ses = os.path.join(out, name + ".ses")

    board = pn.LoadBoard(pcb)
    _strip_routing(board)
    ns = board.GetDesignSettings().m_NetSettings
    power = ns.GetNetClassByName("Power") if ns.HasNetclass("Power") else None
    base_w = power.GetTrackWidth() if power is not None else None
    signal_w = ns.GetDefaultNetclass().GetTrackWidth()

    def set_power_width(w):
        if power is None or w is None:
            return
        power.SetTrackWidth(w)
        ns.ClearAllCaches()
        if hasattr(board, "SynchronizeNetsAndNetClasses"):
            board.SynchronizeNetsAndNetClasses(False)

    # Freerouting is randomized, so judge each attempt by KiCad's own connectivity
    # and keep the best. Early attempts make the bottom layer expensive so it stays
    # an unbroken ground plane (lower noise); later ones trade that for completion.
    plan = [(base_w, BOTTOM_COST), (signal_w, BOTTOM_COST), (base_w, None), (signal_w, None)]
    plan = plan[:max(1, attempts_max)]
    best = None
    for i, (width, bcost) in enumerate(plan):
        if i:
            print("retrying: %s, %s" % ("power tracks at signal width" if width == signal_w and power is not None
                                        else "normal power tracks",
                                        "signals kept off bottom layer" if bcost else "both layers free"))
        set_power_width(width)
        per_attempt = max(60, timeout // (len(plan) + 1))
        _freeroute(pn, board, jar, dsn, ses, out, passes, per_attempt, bottom_cost=bcost)
        if not pn.ImportSpecctraSES(board, ses):
            raise RuntimeError("SES import failed")
        heal_dangling(board)
        board.BuildConnectivity()
        missing = board.GetConnectivity().GetUnconnectedCount(True)
        bottom = bottom_signal_length(board)
        print("attempt %d: %d unconnected, %.0f mm of signal on the bottom layer" % (i + 1, missing, bottom))
        kept = os.path.join(out, "%s.attempt%d.ses" % (name, i + 1))
        shutil.copyfile(ses, kept)
        score = (missing, bottom)
        if best is None or score < best[0]:
            best = (score, kept, width)
        if missing == 0:
            break          # attempts run from most to least plane-friendly
        for t in list(board.GetTracks()):
            _delete(board, t)

    if best[1] != kept or best[0][0] != 0:
        for t in list(board.GetTracks()):
            _delete(board, t)
        set_power_width(best[2])
        pn.ImportSpecctraSES(board, best[1])
    healed = heal_dangling(board)
    board.BuildConnectivity()
    if board.GetConnectivity().GetUnconnectedCount(True):
        # completion pass: the DSN export carries the existing tracks, so the
        # router keeps them and only has to finish the few missing connections
        print("completion pass for %d missing connection(s)..." % board.GetConnectivity().GetUnconnectedCount(True))
        _freeroute(pn, board, jar, dsn, ses, out, passes, max(60, timeout // 5))
        for t in list(board.GetTracks()):
            _delete(board, t)
        pn.ImportSpecctraSES(board, ses)
        healed += heal_dangling(board)
        board.BuildConnectivity()
        print("after completion: %d unconnected" % board.GetConnectivity().GetUnconnectedCount(True))
    set_power_width(base_w)   # tracks keep their routed width; the project keeps the intended class
    for f in glob.glob(os.path.join(out, "%s.attempt*.ses" % name)):
        os.remove(f)
    ntracks = sum(1 for t in board.GetTracks() if t.GetClass() == "PCB_TRACK")
    nvias = sum(1 for t in board.GetTracks() if t.GetClass() == "PCB_VIA")
    print("using best attempt: %d unconnected, %d track segments, %d vias%s" % (
        best[0][0], ntracks, nvias, (", %d bridged" % healed) if healed else ""))

    if pour:
        spec_pour = _spec_pour_net(pdir, name, board)
        gnd = spec_pour if spec_pour is not None else _guess_ground(board)
        if gnd:
            add_pours(board, gnd, 0.3)
            n = add_stitching_vias(board, gnd)
            print("poured %s on top and bottom, %d stitching vias" % (gnd, n))
    pn.SaveBoard(pcb, board)
    rc = checks.drc(pdir, name)
    from . import noise
    print()
    noise.run(pdir, name, quiet=True)
    for p in rendermod.render(pdir, name, "pcb"):
        if p.endswith(".png"):
            print("preview:   %s" % p)
    if rc == 0:
        print("\nDRC clean. Next: `kipcb fab %s`" % pdir)
    else:
        print("\nFix remaining DRC issues (adjust placement/rules and re-run build + route, "
              "or finish by hand in KiCad).")
    return rc


def _guess_ground(board):
    names = [str(n) for n in board.GetNetsByName().keys()]
    for cand in ("GND", "/GND", "GNDD", "DGND", "VSS"):
        if cand in names:
            return cand
    return None


def _spec_pour_net(pdir, name, board):
    """Honour board.ground_pour from the spec if the spec sits next to the project."""
    import json
    for cand in (os.path.join(os.path.dirname(pdir), name + ".json"), os.path.join(pdir, name + ".json")):
        if os.path.exists(cand):
            try:
                with open(cand) as f:
                    b = json.load(f).get("board", {})
            except ValueError:
                return None
            if "ground_pour" not in b:
                return None
            gp = b["ground_pour"]
            if not gp:
                return ""
            names = [str(n) for n in board.GetNetsByName().keys()]
            return gp if gp in names else ("/" + gp if "/" + gp in names else None)
    return None
