"""How much board a design needs, learned from real routed boards (nearest neighbours).

The knowledge base holds, for every fully routed open-source board, what it had to fit
(pads, parts, connections, courtyard area, share of fine-pitch pads) and the board area
its designer gave it. For a new design, the most similar real boards on the same layer
count say how full boards like it get: their courtyard fill (courtyard area / board area).
Measured with kipcb.placestats.design_features on both sides.

Leave-one-repository-out on ~500 real boards, the prediction is typically within 1.35x
of the board a person chose, against 1.8x for a single density figure per layer count.

    features(design)        -> design features from the spec (before placement)
    fill(features, layers)  -> {"p25", "p50", "p75", "p90", "n"} courtyard fill of similar boards
"""

import math

from . import placestats

K = 30            # neighbours (15-60 all within 2% leave-one-out; 30 best)
MIN_ROWS = 50     # below this the knowledge base is too thin to say anything


def features(design):
    parts = []
    for c in design.components:
        x0, y0, x1, y1 = c.fp.courtyard
        parts.append((x1 - x0, y1 - y0, [(p["x"], p["y"], design.pin_net.get((c.ref, p["number"])))
                                         for p in c.fp.pads]))
    return placestats.design_features(parts)


def _vector(f):
    pads, parts = max(f["pads"], 1), max(f["parts"], 1)
    return (math.log(pads), math.log(pads / float(parts)), f["fine"],
            math.log(max(f["conns"], 1) / max(f["court_area"], 1.0) * 100.0))


def _rows(layers):
    from . import knowledge
    kb = knowledge.placement() or {}
    names, rows = kb.get("sizing_fields"), kb.get("sizing") or []
    if not names or len(rows) < MIN_ROWS:
        return []
    want = 4 if layers >= 4 else 2
    out = []
    for r in rows:
        d = dict(zip(names, r))
        if (4 if d["layers"] >= 4 else 2) == want and d["board_area"] > 0 and d["court_area"] > 0:
            out.append(d)
    return out


def _quantile(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def neighbours(f, layers, k=K, rows=None):
    rows = _rows(layers) if rows is None else rows
    if len(rows) < k:
        return []
    vs = [_vector(r) for r in rows]
    sd = [max(1e-6, _std([v[i] for v in vs])) for i in range(4)]
    x = _vector(f)
    scored = sorted((sum(((x[i] - v[i]) / sd[i]) ** 2 for i in range(4)), j) for j, v in enumerate(vs))
    return [rows[j] for _, j in scored[:k]]


def _std(xs):
    m = sum(xs) / float(len(xs))
    return math.sqrt(sum((v - m) ** 2 for v in xs) / float(len(xs)))


def fill(f, layers, rows=None):
    """Courtyard fill (courtyard area / board area) of the most similar real boards, or None."""
    nb = neighbours(f, layers, rows=rows)
    if not nb:
        return None
    fills = [r["court_area"] / r["board_area"] for r in nb]
    out = {"p%d" % int(q * 100): round(_quantile(fills, q), 3) for q in (0.25, 0.5, 0.75, 0.9)}
    out["n"] = len(nb)
    return out
