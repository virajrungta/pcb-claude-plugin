"""Preflight: catch everything that would make routing fail *before* routing.

Routing is the slow step (minutes). Each check here takes milliseconds on the
placed board and answers "can this possibly route cleanly?":

  classes     every net has exactly one netclass (KiCad merges several into one
              with the wrong width)
  pad reach   each net's track width can reach every pad it connects, given the
              pad pitch (a 0.25 mm track can't reach a 0.4 mm-pitch QFN pin)
  pad gaps    footprints whose own pads sit closer than the clearance rule
  fab limits  track, clearance, via and drill sizes the manufacturer can make
  edge        copper closer to the board edge than the edge-clearance rule
  placement   courtyard overlaps, parts off the board, decoupling caps / crystal
              load caps too far from their pins
  density     connections per cm² against what has routed before (your own
              boards and real open-source boards in the knowledge base)

FAIL stops the pipeline before routing; WARN is reported and routing goes on.
Results are saved to reports/preflight.json and learned from (kipcb learn).
"""

import json
import os

from . import paths, ui

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# Standard (no extra cost) capabilities, mm.
FAB_LIMITS = {
    "JLCPCB": {"track": 0.127, "clearance": 0.127, "via_drill": 0.3, "via_dia": 0.45, "edge": 0.3},
    "PCBWay": {"track": 0.1, "clearance": 0.1, "via_drill": 0.2, "via_dia": 0.4, "edge": 0.25},
    "OSH Park": {"track": 0.152, "clearance": 0.152, "via_drill": 0.254, "via_dia": 0.508, "edge": 0.381},
    "Other": {"track": 0.15, "clearance": 0.15, "via_drill": 0.3, "via_dia": 0.6, "edge": 0.3},
}
MARGIN = 0.01          # the router is asked for this much extra clearance (route._export_dsn)


class Result(object):
    def __init__(self):
        self.items = []

    def add(self, check, level, msg, fix=None):
        self.items.append({"check": check, "level": level, "message": msg, "fix": fix})

    def count(self, level):
        return sum(1 for i in self.items if i["level"] == level)


def _mm(v):
    return v / 1e6


def _classes(board):
    """{net name: (class name, track mm, clearance mm)} as the router will see them."""
    out = {}
    ns = board.GetDesignSettings().m_NetSettings
    for name in board.GetNetsByName().keys():
        name = str(name)
        if not name:
            continue
        nc = ns.GetEffectiveNetClass(name)
        out[name] = (str(nc.GetName()), _mm(nc.GetTrackWidth()), _mm(nc.GetClearance()))
    return out


def connections_per_cm2(design, w, h):
    conns = sum(len(p) - 1 for p in design.nets.values() if len(p) > 1)
    return round(conns / max(0.01, w * h / 100.0), 2), conns


def finest_pitch(design):
    ps = [c.fp.pitch() for c in design.components if c.fp is not None]
    ps = [p for p in ps if p]
    return min(ps) if ps else None


