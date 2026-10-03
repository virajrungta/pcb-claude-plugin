"""Autorouting with Freerouting (Specctra DSN/SES round trip) and ground pours."""

import glob
import json
import os
import re
import shutil
import subprocess
import time

from . import checks, kienv, render as rendermod
from . import ui
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
        ui.say("Java not found. Install a JRE (Java 21+; Java 25 for the newest Freerouting), e.g. %s." % (
            "`winget install EclipseAdoptium.Temurin.21.JDK`" if kienv.WINDOWS else "`brew install --cask temurin`"))
        return 1
    if version is None:
        for need, v in LATEST_FOR_JAVA:
            if jv >= need:
                version = v
                break
    if version is None:
        ui.say("Java %d is too old; Freerouting needs Java 21+." % jv)
        return 1
    dest = os.path.join(kienv.cache_dir(), "freerouting-%s.jar" % version)
    if os.path.exists(dest):
        ui.say("already installed: %s" % dest)
        return 0
    url = URL.format(v=version)
    ui.say("downloading %s (Java %d detected)" % (url, jv))
    tmp = dest + ".part"
    try:
        from urllib.request import urlopen
        with urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:
        if not shutil.which("curl"):
            raise RuntimeError("download failed: %s" % e)
        try:
            subprocess.run(["curl", "-fsSL", "--max-time", "600", "-o", tmp, url], check=True, timeout=660)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e2:
            if os.path.exists(tmp):
                os.remove(tmp)            # never leave a half-downloaded jar behind
            raise RuntimeError("download failed: %s" % e2)
    os.replace(tmp, dest)
    ui.say("installed: %s (%.1f MB)" % (dest, os.path.getsize(dest) / 1e6))
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
    # the pour test samples a few points, so a diagonal track can slip between them:
    # also keep real clearance (+ margin) from every other net's copper
    keep = mm(dia / 2) + board.GetDesignSettings().m_MinClearance + mm(0.25)
    code = net.GetNetCode()
    foreign = [t for t in board.GetTracks() if t.GetNetCode() != code]
    foreign += [p for f in board.GetFootprints() for p in f.Pads() if p.GetNetCode() != code]

    def clear_of_others(x, y):
        q = pn.VECTOR2I(x, y)
        for o in foreign:
            b = o.GetBoundingBox()
            if b.GetRight() < x - keep or b.GetX() > x + keep or b.GetBottom() < y - keep or b.GetY() > y + keep:
                continue
            if o.HitTest(q, keep):
                return False
        return True

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
            if ok and clear_of_others(x, y):
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


