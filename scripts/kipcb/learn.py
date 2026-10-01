"""Local learning: kipcb gets better at placement and routing the more it is used.

Every build and routing attempt is logged to an experience file on this
machine (never uploaded). The log is used for three things:

1. Routing strategy (a contextual bandit). Each strategy ("arm") is a
   combination of power-track width and how strongly signals are kept off
   the bottom layer. For a new board, arms are ranked by how they did on
   past boards of similar pad density, with an exploration bonus for arms
   that have little evidence. A cold start reproduces the hand-tuned default order.
2. Board auto-sizing. The smallest area per pad that has reliably routed on
   similar boards, seeded by statistics from open-source designs (knowledge.json).
3. Hard footprints. Footprints that keep appearing in unrouted connections
   get extra clearance in future placements.
"""

import json
import math
import os
import time

ARMS = [  # (narrow power tracks, bottom-layer cost or None)
    (False, 3.0), (True, 3.0), (False, None), (True, None), (False, 2.0), (True, 4.5),
]
# cold-start prior values: reproduces the hand-tuned order of the first four arms
PRIOR = {(False, 3.0): 0.60, (True, 3.0): 0.55, (False, None): 0.50, (True, None): 0.45,
         (False, 2.0): 0.40, (True, 4.5): 0.35}


def data_dir():
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    d = os.path.join(base, "kipcb")
    os.makedirs(d, exist_ok=True)
    return d


def log_path():
    return os.environ.get("KIPCB_EXPERIENCE") or os.path.join(data_dir(), "experience.jsonl")


def enabled():
    return os.environ.get("KIPCB_LEARN", "1") != "0"


def record(kind, **data):
    if not enabled():
        return
    data.update({"kind": kind, "t": round(time.time())})
    try:
        with open(log_path(), "a") as f:
            f.write(json.dumps(data) + "\n")
    except OSError:
        pass


def _load():
    try:
        with open(log_path()) as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []


def _mark_tainted(rows):
    """Flag routing records that came from a broken placement.

    If a build left a part off the board, every route that follows it (for
    the same board, until the next clean build) fails for reasons that have
    nothing to do with the routing strategy or the footprints involved.
    Learning from those runs would teach the wrong lessons."""
    broken = []          # pad counts of boards whose latest build stranded a part
    for r in rows:
        pads = (r.get("features") or {}).get("pads", 0)
        same = lambda p: abs(p - pads) <= 6
        if r.get("kind") == "build":
            broken = [p for p in broken if not same(p)]
            if r.get("placement_failed"):
                broken.append(pads)
        elif r.get("kind", "").startswith("route"):
            r["_tainted"] = any(same(p) for p in broken)
    return rows


def history(kind=None, include_tainted=False):
    rows = _mark_tainted(_load())
    return [r for r in rows if (kind is None or r.get("kind") == kind)
            and (include_tainted or not r.get("_tainted"))]


# ------------------------------------------------------------------ features

def board_features(board):
    """Size/density features of a loaded pcbnew board."""
    bb = board.GetBoardEdgesBoundingBox()
    area = max(1.0, bb.GetWidth() / 1e6 * bb.GetHeight() / 1e6)
    pads = sum(len(list(fp.Pads())) for fp in board.GetFootprints())
    nets = board.GetNetCount()
    return {"pads": pads, "parts": len(list(board.GetFootprints())), "nets": nets,
            "area_mm2": round(area, 1), "layers": board.GetCopperLayerCount(),
            "pad_density": round(pads / (area / 100.0), 3)}   # pads per cm²


def _similarity(f, g):
    if f.get("layers") != g.get("layers"):
        return 0.0
    a, b = max(f.get("pad_density", 1e-3), 1e-3), max(g.get("pad_density", 1e-3), 1e-3)
    size = abs(math.log(max(f.get("pads", 1), 1) / max(g.get("pads", 1), 1)))
    return math.exp(-abs(math.log(a / b)) / 0.35 - size / 1.5)


# ------------------------------------------------------------------ 1. routing strategy

def _reward(r):
    """1 for a complete, plane-friendly, quick route; lower for unrouted/bottom-heavy/slow."""
    if r["missing"] > 0:
        return max(0.0, 0.25 - 0.05 * r["missing"])
    return 1.0 - min(r.get("bottom_mm", 0), 120) / 240.0 - min(r.get("seconds", 0), 300) / 1500.0