def check(pdir, name, design, board=None):
    from .kicad import pcbnew
    from . import learn, noise, knowledge
    pn = pcbnew()
    if board is None:
        board = pn.LoadBoard(os.path.join(pdir, name + ".kicad_pcb"))
    res = Result()
    classes = _classes(board)
    r = design.rules

    # ---- classes
    merged = sorted(n for n, (c, _, _) in classes.items() if "," in c)
    if merged:
        res.add("classes", FAIL, "nets with several netclasses (KiCad merges them, usually with the "
                "wrong width): %s" % ", ".join(merged[:8]),
                "give each net one class: remove it from \"power_nets\" or from extra \"net_classes\"")
    else:
        res.add("classes", PASS, "every net has one netclass")

    # ---- pad reach and pad gaps
    pcb_nets = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname():
                pcb_nets[(fp.GetReference(), pad.GetNumber())] = pad.GetNetname()
    ds = board.GetDesignSettings()
    board_min = _mm(ds.m_MinClearance)
    strict = sorted({"%s %.3f mm" % (c, k) for c, _, k in classes.values() if k < board_min - 1e-6})
    if strict:
        res.add("classes", FAIL, "board minimum clearance %.3f mm is stricter than class(es) %s, so their "
                "tracks would all be DRC errors" % (board_min, ", ".join(strict)),
                "rebuild with this version of kipcb (it sets the board minimum from the classes)")
    unreachable, tight = {}, []
    lim_cache = {}
    for c in design.components:
        if c.fp is None:
            continue
        for num in c.fp.pad_numbers():
            net = pcb_nets.get((c.ref, num))
            if not net or net not in classes or net.startswith("unconnected-"):
                continue                       # no-connect pins are never routed
            cls, width, clr = classes[net]
            if c.ref not in lim_cache:
                clr_of = {n: classes.get(pcb_nets.get((c.ref, n)), (None, 0, r["clearance"]))[2]
                          for n in c.fp.pad_numbers()}
                lim_cache[c.ref] = c.fp.track_limits(r["clearance"], MARGIN, clr_of)
            lim = lim_cache[c.ref].get(num)
            if lim is not None and width > lim + 1e-6:
                u = unreachable.setdefault((net.lstrip("/"), cls, width), [])
                u.append("%s.%s" % (c.ref, num))
        # pads closer to each other than the clearance (different nets): DRC errors no matter what
        gap = _min_pad_gap(c, pcb_nets)
        if gap is not None and gap < r["clearance"] - 1e-6:
            tight.append("%s (%s): %.3f mm between pads" % (c.ref, c.fp.fp_id.split(":")[-1], gap))
    if unreachable:
        by_cls = {}
        for (net, cls, width), pads in sorted(unreachable.items()):
            by_cls.setdefault((cls, width), []).append("%s (%s)" % (net, ", ".join(pads[:3])))
        for (cls, width), nets in sorted(by_cls.items()):
            res.add("pad reach", FAIL, "%.3f mm tracks (class %s) can't reach the pins of %d net(s), the pads "
                    "are too close together: %s" % (width, cls, len(nets), "; ".join(nets[:5])),
                    "lower that track width, or let kipcb choose (remove a custom \"rules\".\"track\")")
    else:
        res.add("pad reach", PASS, "every pad is reachable at its net's track width")
    if tight:
        res.add("pad gaps", FAIL, "footprint pads closer than the %.2f mm clearance rule: %s" % (
            r["clearance"], "; ".join(tight[:4])),
            "set \"rules\": {\"clearance\": <smaller>} (fab minimum allowing), or choose another footprint")

    # ---- fab limits
    from . import learn as _learn
    fab = _learn.settings().get("fab_house", "JLCPCB")
    lim = FAB_LIMITS.get(fab, FAB_LIMITS["Other"])
    bad = []
    for cname, track, clr in sorted({(c, t, k) for c, t, k in classes.values()}):
        if track < lim["track"] - 1e-6:
            bad.append("class %s track %.3f mm < %.3f" % (cname, track, lim["track"]))
        if clr < lim["clearance"] - 1e-6:
            bad.append("class %s clearance %.3f mm < %.3f" % (cname, clr, lim["clearance"]))
    if r["via_drill"] < lim["via_drill"] - 1e-6:
        bad.append("via drill %.2f mm < %.2f" % (r["via_drill"], lim["via_drill"]))
    if r["via_diameter"] < lim["via_dia"] - 1e-6:
        bad.append("via diameter %.2f mm < %.2f" % (r["via_diameter"], lim["via_dia"]))
    if r["edge_clearance"] < lim["edge"] - 1e-6:
        bad.append("edge clearance %.2f mm < %.2f" % (r["edge_clearance"], lim["edge"]))
    if bad:
        res.add("fab limits", FAIL, "%s can't make: %s" % (fab, "; ".join(bad)),
                "raise those \"rules\" to the manufacturer's minimum")
    else:
        res.add("fab limits", PASS, "tracks, gaps, vias and drills within %s's standard limits" % fab)

    # ---- edge
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0, x1, y1 = _mm(bb.GetX()), _mm(bb.GetY()), _mm(bb.GetRight()), _mm(bb.GetBottom())
    near = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if not any(pad.IsOnLayer(l) for l in (pn.F_Cu, pn.B_Cu)):
                continue
            pb = pad.GetBoundingBox()
            gap = min(_mm(pb.GetX()) - x0, _mm(pb.GetY()) - y0, x1 - _mm(pb.GetRight()), y1 - _mm(pb.GetBottom()))
            if gap < r["edge_clearance"] - 0.02 and pad.GetNetname():
                near.append("%s.%s %.2f mm" % (fp.GetReference(), pad.GetNumber(), max(gap, 0)))
    if near:
        res.add("edge", FAIL, "pads closer to the board edge than %.2f mm: %s" % (r["edge_clearance"], ", ".join(near[:6])),
                "move those parts inward (or enlarge the board)")
    else:
        res.add("edge", PASS, "all pads at least %.2f mm from the edge" % r["edge_clearance"])

    # ---- placement (reuses the build's DRC and the placement-stage noise checks)
    from . import report
    b = report._load(pdir, "build.json") or {}
    if b.get("placement_failed"):
        res.add("placement", FAIL, "parts off the board: %s" % ", ".join(b["placement_failed"]),
                "enlarge the board or relax spacing")
    overlaps = _courtyard_overlaps(pdir)
    if overlaps:
        res.add("placement", FAIL, "overlapping parts: %s" % ", ".join(overlaps[:6]),
                "give those parts room (place hints or a bigger board)")
    nrep = noise.check(pdir, name, board)
    for it in nrep.items:
        if it["check"] in ("decoupling", "crystal", "switcher loop") and it["level"] != PASS:
            res.add("placement", it["level"], it["message"],
                    "put it next to the pin: {\"place\": {\"near\": \"U1.<pin>\"}}, or spacing \"compact\"")
    if not any(i["check"] == "placement" for i in res.items):
        res.add("placement", PASS, "no overlaps; decoupling and crystal parts close to their pins")

    # ---- layout rules from application notes (USB ESD, crystals, buck loops, sensors, RF...)
    from . import layout
    findings = layout.check(design, board)
    for rule, level, msg, fix in findings:
        res.add("layout", level, "[%s] %s" % (rule, msg), fix)
    if not findings:
        res.add("layout", PASS, "USB, crystal, regulator, sensor and RF placement rules all met")

    # ---- crowding: most parts packed into a small corner of a roomy board
    crowd = _crowding(board)
    if crowd:
        res.add("crowding", WARN, crowd, "rebuild (kipcb spreads parts over spare room), or set a smaller "
                "board size; avoid \"spacing\": \"compact\" on roomy boards")
    else:
        res.add("crowding", PASS, "parts use the board evenly")

    # ---- escape room around fine-pitch chips
    blocked = _escape(board, design, pcb_nets)
    for line in blocked:
        res.add("escape", WARN, line, "spacing \"roomy\", fewer \"near\" hints on that side, or 4 layers")
    if not blocked:
        res.add("escape", PASS, "fine-pitch chips have room to fan their tracks out")

    # ---- density (learned)
    w, h = _mm(bb.GetWidth()), _mm(bb.GetHeight())
    layers = board.GetCopperLayerCount()
    dens, conns = connections_per_cm2(design, w, h)
    pitch = finest_pitch(design)
    pred = learn.routability(layers, dens)
    kb = knowledge.routed_density(layers)
    msg = "%.1f connections/cm² on %d layers" % (dens, layers)
    level = PASS
    if kb and dens > kb["p90"] * 1.5:
        level = WARN
        msg += "; denser than 90%% of real %d-layer boards that route (%.1f)" % (layers, kb["p90"])
    if pred and pred["n"] >= 3 and pred["p_ok"] < 0.34:
        level = WARN
        msg += "; only %d of %d similar past boards routed completely" % (pred["ok"], pred["n"])
    res.add("density", level, msg, "enlarge the board (\"auto_shrink\": false or a fixed size) or use 4 layers"
            if level != PASS else None)

    # ---- layout score: how typical this layout is of real routed boards (knowledge base)
    from . import placestats
    pm = placestats.metrics(board)
    lscore, lnotes = placestats.score(pm, knowledge.placement(), layers)
    if lscore is not None:
        res.add("layout score", PASS if lscore >= 60 else WARN,
                "%d/100 compared with real %d-layer boards%s" % (
                    lscore, layers, ("; weakest: " + "; ".join(lnotes[:2])) if lnotes else ""),
                None if lscore >= 60 else "see the weakest items; place hints or spacing usually fix them")

    features = {"layers": layers, "conn_density": dens, "connections": conns,
                "finest_pitch": round(pitch, 3) if pitch else None, "layout_score": lscore}
    learn.record("preflight", features=features, fails=sorted({i["check"] for i in res.items if i["level"] == FAIL}),
                 warns=sorted({i["check"] for i in res.items if i["level"] == WARN}))
    return res, features


