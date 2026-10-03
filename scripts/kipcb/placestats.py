"""Placement metrics of a board, shared by kipcb (layout score) and pcb-knowledge (mining
real boards), so generated layouts are measured exactly the way real ones were.

    metrics(board) -> {"fill", "peak_ratio", "rc_orientation_share", "connector_edge_mm",
                       "decoupling_mm", "parts", "layers", ...}
    pairs(board)   -> {"crystal_ic": [mm], "crystal_cap", "esd_connector", "inductor_sw", "bootstrap"}
    score(metrics, knowledge) -> (0-100, [per-metric notes])
"""

import math
import re

PASSIVE = ("R", "C", "L", "FB")
CONNECTOR = ("J", "P", "CN", "USB", "CON")
IC = ("U", "IC")
MAX_SAMPLES = 60


def prefix(ref):
    m = re.match(r"^[A-Za-z]+", ref or "")
    return m.group(0).upper() if m else ""


def _mm(v):
    return v / 1e6


def _box(fp):
    b = fp.GetBoundingBox(False, False)     # body + pads, no text
    return (_mm(b.GetX()), _mm(b.GetY()), _mm(b.GetRight()), _mm(b.GetBottom()))


def gap(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _pad_gap(p, q):
    a, b = p.GetBoundingBox(), q.GetBoundingBox()
    return gap((_mm(a.GetX()), _mm(a.GetY()), _mm(a.GetRight()), _mm(a.GetBottom())),
               (_mm(b.GetX()), _mm(b.GetY()), _mm(b.GetRight()), _mm(b.GetBottom())))


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def metrics(board):
    """Placement metrics of a loaded pcbnew board, or None for boards too small to say much."""
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0, w, h = _mm(bb.GetX()), _mm(bb.GetY()), _mm(bb.GetWidth()), _mm(bb.GetHeight())
    fps = [f for f in board.GetFootprints() if f.GetReference() and not f.GetReference().startswith("#")
           and "MountingHole" not in f.GetFPIDAsString()]
    if len(fps) < 5 or w <= 5 or h <= 5:
        return None
    boxes = {f.GetReference(): _box(f) for f in fps}
    fps = [f for f in fps if x0 - 1 <= boxes[f.GetReference()][0] and boxes[f.GetReference()][2] <= x0 + w + 1
           and y0 - 1 <= boxes[f.GetReference()][1] and boxes[f.GetReference()][3] <= y0 + h + 1]
    if len(fps) < 5:
        return None
    area = w * h
    part_area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in (boxes[f.GetReference()] for f in fps))
    fill = part_area / area

    # densest window (3x3 cells of an 8x8 grid) vs the whole board
    n = 8
    cells = [[0.0] * n for _ in range(n)]
    for f in fps:
        b = boxes[f.GetReference()]
        for i in range(n):
            for j in range(n):
                cx0, cy0 = x0 + w * i / n, y0 + h * j / n
                cells[i][j] += max(0.0, min(b[2], cx0 + w / n) - max(b[0], cx0)) * \
                    max(0.0, min(b[3], cy0 + h / n) - max(b[1], cy0))
    cell = area / (n * n)
    peak = max(sum(cells[i + a][j + c] for a in range(3) for c in range(3)) / (9 * cell)
               for i in range(n - 2) for j in range(n - 2))

    passives = [f for f in fps if prefix(f.GetReference()) in PASSIVE and len(list(f.Pads())) <= 3]
    nn = []
    for f in passives[:MAX_SAMPLES]:
        b = boxes[f.GetReference()]
        d = min((gap(b, boxes[g.GetReference()]) for g in fps if g is not f), default=None)
        if d is not None:
            nn.append(round(d, 2))

    orient = None
    rc = [f for f in passives if prefix(f.GetReference()) in ("R", "C")]
    if len(rc) >= 4:
        angles = [int(round(f.GetOrientationDegrees())) % 180 for f in rc]
        orient = round(max(angles.count(a) for a in set(angles)) / float(len(angles)), 3)

    conn_edge = []
    for f in fps:
        if prefix(f.GetReference()) in CONNECTOR and len(list(f.Pads())) >= 2:
            b = boxes[f.GetReference()]
            conn_edge.append(round(max(0.0, min(b[0] - x0, b[1] - y0, x0 + w - b[2], y0 + h - b[3])), 2))

    pads_by_net = {}
    for f in fps:
        for p in f.Pads():
            if p.GetNetname():
                pads_by_net.setdefault(p.GetNetname(), []).append((f, p))
    gnds = [k for k in pads_by_net if re.match(r"^/?(GND|GNDD|DGND|VSS)$", k, re.I)]
    gnd = gnds[0] if gnds else None
    cap_nets = {f.GetReference(): {p.GetNetname() for p in f.Pads()} for f in fps if prefix(f.GetReference()) == "C"}
    decoup = []
    for f in fps:
        if prefix(f.GetReference()) not in IC:
            continue
        for p in f.Pads():
            net = p.GetNetname()
            if not net or net == gnd or p.GetPinType() != "power_in":
                continue
            best = None
            for g, q in pads_by_net.get(net, []):
                if prefix(g.GetReference()) == "C" and gnd in cap_nets.get(g.GetReference(), ()):
                    d = _pad_gap(p, q)
                    best = d if best is None else min(best, d)
            if best is not None:
                decoup.append(round(best, 2))

    return {
        "layers": board.GetCopperLayerCount(), "w": round(w, 2), "h": round(h, 2), "parts": len(fps),
        "fill": round(fill, 3), "density_peak": round(peak, 3),
        "peak_ratio": round(peak / max(fill, 1e-6), 2),
        "passive_nn_gap": nn, "rc_orientation_share": orient,
        "connector_edge": conn_edge[:MAX_SAMPLES], "decoupling": decoup[:MAX_SAMPLES],
    }


