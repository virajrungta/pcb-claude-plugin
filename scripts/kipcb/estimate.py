"""Time estimates for each pipeline step, shown before a run starts.

Starts from simple rules (part count, finest pin pitch, connections) and
switches to the user's own measured timings once similar boards have run
(learned from `run_timings` records). Long steps get a one-line reason.
"""

import math

from . import learn

STEPS = ("check", "build", "preflight", "route", "fab")
LABEL = {"check": "check", "build": "build", "preflight": "preflight", "route": "route", "fab": "files"}


def features(design):
    pads = sum(len(c.fp.pads) for c in design.components if c.fp is not None)
    conns = sum(len(p) - 1 for p in design.nets.values() if len(p) > 1)
    fine = []
    for c in design.components:
        # chips with many fine-pitch pins are what slows the autorouter; connectors like
        # USB-C have fine pitch too, but few pins to fan out
        if c.fp is None or len(c.fp.pads) < 24 or c.ref.rstrip("0123456789") not in ("U", "IC"):
            continue
        p = c.fp.pitch()
        if p and p <= 0.52:
            fine.append((p, c.ref, c.value))
    fine.sort()
    return {"parts": len(design.components), "pads": pads, "connections": conns,
            "layers": int(design.board.get("layers", 2)),
            "finest_pitch": round(fine[0][0], 2) if fine else None,
            "fine_parts": ["%s (%s)" % (r, v) for _, r, v in fine[:2]]}


def _heuristic(f):
    route_lo, route_hi = 5, 30
    if (f["finest_pitch"] and f["finest_pitch"] <= 0.42) or f["pads"] > 300:
        route_lo, route_hi = 300, 480
    elif f["finest_pitch"] or f["pads"] > 120 or f["connections"] > 120:
        route_lo, route_hi = 60, 180
    build = 2 + 0.035 * f["parts"] ** 2          # auto-size tries more board sizes as parts grow
    return {"check": (0.5, 1.5), "build": (build * 0.7, build * 1.3), "preflight": (0.5, 1.5),
            "route": (route_lo, route_hi), "fab": (4, 8)}


def _learned(f):
    """Median timings of past runs of similar boards (same layers, pads within ~40%, same fine-pitch-ness)."""
    rows = []
    for r in learn.history("run_timings"):
        g = r.get("features", {})
        if g.get("layers") != f["layers"] or bool(g.get("finest_pitch")) != bool(f["finest_pitch"]):
            continue
        if abs(math.log(max(g.get("pads", 1), 1) / float(max(f["pads"], 1)))) > 0.35:
            continue
        rows.append(r.get("timings", {}))
    if len(rows) < 2:
        return {}
    out = {}
    for step in STEPS:
        xs = sorted(t[step] for t in rows if step in t)
        if len(xs) >= 2:
            med = xs[len(xs) // 2]
            out[step] = (med * 0.8, max(med * 1.2, xs[-1]))
    out["_n"] = len(rows)
    return out


def estimate(design):
    f = features(design)
    est = _heuristic(f)
    learned = _learned(f)
    for step in STEPS:
        if step in learned:
            est[step] = learned[step]
    reasons = []
    lo, hi = est["route"]
    if hi > 120:
        if f["finest_pitch"]:
            reasons.append("routing takes a while: %s %s %.1f mm pins; boards like this usually need "
                           "%s of autorouting" % (" and ".join(f["fine_parts"]),
                                                  "have" if len(f["fine_parts"]) > 1 else "has",
                                                  f["finest_pitch"], span(lo, hi)))
        else:
            reasons.append("routing takes a while: %d connections on %d layers; boards this size usually "
                           "need %s of autorouting" % (f["connections"], f["layers"], span(lo, hi)))
    blo, bhi = est["build"]
    if bhi > 60:
        reasons.append("building takes about %s: %d parts, and auto-size tries several board sizes" % (
            span(blo, bhi), f["parts"]))
    if learned.get("_n"):
        reasons.append("(from %d similar boards you've built)" % learned["_n"])
    return est, reasons, f


def fmt(seconds):
    if seconds < 60:
        return "%ds" % max(1, round(seconds))
    m = seconds / 60.0
    return "%d min" % round(m) if m >= 1.5 else "1 min"


def span(lo, hi):
    if hi < 90:
        return "~" + fmt((lo + hi) / 2)
    a, b = fmt(lo), fmt(hi)
    if a == b:
        return "~" + a
    if a.endswith(" min") and b.endswith(" min"):
        return "%s-%s" % (a[:-4], b)
    return "%s-%s" % (a, b)


def lines(design, fab=True):
    est, reasons, _ = estimate(design)
    steps = [s for s in STEPS if fab or s != "fab"]
    total = sum((est[s][0] + est[s][1]) / 2 for s in steps)
    head = "plan   " + " · ".join("%s %s" % (LABEL[s], "~" + fmt((est[s][0] + est[s][1]) / 2)) for s in steps)
    out = [head + "   (total ~%s)" % fmt(total)]
    out += ["       " + r for r in reasons]
    return out


def checklist(design):
    """The progress checklist Claude shows the user (task list), with times."""
    est, reasons, f = estimate(design)
    mid = lambda s: "~" + fmt((est[s][0] + est[s][1]) / 2)
    why = ""
    if est["route"][1] > 120 and f["fine_parts"]:
        why = ", %s has fine-pitch pins" % " and ".join(f["fine_parts"])
    return ["Requirements", "Components & circuit", "Schematic",
            "Placement (%s)" % mid("build"), "Preflight checks (%s)" % mid("preflight"),
            "Routing (%s%s)" % (mid("route"), why), "DRC & noise checks",
            "Manufacturing files (%s)" % mid("fab"), "Review & hand-off"]


def record(design, timings):
    f = features(design)
    f.pop("fine_parts", None)
    learn.record("run_timings", features=f, timings=timings)