def _escape(board, design, pcb_nets, ring=1.5):
    """Sides of fine-pitch chips where neighbours cover most of the strip the tracks must
    fan out through, while several pins on that side still need routing."""
    out = []
    boxes = {}
    for fp in board.GetFootprints():
        bb = fp.GetBoundingBox(False)
        boxes[fp.GetReference()] = (_mm(bb.GetX()), _mm(bb.GetY()), _mm(bb.GetRight()), _mm(bb.GetBottom()))
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        c = design.by_ref.get(ref)
        if c is None or c.fp is None or len(c.fp.pads) < 16 or (c.fp.pitch() or 9) > 0.65:
            continue
        x0, y0, x1, y1 = boxes[ref]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        strips = {"left": (x0 - ring, y0, x0, y1), "right": (x1, y0, x1 + ring, y1),
                  "top": (x0, y0 - ring, x1, y0), "bottom": (x0, y1, x1, y1 + ring)}
        pins = {k: 0 for k in strips}
        for pad in fp.Pads():
            net = pcb_nets.get((ref, pad.GetNumber()))
            if not net or net.startswith("unconnected-"):
                continue
            px, py = _mm(pad.GetPosition().x) - cx, _mm(pad.GetPosition().y) - cy
            if abs(px) < 0.3 and abs(py) < 0.3:
                continue                                  # exposed pad: connects through vias
            side = ("right" if px > 0 else "left") if abs(px) > abs(py) else ("bottom" if py > 0 else "top")
            pins[side] += 1
        for side, (sx0, sy0, sx1, sy1) in strips.items():
            area = (sx1 - sx0) * (sy1 - sy0)
            covered, who = 0.0, []
            for oref, (ox0, oy0, ox1, oy1) in boxes.items():
                if oref == ref:
                    continue
                ix = max(0.0, min(sx1, ox1) - max(sx0, ox0))
                iy = max(0.0, min(sy1, oy1) - max(sy0, oy0))
                if ix * iy > 0:
                    covered += ix * iy
                    who.append(oref)
            share = covered / area if area else 0
            if pins[side] >= 3 and share > 0.6:
                out.append("%s %s side: %d%% blocked by %s, %d pins must route out that way" % (
                    ref, side, min(100, round(100 * share)), ", ".join(sorted(who)[:6]), pins[side]))
    return out