def rank_arms(features, available_narrow=True):
    """Arms ordered best-first for a board with these features, with an explanation."""
    rows = history("route_attempt")
    scored = []
    for arm in ARMS:
        if arm[0] and not available_narrow:
            continue
        w_sum, r_sum = 0.0, 0.0
        for r in rows:
            if tuple(r["arm"]) != arm:
                continue
            w = _similarity(features, r["features"])
            w_sum += w
            r_sum += w * _reward(r)
        prior_w = 1.0
        mean = (r_sum + prior_w * PRIOR[arm]) / (w_sum + prior_w)
        bonus = 0.15 / math.sqrt(1.0 + w_sum)          # explore arms with little evidence here
        scored.append((mean + bonus, mean, w_sum, arm))
    scored.sort(key=lambda t: -t[0])
    return [s[3] for s in scored], scored


def arm_label(arm):
    narrow, cost = arm
    return "%s power tracks, %s" % ("signal-width" if narrow else "full-width",
                                    ("bottom layer cost x%.1f" % cost) if cost else "both layers free")


# ------------------------------------------------------------------ 2. board sizing

def _sizing_samples(layers, pads):
    """(successes, failures) as area-per-pad values for similar auto-sized boards."""
    ok, bad = [], []
    for r in history("route_final"):
        f = r["features"]
        if f.get("layers") != layers or not r.get("auto_size"):
            continue
        if abs(math.log(max(f.get("pads", 1), 1) / max(pads, 1))) > 1.2:
            continue
        app = f["area_mm2"] / max(f["pads"], 1)
        (ok if r.get("unconnected", 1) == 0 else bad).append(app)
    return ok, bad


def area_per_pad(layers, pads):
    """Learned area (mm² per pad) that reliably routes; None if nothing learned yet.

    The tightest size that has routed wins. A failure only matters if it was
    at a tighter size than every success; a failure at a size that has also
    routed fine was bad luck or a different problem, not the size."""
    ok, bad = _sizing_samples(layers, pads)
    if not ok:
        return None
    best = min(ok)
    closer = [b for b in bad if b < best]
    return max(best, max(closer) * 1.1) if closer else best


def min_area_per_pad(layers, pads):
    """Area per pad known to be too tight for similar boards (a floor), or None."""
    ok, bad = _sizing_samples(layers, pads)
    if ok:
        bad = [b for b in bad if b < min(ok)]
    return max(bad) * 1.1 if bad else None


# ------------------------------------------------------------------ 3. hard footprints

def hard_footprints():
    """Footprints that repeatedly sat at the end of an unrouted connection.

    Only boards that came close (a few misses) count: when many connections
    fail, the cause is the board (too small, crowded), not those footprints."""
    counts = {}
    for r in history("route_final"):
        if r.get("unconnected", 0) > 3:
            continue
        for fp in r.get("hard_footprints", []):
            counts[fp] = counts.get(fp, 0) + 1
    return counts


def extra_margin(fp_id):
    n = hard_footprints().get(fp_id, 0)
    return min(0.8, 0.4 * n)


# ------------------------------------------------------------------ report

def status():
    att = history("route_attempt")
    fin = history("route_final")
    builds = history("build")
    print("experience: %s" % log_path())
    print("  %d builds, %d routed boards, %d routing attempts" % (len(builds), len(fin), len(att)))
    ignored = len(history("route_attempt", include_tainted=True)) - len(att)
    if ignored:
        print("  %d attempts ignored: they followed a build that left a part off the board" % ignored)
    if att:
        print("  routing strategies (all boards):")
        for arm in ARMS:
            rs = [r for r in att if tuple(r["arm"]) == arm]
            if rs:
                ok = sum(1 for r in rs if r["missing"] == 0)
                print("    %-58s %2d/%-2d complete, avg reward %.2f" % (
                    arm_label(arm), ok, len(rs), sum(_reward(r) for r in rs) / len(rs)))
    hf = hard_footprints()
    if hf:
        print("  footprints that caused unrouted connections (get extra clearance):")
        for fp, n in sorted(hf.items(), key=lambda kv: -kv[1]):
            print("    %-60s x%d (+%.1f mm)" % (fp, n, extra_margin(fp)))
    for layers in (2, 4):
        a = area_per_pad(layers, 60)
        if a:
            print("  learned area per pad (%d layers, ~60-pad boards): %.1f mm²" % (layers, a))
    from . import knowledge
    kb = knowledge.load()
    if kb:
        print("  knowledge base: %d parts, %d routed reference boards (%s)" % (
            len(kb["parts"]), kb["layout"]["boards"], knowledge.kb_path()))
    else:
        print("  knowledge base: not installed (see the pcb-knowledge repo)")
    return 0


def reset():
    try:
        os.remove(log_path())
        print("cleared %s" % log_path())
    except OSError:
        print("nothing to clear")
    return 0