def _rules_file(board, path, bottom_cost, fanout=False):
    """Freerouting autoroute settings. Making the bottom layer expensive keeps
    signals on top so the bottom stays an unbroken ground plane (less noise)."""
    names = [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()]
    lines = ["(rules PCB board", "  (snap_angle fortyfive_degree)", "  (autoroute_settings",
             "    (fanout %s) (autoroute on) (postroute on) (vias on)" % ("on" if fanout else "off"),
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


def _export_dsn(pn, board, dsn):
    """Export for Freerouting with stacked duplicate pads hidden.

    Connectors like USB-C stack two same-net pads at one spot (A4/B9). Freerouting
    treats the duplicate as a separate pin in a crowded pin row and often fails
    to reach it. Hiding the duplicate's copper layers only while the DSN is
    written leaves the board untouched, and the stacked pad is still connected
    because it overlaps the routed one."""
    hidden = []
    for fp in board.GetFootprints():
        seen = {}
        for pad in fp.Pads():
            if not pad.GetNetname():
                continue
            key = (pad.GetPosition().x, pad.GetPosition().y, pad.GetNetname(), pad.GetSize().x, pad.GetSize().y)
            if key in seen:
                hidden.append((pad, pad.GetLayerSet()))
                pad.SetLayerSet(pn.LSET())
            else:
                seen[key] = pad
    # Freerouting rounds coordinates differently from KiCad, which can leave a
    # trace a few microns inside the clearance. Ask it for 0.01 mm extra.
    ns = board.GetDesignSettings().m_NetSettings
    classes = [ns.GetDefaultNetclass()] + [ns.GetNetClassByName(n) for n in _class_names(ns)]
    saved = [(nc, nc.GetClearance()) for nc in classes]
    for nc, clr in saved:
        nc.SetClearance(clr + mm(0.01))
    ns.ClearAllCaches()
    try:
        ok = pn.ExportSpecctraDSN(board, dsn)
        if ok:
            ds = board.GetDesignSettings()
            inset = (ds.m_CopperEdgeClearance - ns.GetDefaultNetclass().GetClearance()) / 1e3 - 10
            _inset_boundary(dsn, inset)
    finally:
        for pad, layers in hidden:
            pad.SetLayerSet(layers)
        for nc, clr in saved:
            nc.SetClearance(clr)
        ns.ClearAllCaches()
    return ok


def _prepare(pn, board, jar, dsn, ses, passes, timeout, bottom_cost=None, fanout=False, protect=False):
    """Export the router input for the board as it is now; return (command, log path).
    protect: the router must keep the existing wires (kipcb's fan-out stubs) as they are."""
    if not _export_dsn(pn, board, dsn):
        raise RuntimeError("DSN export failed")
    if protect:
        with open(dsn) as f:
            text = f.read()
        with open(dsn, "w") as f:
            f.write(text.replace("(type route)", "(type protect)"))
    rules = dsn[:-4] + ".rules"
    if os.path.exists(rules):
        os.remove(rules)
    extra = []
    if bottom_cost:
        _rules_file(board, rules, bottom_cost, fanout)
        extra = ["-dr", rules]
    if fanout:      # short escape stubs + vias from fine-pitch pins before the main routing
        extra += ["--router.fanout.enabled=true"]
    if os.path.exists(ses):
        os.remove(ses)
    budget = max(10, timeout)
    cmd = ["java", "-jar", jar, "-de", dsn, "-do", ses] + extra + ["-mp", str(passes),
           "--router.max_passes=%d" % passes,
           "--router.job_timeout=%02d:%02d:%02d" % (budget // 3600, budget % 3600 // 60, budget % 60),
           "--gui.enabled=false"]
    return cmd, dsn[:-4] + ".log"


def _launch(cmd, log_path, cwd):
    log = open(log_path, "w")
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=cwd)
    except OSError:
        log.close()
        raise
    proc._kipcb_log = log
    return proc


def _collect(proc, timeout, log_path, ses):
    """Wait for a router process; return its unrouted count (raises if it produced nothing)."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    proc._kipcb_log.close()
    with open(log_path, errors="replace") as f:
        text = f.read()
    if not os.path.exists(ses):
        ui.say(text[-1500:])
        raise RuntimeError("Freerouting produced no session file (log: %s)" % log_path)
    m = re.findall(r'"incomplete_count":\s*(\d+)', text) or re.findall(r"\((\d+) unrouted\)", text)
    return int(m[-1]) if m else 0


def _freeroute(pn, board, jar, dsn, ses, out, passes, timeout, bottom_cost=None):
    """Run one routing attempt to completion; return the number of unrouted connections."""
    cmd, log_path = _prepare(pn, board, jar, dsn, ses, passes, timeout, bottom_cost)
    return _collect(_launch(cmd, log_path, out), timeout + 20, log_path, ses)


def parallel_attempts():
    """How many routing strategies to run at once. One: two Freerouting processes running
    side by side interfere (both spin through hundreds of passes and leave connections
    unrouted), while one alone usually finishes a normal board in seconds."""
    env = os.environ.get("KIPCB_ROUTE_PARALLEL")
    if env and env.isdigit():
        return max(1, int(env))
    return 1




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
    # group by net once: touches() only ever looks at same-net items
    pads_by_net, vias_by_net, tracks_by_net = {}, {}, {}
    for p in pads:
        pads_by_net.setdefault(p.GetNetCode(), []).append(p)
    for v in vias:
        vias_by_net.setdefault(v.GetNetCode(), []).append(v)
    for t in tracks:
        tracks_by_net.setdefault(t.GetNetCode(), []).append(t)

    def touches(pt, net, layer, skip):
        for p in pads_by_net.get(net, ()):
            if p.IsOnLayer(layer) and p.HitTest(pt):
                return True
        for v in vias_by_net.get(net, ()):
            if v.HitTest(pt):
                return True
        for t in tracks_by_net.get(net, ()):
            if t is skip or t.GetLayer() != layer:
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

    def near(item, x0, y0, x1, y1):
        bb = item.GetBoundingBox()
        return not (bb.GetRight() < x0 or bb.GetX() > x1 or bb.GetBottom() < y0 or bb.GetY() > y1)

    def collides(a, b, net, layer, width):
        steps = max(2, int(((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5 / mm(0.05)))
        reach = int(clearance + width / 2)
        # only items whose box comes within reach of the segment can collide with it
        x0, x1 = min(a.x, b.x) - reach, max(a.x, b.x) + reach
        y0, y1 = min(a.y, b.y) - reach, max(a.y, b.y) + reach
        others = [p for p in pads if p.GetNetCode() != net and p.IsOnLayer(layer) and near(p, x0, y0, x1, y1)]
        others += [o for o in tracks if o.GetNetCode() != net and o.GetLayer() == layer and near(o, x0, y0, x1, y1)]
        others += [v for v in vias if v.GetNetCode() != net and near(v, x0, y0, x1, y1)]
        if not others:
            return False
        for i in range(steps + 1):
            q = pn.VECTOR2I(int(a.x + (b.x - a.x) * i / steps), int(a.y + (b.y - a.y) * i / steps))
            for o in others:
                if o.HitTest(q, reach):
                    return True
        return False

    healed = 0
    for t in list(tracks):
        for pt in (t.GetStart(), t.GetEnd()):
            if touches(pt, t.GetNetCode(), t.GetLayer(), t):
                continue
            cands = []
            for p in pads_by_net.get(t.GetNetCode(), ()):
                if not p.IsOnLayer(t.GetLayer()):
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
                tracks_by_net.setdefault(seg.GetNetCode(), []).append(seg)
                healed += 1
                break
    return healed


def route(pdir, name, passes=100, timeout=600, pour=True, attempts_max=4):
    pn = pcbnew()
    jar = find_jar()
    if not jar:
        ui.always("Freerouting is not installed. Run `kipcb setup-router` (downloads ~65 MB from GitHub).")
        return 2
    if not java_version():
        ui.always("Java isn't installed (or isn't on PATH), and the autorouter needs Java 21+. Install it: %s" % (
            "winget install EclipseAdoptium.Temurin.21.JDK" if kienv.WINDOWS else
            "brew install --cask temurin (macOS) or openjdk-21-jre (Linux)"))
        return 2
    pcb = os.path.join(pdir, name + ".kicad_pcb")
    from . import paths
    out = paths.work(pdir)
    dsn = os.path.join(out, name + ".dsn")
    ses = os.path.join(out, name + ".ses")

    board = pn.LoadBoard(pcb)
    stray = _parts_off_board(board)
    if stray:
        ui.always("Not routing: %s %s outside the board outline, so %s connections can never be routed.\n"
              "Fix the placement first (enlarge the board, relax spacing, or set positions), "
              "rebuild with `kipcb build`, then route." % (
                  ", ".join(stray), "is" if len(stray) == 1 else "are", "its" if len(stray) == 1 else "their"))
        return 3
    _strip_routing(board)
    fan = fanout(board)
    if any(fan):
        ui.say("fan-out: %d escape stubs, %d same-net bridges on fine-pitch chips" % fan)
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
    from . import learn
    features = learn.board_features(board)
    narrow_ok = power is not None and signal_w < (base_w or 0)
    arms, _ = learn.rank_arms(features, available_narrow=narrow_ok)
    arms = arms[:max(1, attempts_max)]
    ui.say("routing plan (learned from %d past attempts): %s" % (
        len(learn.history("route_attempt")), " -> ".join("[%s]" % learn.arm_label(a) for a in arms)))
    plan = [((signal_w if narrow else base_w), bcost) for narrow, bcost in arms]
    # Freerouting is randomized and bimodal: on most boards a run either finishes completely
    # within seconds or wanders for minutes and still leaves connections. So: many short
    # attempts, stop at the first complete one, and lengthen the cap only if short ones fail.
    batch = parallel_attempts()
    cap0 = int(os.environ.get("KIPCB_ATTEMPT_SECONDS", "20"))
    best = None
    attempts = []
    kept = None
    t_route = time.time()
    n = 0
    done = False
    round_best = None
    while not done:
        if n and n % len(plan) == 0:          # a full round of strategies finished
            now = best[0][0] if best else None
            if round_best is not None and now is not None and now >= round_best:
                ui.say("  no improvement over the last round; stopping here")
                break
            round_best = now
        left = timeout - (time.time() - t_route)
        cap = cap0 * (1 if n < 2 * len(plan) else 2 if n < 3 * len(plan) else 4)
        if left < 10 or n >= 6 * len(plan):
            break
        cap = int(min(cap, left))
        group = list(range(n, n + batch))
        n += batch
        procs = []
        for k in group:                       # export each strategy's input, then start it
            i = k % len(plan)
            width, bcost = plan[i]
            set_power_width(width)
            _reset_to_fanout(board)
            dsn_i = os.path.join(out, "%s.attempt%d.dsn" % (name, k + 1))
            ses_i = dsn_i[:-4] + ".ses"
            cmd, log_i = _prepare(pn, board, jar, dsn_i, ses_i, passes, cap, bcost,
                                  protect=bool(getattr(board, "_kipcb_fanout", None)))
            procs.append((k, i, _launch(cmd, log_i, out), log_i, ses_i, time.time()))
        for k, i, proc, log_i, ses_i, t0 in procs:
            if done:
                proc.kill()
                proc.wait()
                proc._kipcb_log.close()
                continue
            try:
                _collect(proc, cap + 20, log_i, ses_i)
            except RuntimeError:
                continue                      # killed before it wrote a result: try the next one
            set_power_width(plan[i][0])
            _reset_to_fanout(board)          # the router's result doesn't carry the locked stubs
            if not pn.ImportSpecctraSES(board, ses_i):
                raise RuntimeError("SES import failed")
            _dedupe_fanout(board)
            heal_dangling(board)
            board.BuildConnectivity()
            missing = board.GetConnectivity().GetUnconnectedCount(True)
            bottom = bottom_signal_length(board)
            secs = round(time.time() - t0)
            ui.say("  attempt %d (%s, cap %ds): %d unconnected, %.0f mm on the bottom layer, %ds" % (
                k + 1, learn.arm_label(arms[i]), cap, missing, bottom, secs))
            learn.record("route_attempt", features=features, arm=list(arms[i]), missing=missing,
                         bottom_mm=round(bottom, 1), seconds=secs)
            attempts.append({"strategy": learn.arm_label(arms[i]), "unconnected": missing,
                             "bottom_mm": round(bottom, 1), "seconds": secs})
            kept = ses_i
            if best is None or (missing, bottom) < best[0]:
                best = ((missing, bottom), ses_i, plan[i][0], arms[i])
            if missing == 0:
                done = True
    if best is None:
        raise RuntimeError("no routing attempt produced a result (logs in %s)" % out)

    if best[1] != kept or best[0][0] != 0:
        _reset_to_fanout(board)
        set_power_width(best[2])
        pn.ImportSpecctraSES(board, best[1])
        _dedupe_fanout(board)
    healed = heal_dangling(board)
    board.BuildConnectivity()
    if board.GetConnectivity().GetUnconnectedCount(True):
        # completion pass: the DSN export carries the existing tracks, so the
        # router keeps them and only has to finish the few missing connections
        ui.say("completion pass for %d missing connection(s)..." % board.GetConnectivity().GetUnconnectedCount(True))
        try:
            _freeroute(pn, board, jar, dsn, ses, out, passes, max(60, timeout // 5))
            completed = True
        except RuntimeError:
            completed = False                 # no result in time: keep the best attempt as it is
        if completed:
            _reset_to_fanout(board)
            pn.ImportSpecctraSES(board, ses)
            _dedupe_fanout(board)
        healed += heal_dangling(board)
        board.BuildConnectivity()
        ui.say("after completion: %d unconnected" % board.GetConnectivity().GetUnconnectedCount(True))
    set_power_width(base_w)   # tracks keep their routed width; the project keeps the intended class
    for f in glob.glob(os.path.join(out, "%s.attempt*" % name)):
        if f != best[1]:
            os.remove(f)
    ntracks = sum(1 for t in board.GetTracks() if t.GetClass() == "PCB_TRACK")
    nvias = sum(1 for t in board.GetTracks() if t.GetClass() == "PCB_VIA")
    ui.say("using best attempt: %d unconnected, %d track segments, %d vias%s" % (
        best[0][0], ntracks, nvias, (", %d bridged" % healed) if healed else ""))

    if pour:
        spec_pour = _spec_pour_net(pdir, name, board)
        gnd = spec_pour if spec_pour is not None else _guess_ground(board)
        if gnd:
            add_pours(board, gnd, 0.3)
            n = add_stitching_vias(board, gnd)
            ui.say("poured %s on top and bottom, %d stitching vias" % (gnd, n))
    pn.SaveBoard(pcb, board)
    rc = checks.drc(pdir, name)
    from . import noise
    ui.say()
    noise.run(pdir, name, board=board, quiet=True)
    _record_final(pdir, name, board, features, best[3])
    board.BuildConnectivity()
    _write_route_json(pdir, {
        "seconds": round(time.time() - t_route), "parallel": batch, "attempts": attempts,
        "chosen": learn.arm_label(best[3]), "unconnected": board.GetConnectivity().GetUnconnectedCount(True),
        "tracks": ntracks, "vias": nvias, "bottom_mm": round(bottom_signal_length(board), 1),
        "drc_ok": rc == 0})
    for p in rendermod.render(pdir, name, "review"):
        if p.endswith(".png"):
            ui.say("preview:   %s" % os.path.relpath(p, pdir))
    if rc == 0:
        ui.say("\nDRC clean. Next: `kipcb fab %s`" % pdir)
    else:
        ui.say("\nFix remaining DRC issues (adjust placement/rules and re-run build + route, "
              "or finish by hand in KiCad).")
    return rc


def _write_route_json(pdir, data):
    from . import paths
    with open(os.path.join(paths.reports(pdir), "route.json"), "w") as f:
        json.dump(data, f, indent=1)


def _parts_off_board(board):
    """References of footprints whose pads fall outside the board outline."""
    bb = board.GetBoardEdgesBoundingBox()
    out = []
    for fp in board.GetFootprints():
        pads = list(fp.Pads())
        if pads and not all(bb.Contains(p.GetPosition()) for p in pads):
            out.append(fp.GetReference())
    return sorted(out)


def _record_final(pdir, name, board, features, arm):
    """Log the outcome so later runs learn from it (strategy, sizing, hard footprints)."""
    from . import learn
    from . import paths
    out = paths.reports(pdir)
    try:
        with open(os.path.join(out, "drc.json")) as f:
            drc = json.load(f)
    except (OSError, ValueError):
        drc = {}
    unconnected = drc.get("unconnected_items", [])
    errors = [v for v in drc.get("violations", []) if v.get("severity") == "error"]
    fp_of = {fp.GetReference(): fp.GetFPIDAsString() for fp in board.GetFootprints()}
    hard = set()
    for item in unconnected:
        for it in item.get("items", []):
            m = re.search(r" of (\w+)", it.get("description", ""))
            if m and m.group(1) in fp_of:
                hard.add(fp_of[m.group(1)])
    try:
        with open(os.path.join(out, "noise.json")) as f:
            nz = json.load(f)
    except (OSError, ValueError):
        nz = []
    spec = _spec(pdir, name)
    b = spec.get("board", {})
    learn.record("route_final", features=features, arm=list(arm), unconnected=len(unconnected),
                 drc_errors=len(errors), hard_footprints=sorted(hard),
                 noise_fail=sum(1 for i in nz if i["level"] == "FAIL"),
                 noise_warn=sum(1 for i in nz if i["level"] == "WARN"),
                 auto_size=not (b.get("width") and b.get("height")))


def _spec(pdir, name):
    from . import paths
    return paths.project_spec(pdir, name)


def _guess_ground(board):
    names = [str(n) for n in board.GetNetsByName().keys()]
    for cand in ("GND", "/GND", "GNDD", "DGND", "VSS"):
        if cand in names:
            return cand
    return None


def _spec_pour_net(pdir, name, board):
    """Honour board.ground_pour from the project's spec."""
    b = _spec(pdir, name).get("board", {})
    if b:
        if "ground_pour" not in b:
            return None
        gp = b["ground_pour"]
        if not gp:
            return ""
        names = [str(n) for n in board.GetNetsByName().keys()]
        return gp if gp in names else ("/" + gp if "/" + gp in names else None)
    return None


def _class_names(ns):
    """Names of all non-default netclasses (Power, Ground, Fine, the spec's own...)."""
    try:
        return [str(k) for k in ns.GetNetclasses().keys()]
    except Exception:
        return [n for n in ("Power", "Ground", "Fine", "PowerFine") if ns.HasNetclass(n)]


def _inset_boundary(dsn, inset_um):
    """Freerouting keeps only the normal clearance to the board outline, so copper ends up
    closer to the edge than the edge-clearance rule (DRC errors). Shrink the outline it sees by
    the difference. Only for convex outlines (kipcb's rounded rectangles)."""
    import math
    import re
    if inset_um <= 0:
        return
    with open(dsn) as f:
        text = f.read()
    m = re.search(r"\(boundary\s*\(path pcb 0\s+([-\d.\s]+)\)", text)
    if not m:
        return
    nums = [float(x) for x in m.group(1).split()]
    pts = list(zip(nums[0::2], nums[1::2]))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2
    sign = 1 if area > 0 else -1                 # counter-clockwise: inward normal is on the left
    crosses = []
    for i in range(n):
        (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[(i + 1) % n]
        crosses.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    if any(c * sign < -1e-6 for c in crosses):
        return                                   # not convex: leave it alone
    out = []
    for i in range(n):
        (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[(i + 1) % n]
        def normal(px, py, qx, qy):
            l = math.hypot(qx - px, qy - py) or 1.0
            return (-(qy - py) / l * sign, (qx - px) / l * sign)
        n1, n2 = normal(ax, ay, bx, by), normal(bx, by, cx, cy)
        k = 1.0 + n1[0] * n2[0] + n1[1] * n2[1]
        if k < 1e-6:
            return
        out.append((bx + inset_um * (n1[0] + n2[0]) / k, by + inset_um * (n1[1] + n2[1]) / k))
    out.append(out[0])
    path = "  ".join("%.1f %.1f" % p for p in out)
    text = text[:m.start(1)] + path + text[m.end(1):]
    with open(dsn, "w") as f:
        f.write(text)

def fanout(board, stub=0.6, max_pitch=0.52, min_pads=24):
    """Escape routing for fine-pitch chips, done before autorouting (as a person would):
    a straight bridge between neighbouring pins on the same net, and a short locked stub
    from every other used pin straight out past the pad row, so the router only has to
    reach easy stub ends instead of threading between 0.4 mm-pitch pins.
    Returns (stubs, bridges)."""
    pn = pcbnew()
    ns = board.GetDesignSettings().m_NetSettings
    all_pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
    net_count = {}
    for p in all_pads:
        net_count[p.GetNetCode()] = net_count.get(p.GetNetCode(), 0) + 1
    stubs = bridges = 0
    added = []
    board._kipcb_fanout = []

    def width_of(pad):
        nc = ns.GetEffectiveNetClass(pad.GetNetname())
        return nc.GetTrackWidth(), nc.GetClearance()

    def clear(a, b, net, width, clr):
        steps = max(2, int(((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5 / mm(0.05)))
        reach = int(clr + width / 2)
        for i in range(steps + 1):
            q = pn.VECTOR2I(int(a.x + (b.x - a.x) * i / steps), int(a.y + (b.y - a.y) * i / steps))
            for p in all_pads:
                if p.GetNetCode() != net and p.IsOnLayer(pn.F_Cu) and p.HitTest(q, reach):
                    return False
            for t in added:
                if t.GetNetCode() != net and t.HitTest(q, reach):
                    return False
        return True

    def add(a, b, pad, width):
        t = pn.PCB_TRACK(board)
        t.SetStart(a)
        t.SetEnd(b)
        t.SetWidth(width)
        t.SetLayer(pn.F_Cu)
        t.SetNet(pad.GetNet())
        t.SetLocked(True)
        board.Add(t)
        added.append(t)
        board._kipcb_fanout.append((a, b, width, pad.GetNet()))

    for fp in board.GetFootprints():
        pads = [p for p in fp.Pads() if p.GetNumber() and p.IsOnLayer(pn.F_Cu) and p.GetAttribute() == pn.PAD_ATTRIB_SMD]
        if len(pads) < min_pads or fp.GetReference().rstrip("0123456789") not in ("U", "IC"):
            continue
        c = fp.GetPosition()
        rows = []
        for p in pads:
            pos, bb = p.GetPosition(), p.GetBoundingBox()
            dx, dy = pos.x - c.x, pos.y - c.y
            if abs(dx) < mm(0.3) and abs(dy) < mm(0.3):
                continue                                    # exposed pad: connects by the pour/vias
            if abs(dx) >= abs(dy):
                out, length, across = (1 if dx > 0 else -1, 0), bb.GetWidth(), bb.GetHeight()
            else:
                out, length, across = (0, 1 if dy > 0 else -1), bb.GetHeight(), bb.GetWidth()
            rows.append((p, out, length, across))
        # pitch of this chip
        coords = sorted(((r[1], (r[0].GetPosition().y if r[1][0] else r[0].GetPosition().x)) for r in rows))
        gaps = [abs(b[1] - a[1]) for a, b in zip(coords, coords[1:]) if a[0] == b[0] and b[1] != a[1]]
        if not gaps or min(gaps) > mm(max_pitch):
            continue
        pitch = min(gaps)
        bridged = set()
        # 1. same-net neighbours in a row: bridge them across the gap
        for i, (p, out, length, across) in enumerate(rows):
            net = p.GetNetname()
            if not net or net.startswith("unconnected-"):
                continue
            for q, out2, _, across2 in rows[i + 1:]:
                if out2 != out or q.GetNetname() != net:
                    continue
                d = ((q.GetPosition().x - p.GetPosition().x) ** 2 + (q.GetPosition().y - p.GetPosition().y) ** 2) ** 0.5
                if d > pitch * 1.1:
                    continue
                w = min(width_of(p)[0], across, across2)
                add(p.GetPosition(), q.GetPosition(), p, w)
                bridges += 1
                bridged.add(q.GetNumber())
        # 2. a stub straight out from every used pin (one per bridged group is enough)
        for p, out, length, across in rows:
            net = p.GetNetname()
            if not net or net.startswith("unconnected-") or p.GetNumber() in bridged:
                continue
            if net_count.get(p.GetNetCode(), 0) < 2:
                continue
            w, clr = width_of(p)
            w = min(w, across)
            pos = p.GetPosition()
            end = pn.VECTOR2I(int(pos.x + out[0] * (length / 2 + mm(stub))), int(pos.y + out[1] * (length / 2 + mm(stub))))
            edge = pn.VECTOR2I(int(pos.x + out[0] * length / 2), int(pos.y + out[1] * length / 2))
            if clear(edge, end, p.GetNetCode(), w, clr):
                add(pos, end, p, w)
                stubs += 1
    return stubs, bridges


def _reset_to_fanout(board):
    """Remove all tracks and vias, then put the fan-out stubs back (each routing attempt
    starts from the same escape stubs, not from the previous attempt's routing)."""
    pn = pcbnew()
    for t in list(board.GetTracks()):
        _delete(board, t)
    for a, b, width, net in getattr(board, "_kipcb_fanout", []):
        t = pn.PCB_TRACK(board)
        t.SetStart(a)
        t.SetEnd(b)
        t.SetWidth(width)
        t.SetLayer(pn.F_Cu)
        t.SetNet(net)
        t.SetLocked(True)
        board.Add(t)


def _dedupe_fanout(board):
    """The router hands protected stubs back as ordinary wires: drop those copies."""
    keys = {(min((a.x, a.y), (b.x, b.y)), max((a.x, a.y), (b.x, b.y)))
            for a, b, _, _ in getattr(board, "_kipcb_fanout", [])}
    if not keys:
        return
    tol = mm(0.002)
    for t in list(board.GetTracks()):
        if t.IsLocked() or t.GetClass() != "PCB_TRACK":
            continue
        a, b = t.GetStart(), t.GetEnd()
        k = (min((a.x, a.y), (b.x, b.y)), max((a.x, a.y), (b.x, b.y)))
        if any(abs(k[0][0] - q[0][0]) <= tol and abs(k[0][1] - q[0][1]) <= tol and
               abs(k[1][0] - q[1][0]) <= tol and abs(k[1][1] - q[1][1]) <= tol for q in keys):
            _delete(board, t)
