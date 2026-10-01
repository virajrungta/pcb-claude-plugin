"""Basic noise / signal-integrity checks on a routed board.

These are rules of thumb, not a field solver. They catch the layout mistakes
that most often cause noisy, unreliable boards: distant decoupling caps, a
chopped-up ground plane, long or edge-hugging clock/switching traces,
sensitive analog traces running alongside noisy ones, loose crystal and
switching-regulator layouts, and mismatched differential pairs.

Net roles come from net and pin names, and can be overridden in the spec:
  "net_roles": {"SENSE_IN": "sensitive", "MOTOR_PWM": "noisy", "LEFT_ADC": "quiet"}
"""

import json
import math
import os
import re
from . import ui

from .kicad import mm, pcbnew

NOISY_NET = re.compile(r"(^|[_/])(SW\d*|LX\d*|PH(ASE)?\d*|CLK\w*|\w*CLK|SCK|SCLK|PWM\w*|\w*_PWM|XTAL\w*|XIN|XOUT|"
                       r"XI|XO|OSC\w*|MCLK|BCLK|LRCLK|WS|MOT\w*|USB_D[PN]|USB_D[+-]|D[+-])$", re.I)
NOISY_PIN = re.compile(r"^(SW\d*|LX\d*|PH|XTAL\w*|XIN|XOUT|XI|XO|OSC\w*|CLK\w*|\w*CLK)$", re.I)
SENSITIVE_NET = re.compile(r"(ADC|AIN|ANALOG|SENSE|VREF|AREF|MIC|AUDIO|NTC|THERM|FB\d*$|_IN\d*$|AN\d+$)", re.I)
SENSITIVE_PIN = re.compile(r"^(\+|-|IN[+-]|IN\d*[+-]?|FB|VREF|AREF|ADC\w*|AIN\w*|SENSE\w*)$", re.I)
DIFF = [(re.compile(r"^(.*?)(_?)DP$", re.I), "DN"), (re.compile(r"^(.*?)(_?)P$", re.I), "N"),
        (re.compile(r"^(.*?)\+$"), "-")]

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _n(net):
    return net.lstrip("/")


class Report(object):
    def __init__(self):
        self.items = []

    def add(self, check, level, msg):
        self.items.append({"check": check, "level": level, "message": msg})

    def count(self, level):
        return sum(1 for i in self.items if i["level"] == level)


def _dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y) / 1e6


def _pad_gap(p, q):
    """Copper edge-to-edge distance between two pads (mm): the trace that joins them."""
    a, b = p.GetBoundingBox(), q.GetBoundingBox()
    dx = max(0, max(a.GetLeft(), b.GetLeft()) - min(a.GetRight(), b.GetRight()))
    dy = max(0, max(a.GetTop(), b.GetTop()) - min(a.GetBottom(), b.GetBottom()))
    return math.hypot(dx, dy) / 1e6


def _spec(pdir, name):
    for cand in (os.path.join(os.path.dirname(pdir), name + ".json"), os.path.join(pdir, name + ".json")):
        if os.path.exists(cand):
            try:
                with open(cand) as f:
                    return json.load(f)
            except ValueError:
                return {}
    return {}


