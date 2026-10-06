"""`kipcb inspect`: compare any KiCad board with real routed open-source boards.

Works on boards drawn by hand as well as ones kipcb built: how full the board is
against the most similar real boards, the layout score (crowding, R/C orientation,
connectors at the edge, decoupling distance), and how close the parts that belong
together sit (crystal, ESD array, inductor, bootstrap capacitor) compared with real
designs. Everything is measured with kipcb.placestats, as for the knowledge base.
"""

import json
import os

from . import knowledge, placestats, sizemodel

PAIR_LABELS = {
    "crystal_ic": "crystal to the chip it clocks",
    "crystal_cap": "crystal to its load capacitors",
    "esd_connector": "USB ESD array to the connector's data pins",
    "inductor_sw": "inductor to the regulator's SW pin",
    "bootstrap": "bootstrap capacitor to the BST pin",
}


def _rank(v, xs):
    """Share (0-1) of xs below v."""
    return sum(1 for x in xs if x < v) / float(len(xs)) if xs else None


def inspect(board):
    """Findings for a loaded pcbnew board: {"summary", "findings": [(level, text)], ...}."""
    layers = board.GetCopperLayerCount()
    m = placestats.metrics(board)
    out = {"layers": layers, "findings": []}
    if m is None:
        out["summary"] = "too few parts on the board edge outline to compare (needs 5+ parts and a board outline)"
        return out
    add = out["findings"].append
    out.update({"width_mm": m["w"], "height_mm": m["h"], "parts": m["parts"]})
    routed = any(t.GetClass() == "PCB_TRACK" for t in board.GetTracks())
    out["routed"] = routed

    # how full, against the most similar real routed boards. Parts on both sides share the
    # board's area twice over, so a double-sided board is compared per side.
    d = m["design"]
    fps = [f for f in board.GetFootprints() if f.GetReference() and not f.GetReference().startswith("#")]
    back = sum(1 for f in fps if f.IsFlipped())
    sides = 2 if fps and back / float(len(fps)) >= 0.15 else 1
    out["parts_on_back"] = back
    if sides == 2:
        add(("info", "%d of %d parts are on the back: coverage below is per side" % (back, len(fps))))
    fill = d["court_area"] / max(m["w"] * m["h"] * sides, 1.0)
    nb = sizemodel.neighbours(d, layers)
    if nb:
        fills = sorted(r["court_area"] / r["board_area"] for r in nb)
        r = _rank(fill, fills)
        out["fill"] = {"value": round(fill, 3), "similar_p50": round(fills[len(fills) // 2], 3),
                       "fuller_than": round(r, 2)}
        text = "parts cover %.0f%% of the board; similar real boards: %.0f%% typical (fuller than %.0f%% of them)" % (
            100 * fill, 100 * fills[len(fills) // 2], 100 * r)
        if r >= 0.9 and sides == 1:
            add(("warn", text + ". Tight: expect hard routing, consider a larger board or 4 layers"))
        elif r >= 0.9:
            add(("info", text + ". Dense even for a two-sided board"))
        elif r <= 0.1:
            add(("info", text + ". Roomy: the board could likely be smaller"))
        else:
            add(("ok", text))

    # layout score
    # both sides stack up in plan view, so crowding isn't comparable on a two-sided board
    score, notes = placestats.score(dict(m, density_peak=None) if sides == 2 else m,
                                    knowledge.placement(), layers)
    out["layout_score"] = score
    if score is not None:
        add(("ok" if score >= 80 else "warn",
             "layout score %d/100 against real %d-layer boards%s" % (
                 score, layers, (": " + "; ".join(notes)) if notes else "")))

    # parts that belong together
    pairs = placestats.pairs(board)
    kb = knowledge.placement() or {}
    ref = (kb.get(str(min(layers, 4))) or kb.get("all") or {}).get("pairs_mm") or {}
    out["pairs"] = {}
    for key, vals in pairs.items():
        dist = ref.get(key)
        if not vals or not dist or dist.get("n", 0) < 20:
            continue
        worst = max(vals)
        out["pairs"][key] = {"worst_mm": worst, "real_p50": dist["p50"], "real_p90": dist["p90"]}
        text = "%s: up to %.1f mm (real boards: half within %.1f mm, 90%% within %.1f mm)" % (
            PAIR_LABELS[key], worst, dist["p50"], dist["p90"])
        add(("warn" if worst > dist["p90"] else "ok", text))

    # decoupling, crowding, connectors at the edge, in the board's own numbers
    if m.get("decoupling"):
        dec = sorted(m["decoupling"])
        add(("ok" if dec[-1] <= 5 else "warn",
             "decoupling caps: median %.1f mm, farthest %.1f mm from their IC power pins" % (
                 dec[len(dec) // 2], dec[-1])))
    far = [e for e in (m.get("connector_edge") or []) if e > 5]
    if far:
        add(("info", "%d connector(s) sit more than 5 mm from the board edge (fine for headers, "
                     "not for cables plugged from outside)" % len(far)))
    out["summary"] = "%.0f x %.0f mm, %d layers, %d parts, %s" % (
        m["w"], m["h"], layers, m["parts"], "routed" if routed else "not routed yet")
    return out


def run(pcb_path, as_json=False):
    from .kicad import pcbnew
    board = pcbnew().LoadBoard(pcb_path)
    res = inspect(board)
    if as_json:
        print(json.dumps(res, indent=1))
        return 0
    print("%s: %s" % (os.path.basename(pcb_path), res["summary"]))
    mark = {"ok": "  ok  ", "info": "  note", "warn": "  WARN"}
    for level, text in res["findings"]:
        print("%s %s" % (mark[level], text))
    if not knowledge.placement():
        print("  (no knowledge base found: install the plugin's data/knowledge.json to compare with real boards)")
    return 0