# ------------------------------------------------------------------ "must be close" pairs

ESD = re.compile(r"ESD|USBLC|TPD\d|PRTR|SRV05|IP42\d\d|SP0503", re.I)
SW_PINS = {"SW", "LX", "PH", "SW1", "SW2"}
USB_DATA = re.compile(r"^(D[+-]|DP|DN|DM|USB_?D[PNM+-]|UD[+-])$", re.I)      # as layout.USB_DATA
BST_PINS = {"BST", "BOOT", "CB", "BS", "BOOST"}


def pairs(board):
    """Pad-to-pad distances (mm) of the part pairs layout guides say belong together, as
    real designers placed them: crystal to the IC it clocks, crystal to its load caps,
    USB ESD array to the connector, inductor to the regulator's SW pin, bootstrap cap
    to the BST pin. Same definitions as kipcb's layout rules."""
    fps = [f for f in board.GetFootprints() if f.GetReference() and not f.GetReference().startswith("#")]
    by_net = {}
    for f in fps:
        for p in f.Pads():
            if p.GetNetname():
                by_net.setdefault(p.GetNetname(), []).append((f, p))
    gnd = re.compile(r"^/?(GND|GNDD|DGND|AGND|VSS|PGND)$", re.I)

    def closest(f, kinds, pin_names=None):
        """min pad gap from f's (non-ground) pads to pads of parts whose prefix is in kinds
        (and, if given, whose pin name is in / accepted by pin_names)."""
        best = None
        for p in f.Pads():
            net = p.GetNetname()
            if not net or gnd.match(net):
                continue
            for g, q in by_net.get(net, []):
                if g is f or prefix(g.GetReference()) not in kinds:
                    continue
                fn = q.GetPinFunction().upper()
                if pin_names is not None and not (pin_names(fn) if callable(pin_names) else fn in pin_names):
                    continue
                d = _pad_gap(p, q)
                best = d if best is None else min(best, d)
        return best

    out = {"crystal_ic": [], "crystal_cap": [], "esd_connector": [], "inductor_sw": [], "bootstrap": []}
    for f in fps:
        kind = prefix(f.GetReference())
        text = "%s %s" % (f.GetValue(), f.GetFPIDAsString())
        d = None
        if kind in ("Y", "X", "XTAL") and "Oscillator" not in text:
            d = closest(f, IC)
            if d is not None:
                out["crystal_ic"].append(round(d, 2))
            d = closest(f, ("C",))
            if d is not None:
                out["crystal_cap"].append(round(d, 2))
        elif kind in IC + ("D",) and ESD.search(text):
            d = closest(f, CONNECTOR, pin_names=lambda fn: bool(USB_DATA.match(fn)))
            if d is not None:
                out["esd_connector"].append(round(d, 2))
        elif kind == "L" and len(list(f.Pads())) == 2:
            d = closest(f, IC, pin_names=SW_PINS)
            if d is not None:
                out["inductor_sw"].append(round(d, 2))
        elif kind == "C" and len(list(f.Pads())) == 2:
            d = closest(f, IC, pin_names=BST_PINS)
            if d is not None:
                out["bootstrap"].append(round(d, 2))
    return {k: v[:MAX_SAMPLES] for k, v in out.items()}