def check(pdir, name, board=None):
    pn = pcbnew()
    if board is None:
        board = pn.LoadBoard(os.path.join(pdir, name + ".kicad_pcb"))
    spec = _spec(pdir, name)
    rep = Report()

    pads = [(fp.GetReference(), pad) for fp in board.GetFootprints() for pad in fp.Pads()]
    tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    nets = sorted({pad.GetNetname() for _, pad in pads if pad.GetNetname()})

    # ---- ground net
    gnd = spec.get("board", {}).get("ground_pour") or "GND"
    gnd_names = [n for n in nets if _n(n) == gnd] or [n for n in nets if _n(n) in ("GND", "GNDD", "DGND", "VSS")]
    gnd = gnd_names[0] if gnd_names else None

    # ---- roles
    overrides = {k: v for k, v in (spec.get("net_roles") or {}).items()}
    roles = {}
    for n in nets:
        if n == gnd or n.startswith("unconnected-"):
            continue
        base = _n(n)
        if base in overrides:
            roles[n] = overrides[base]
            continue
        funcs = [pad.GetPinFunction() for _, pad in pads if pad.GetNetname() == n]
        if NOISY_NET.search(base) or any(NOISY_PIN.match(f or "") for f in funcs):
            roles[n] = "noisy"
        elif SENSITIVE_NET.search(base) or any(SENSITIVE_PIN.match(f or "") for f in funcs):
            roles[n] = "sensitive"
    power_nets = set(_n(x) for x in spec.get("power_nets", []))

    board_box = board.GetBoardEdgesBoundingBox()

    # ---- 1. decoupling distance per IC power pin
    caps = {}
    for ref, pad in pads:
        if ref.rstrip("0123456789") == "C" and pad.GetNetname():
            caps.setdefault(ref, []).append(pad)
    for ref, pad in pads:
        if ref.rstrip("0123456789") not in ("U", "IC") or pad.GetPinType() != "power_in":
            continue
        net = pad.GetNetname()
        if not net or net == gnd:
            continue
        best = None
        for cref, cpads in caps.items():
            nets_c = {p.GetNetname() for p in cpads}
            if net in nets_c and gnd in nets_c:
                for p in cpads:
                    if p.GetNetname() == net:
                        d = _pad_gap(pad, p)
                        if best is None or d < best[0]:
                            best = (d, cref)
        label = "%s.%s (%s, net %s)" % (ref, pad.GetNumber(), pad.GetPinFunction() or "power", _n(net))
        if best is None:
            rep.add("decoupling", FAIL, "%s has no decoupling capacitor to %s" % (label, _n(gnd or "GND")))
        elif best[0] > 5:
            rep.add("decoupling", FAIL, "%s: nearest decoupling cap %s is %.1f mm pad-to-pad (aim for < 2.5 mm)" % (label, best[1], best[0]))
        elif best[0] > 2.5:
            rep.add("decoupling", WARN, "%s: decoupling cap %s is %.1f mm pad-to-pad (aim for < 2.5 mm)" % (label, best[1], best[0]))
        else:
            rep.add("decoupling", PASS, "%s: %s %.1f mm pad-to-pad" % (label, best[1], best[0]))

    # ---- 5. crystals
    fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
    for ref, fp in sorted(fps.items()):
        if ref.rstrip("0123456789") not in ("Y", "X"):
            continue
        xnets = {p.GetNetname() for p in fp.Pads() if p.GetNetname() and p.GetNetname() != gnd}
        far = []
        for oref, pad in pads:
            if oref == ref or pad.GetNetname() not in xnets:
                continue
            d = min(_dist(pad.GetPosition(), p.GetPosition()) for p in fp.Pads() if p.GetNetname() == pad.GetNetname())
            if oref.rstrip("0123456789") in ("U", "IC") and d > 6:
                far.append("%s.%s %.1f mm" % (oref, pad.GetNumber(), d))
            if oref.rstrip("0123456789") == "C" and d > 4:
                far.append("load cap %s %.1f mm" % (oref, d))
        bb = fp.GetBoundingBox(False)
        intruders = sorted({_n(t.GetNetname()) for t in tracks
                            if t.GetNetname() not in xnets and t.GetNetname() != gnd and
                            (bb.Contains(t.GetStart()) or bb.Contains(t.GetEnd()))})
        msg = []
        if far:
            msg.append("too far: " + ", ".join(far))
        if intruders:
            msg.append("other signals routed under it: " + ", ".join(intruders))
        rep.add("crystal", WARN if msg else PASS, "%s %s" % (ref, "; ".join(msg) if msg else "compact, nothing routed underneath"))

    # ---- 6. switching regulator loop (inductor at the SW/LX pin)
    for ref, fp in sorted(fps.items()):
        if ref.rstrip("0123456789") not in ("L",):
            continue
        for lp in fp.Pads():
            for oref, pad in pads:
                if oref.rstrip("0123456789") in ("U", "IC") and pad.GetNetname() == lp.GetNetname() and \
                        NOISY_PIN.match(pad.GetPinFunction() or "") and pad.GetPinFunction().upper().startswith(("SW", "LX", "PH")):
                    d = _dist(lp.GetPosition(), pad.GetPosition())
                    rep.add("switcher loop", WARN if d > 4 else PASS,
                            "%s to %s.%s (%s): %.1f mm%s" % (ref, oref, pad.GetNumber(), pad.GetPinFunction(), d,
                                                              " -- keep the switch node short (< 4 mm)" if d > 4 else ""))

    routed = bool(tracks)
    if not routed:
        return rep   # the remaining checks need routed copper

    # ---- 2. ground plane integrity
    outline = pn.SHAPE_POLY_SET()
    board.GetBoardPolygonOutlines(outline)
    board_area = outline.Area() / 1e12 if outline.OutlineCount() else \
        board_box.GetWidth() * board_box.GetHeight() / 1e12
    zones = [board.GetArea(i) for i in range(board.GetAreaCount())]
    gzones = [z for z in zones if gnd and z.GetNetname() == gnd and not z.GetIsRuleArea()]
    if not gzones:
        rep.add("ground plane", WARN, "no %s copper pour: add one (board.ground_pour) for a low-impedance return path"
                % _n(gnd or "GND"))
    for z in gzones:
        for layer in z.GetLayerSet().CuStack():
            fill = z.GetFilledPolysList(layer)
            area = fill.Area() / 1e12 if fill else 0.0
            cover = area / board_area * 100 if board_area else 0
            islands = fill.OutlineCount() if fill else 0
            lname = board.GetLayerName(layer)
            bottom = layer == pn.B_Cu
            if bottom and cover < 60:
                lvl = FAIL
            elif (bottom and cover < 80) or cover < 50:
                lvl = WARN
            else:
                lvl = PASS
            rep.add("ground plane", lvl, "%s pour covers %.0f%% of the board%s" % (
                lname, cover, (", split into %d islands" % islands) if islands > 1 else ""))
    bsig = sum(t.GetLength() for t in tracks if t.GetLayer() == pn.B_Cu and t.GetNetname() != gnd) / 1e6
    if board.GetCopperLayerCount() == 2:
        lvl = PASS if bsig < 15 else WARN
        rep.add("ground plane", lvl, "%.0f mm of signal track on the bottom layer%s" % (
            bsig, "" if lvl == PASS else ": each cut forces return currents around it; move routes to the top or use 4 layers"))
    area_cm2 = board_area / 100.0
    gvias = [v for v in vias if v.GetNetname() == gnd]
    if gzones and len(zones) and area_cm2 > 0:
        dens = len(gvias) / area_cm2
        rep.add("stitching", PASS if dens >= 0.4 else WARN,
                "%d ground vias (%.1f per cm²)%s" % (len(gvias), dens, "" if dens >= 0.4 else ": stitch the top and bottom pours more densely"))

    # ---- helpers for tracks
    def net_tracks(n):
        return [t for t in tracks if t.GetNetname() == n]

    def edge_gap(t):
        g = []
        for p in (t.GetStart(), t.GetEnd()):
            g += [p.x - board_box.GetLeft(), board_box.GetRight() - p.x, p.y - board_box.GetTop(), board_box.GetBottom() - p.y]
        return min(g) / 1e6

    # ---- 3. noisy nets: length, edges, vias
    for n, role in sorted(roles.items()):
        if role != "noisy":
            continue
        ts = net_tracks(n)
        if not ts:
            continue
        length = sum(t.GetLength() for t in ts) / 1e6
        nv = sum(1 for v in vias if v.GetNetname() == n)
        near_edge = min(edge_gap(t) for t in ts)
        problems = []
        if length > 60:
            problems.append("long (%.0f mm)" % length)
        if near_edge < 1.0:
            problems.append("runs %.1f mm from the board edge (radiates)" % near_edge)
        if nv > 2:
            problems.append("%d vias" % nv)
        rep.add("noisy nets", WARN if problems else PASS, "%s: %.0f mm, %d vias%s" % (
            _n(n), length, nv, (" -- " + "; ".join(problems)) if problems else ""))

    # ---- 4. coupling between sensitive and noisy traces
    noisy = [t for t in tracks if roles.get(t.GetNetname()) == "noisy"]
    for n, role in sorted(roles.items()):
        if role != "sensitive":
            continue
        coupled = 0.0
        partner = set()
        for t in net_tracks(n):
            steps = max(1, int(t.GetLength() / mm(0.25)))
            s, e = t.GetStart(), t.GetEnd()
            for i in range(steps + 1):
                q = pn.VECTOR2I(int(s.x + (e.x - s.x) * i / steps), int(s.y + (e.y - s.y) * i / steps))
                for o in noisy:
                    if o.GetLayer() == t.GetLayer() and o.HitTest(q, int(mm(0.5) + t.GetWidth() / 2)):
                        coupled += t.GetLength() / 1e6 / (steps + 1)
                        partner.add(_n(o.GetNetname()))
                        break
        if coupled > 2.0:
            rep.add("crosstalk", WARN, "sensitive net %s runs %.1f mm within 0.5 mm of noisy %s: separate them or put ground between"
                    % (_n(n), coupled, ", ".join(sorted(partner))))
        elif net_tracks(n):
            rep.add("crosstalk", PASS, "sensitive net %s is clear of noisy traces" % _n(n))

    # ---- 7. differential pairs
    seen = set()
    for n in nets:
        base = _n(n)
        for rx, neg in DIFF:
            m = rx.match(base)
            if not m:
                continue
            partner_name = m.group(1) + (m.group(2) if m.lastindex and m.lastindex >= 2 else "") + neg
            partner = [x for x in nets if _n(x) == partner_name]
            if not partner or n in seen:
                continue
            p = partner[0]
            seen.update((n, p))
            la = sum(t.GetLength() for t in net_tracks(n)) / 1e6
            lb = sum(t.GetLength() for t in net_tracks(p)) / 1e6
            va = sum(1 for v in vias if v.GetNetname() == n)
            vb = sum(1 for v in vias if v.GetNetname() == p)
            mism = abs(la - lb)
            probs = []
            if mism > 2.0:
                probs.append("length mismatch %.1f mm" % mism)
            if va != vb:
                probs.append("via count differs (%d vs %d)" % (va, vb))
            rep.add("diff pair", WARN if probs else PASS, "%s/%s: %.1f / %.1f mm%s" % (
                base, partner_name, la, lb, (" -- " + "; ".join(probs)) if probs else ""))
            break

    # ---- 8. power track width
    for n in nets:
        if _n(n) not in power_nets or n == gnd:
            continue
        ts = net_tracks(n)
        if not ts:
            continue
        w = min(t.GetWidth() for t in ts) / 1e6
        rep.add("power width", PASS if w >= 0.4 else WARN, "%s narrowest track %.2f mm%s" % (
            _n(n), w, "" if w >= 0.4 else ": thin supply traces add resistance and noise; widen where current flows"))

    return rep


def run(pdir, name, board=None, quiet=False, save=True):
    rep = check(pdir, name, board)
    if save:
        from . import paths
        out = paths.reports(pdir)
        with open(os.path.join(out, "noise.json"), "w") as f:
            json.dump(rep.items, f, indent=1)
    fails, warns = rep.count(FAIL), rep.count(WARN)
    ui.say("NOISE CHECK: %d fail, %d warn, %d pass" % (fails, warns, rep.count(PASS)))
    order = {FAIL: 0, WARN: 1, PASS: 2}
    for it in sorted(rep.items, key=lambda i: (order[i["level"]], i["check"])):
        if quiet and it["level"] == PASS:
            continue
        ui.say("  %-4s %-13s %s" % (it["level"], it["check"], it["message"]))
    return 1 if fails else 0