def _crowding(board, n=8, win=3):
    """A small region packed far denser than the board as a whole: the densest window
    (3x3 cells of an 8x8 grid, about 14% of the board) vs. the overall fill. Returns a
    message, or None when the layout is reasonably even or the board is simply full."""
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0, w, h = _mm(bb.GetX()), _mm(bb.GetY()), _mm(bb.GetWidth()), _mm(bb.GetHeight())
    cells = [[0.0] * n for _ in range(n)]
    total = 0.0
    for fp in board.GetFootprints():
        b = fp.GetBoundingBox(False)
        bx0, by0, bx1, by1 = _mm(b.GetX()), _mm(b.GetY()), _mm(b.GetRight()), _mm(b.GetBottom())
        for i in range(n):
            for j in range(n):
                cx0, cy0 = x0 + w * i / n, y0 + h * j / n
                ix = max(0.0, min(bx1, cx0 + w / n) - max(bx0, cx0))
                iy = max(0.0, min(by1, cy0 + h / n) - max(by0, cy0))
                cells[i][j] += ix * iy
        total += (bx1 - bx0) * (by1 - by0)
    fill = total / (w * h)
    if total <= 0 or fill > 0.35:
        return None                          # a full board isn't "crowded in a corner"
    cell = w * h / (n * n)
    peak = max(sum(cells[i + a][j + b] for a in range(win) for b in range(win)) / (win * win * cell)
               for i in range(n - win + 1) for j in range(n - win + 1))
    if peak < 0.45 or peak < 2.5 * fill:
        return None
    return "one area is %d%% packed while the board overall is only %d%% used; crowded parts are " \
           "hard to route" % (round(100 * peak), round(100 * fill))


def _min_pad_gap(c, pcb_nets):
    """Smallest edge-to-edge gap between two pads of different nets in one footprint row."""
    best = None
    pads = [p for p in c.fp.pads if p["number"] and pcb_nets.get((c.ref, p["number"]))]
    for i, a in enumerate(pads):
        na = pcb_nets[(c.ref, a["number"])]
        for b in pads[i + 1:]:
            if pcb_nets[(c.ref, b["number"])] == na:
                continue
            dx, dy = abs(a["x"] - b["x"]), abs(a["y"] - b["y"])
            if dy < 0.01:
                g = dx - (a["w"] + b["w"]) / 2
            elif dx < 0.01:
                g = dy - (a["h"] + b["h"]) / 2
            else:
                continue
            if g >= 0 and (best is None or g < best):
                best = g
    return best


def _courtyard_overlaps(pdir):
    from . import report
    drc = report._load(pdir, "drc.json") or {}
    out = []
    for v in drc.get("violations", []):
        if v.get("type") == "courtyards_overlap" and v.get("severity") == "error":
            refs = [i["description"].split(" ")[1] for i in v.get("items", []) if i["description"].startswith("Footprint ")]
            out.append("/".join(refs) or "?")
    return out


def run(pdir, name, design, board=None, quiet=False):
    """Check, save reports/preflight.json, print problems. Returns the number of FAILs."""
    res, features = check(pdir, name, design, board)
    with open(os.path.join(paths.reports(pdir), "preflight.json"), "w") as f:
        json.dump({"items": res.items, "features": features}, f, indent=1)
    out = ui.always if not quiet else ui.say
    for it in res.items:
        if it["level"] != PASS:
            out("  %s %-10s %s" % (it["level"], it["check"], it["message"]))
            if it.get("fix"):
                out("       fix: %s" % it["fix"])
    return res.count(FAIL)