# ------------------------------------------------------------------ the score

# metric -> (how to read it from metrics(), what's better, weight)
#   "low": smaller is better (score falls above the real boards' median)
#   "high": larger is better
#   "band": typical is best (score falls outside the middle half of real boards)
SCORED = {
    # absolute local density of the densest area: low-fill boards (kipcb leaves routing room)
    # aren't penalised for having empty space, only for genuinely packed spots
    "density_peak": (lambda m: m.get("density_peak"), "low", 1.0),
    "rc_orientation_share": (lambda m: m.get("rc_orientation_share"), "high", 0.7),
    "connector_edge_mm": (lambda m: _median(m.get("connector_edge") or []), "low", 1.0),
    "decoupling_mm": (lambda m: _median(m.get("decoupling") or []), "low", 1.2),
}
LABELS = {"density_peak": "crowding (densest area)", "rc_orientation_share": "R/C orientation alignment",
          "connector_edge_mm": "connectors at the edge (median mm in)", "decoupling_mm": "decoupling distance (median mm)"}


def _sub_score(v, d, better):
    """0-100 for a value against a real-board distribution d (p10..p90)."""
    p10, p25, p50, p75, p90 = (d[k] for k in ("p10", "p25", "p50", "p75", "p90"))

    def ramp(x, good, bad):          # 100 at `good`, 0 at `bad`, linear between
        if bad == good:
            return 100.0 if x == good else 0.0
        t = (x - good) / float(bad - good)
        return max(0.0, min(100.0, 100.0 * (1 - t)))
    if better == "low":
        return 100.0 if v <= p50 else ramp(v, p50, p90 + (p90 - p50))
    if better == "high":
        return 100.0 if v >= p50 else ramp(v, p50, p10 - (p50 - p10))
    if p25 <= v <= p75:
        return 100.0
    return ramp(v, p25, p10 - (p25 - p10)) if v < p25 else ramp(v, p75, p90 + (p90 - p75))


def score(m, kb_placement, layers=2):
    """(0-100, notes) - how typical this layout is of real routed boards with this many layers."""
    if not m or not kb_placement:
        return None, []
    ref = kb_placement.get(str(min(layers, 4))) or kb_placement.get("all") or {}
    total = wsum = 0.0
    notes = []
    for key, (get, better, weight) in SCORED.items():
        v = get(m)
        d = ref.get(key)
        if v is None or not d or d.get("n", 0) < 20:
            continue
        s = _sub_score(v, d, better)
        total += s * weight
        wsum += weight
        notes.append((s, key, v, d["p50"]))
    if not wsum:
        return None, []
    notes.sort()
    return round(total / wsum), ["%s: %s (real boards: %s)" % (LABELS[k], _fmt(v), _fmt(p50))
                                 for s, k, v, p50 in notes if s < 70]


def _fmt(v):
    return ("%.0f%%" % (100 * v)) if isinstance(v, float) and v <= 1.0 and v > 0 else ("%.1f" % v if isinstance(v, float) else str(v))
