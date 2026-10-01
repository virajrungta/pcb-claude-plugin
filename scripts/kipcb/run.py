"""kipcb run: spec -> checked, routed board and manufacturing files in one command.

Prints one progress line per step and then the final report, instead of the
full output of four separate commands. If the spec hasn't changed since the
last successful run, it skips straight to the report.
"""

import hashlib
import json
import os
import time

from . import __version__, paths, report, ui


def _spec_hash(spec_path):
    with open(spec_path, "rb") as f:
        return hashlib.sha256(f.read() + __version__.encode()).hexdigest()[:16]


def _cache_file(pdir):
    return os.path.join(paths.work(pdir), "last_run.json")


def _stage_file(pdir):
    return os.path.join(paths.work(pdir), "stage.json")


STAGES = ("build", "preflight", "route", "fab")


def _erc_errors(pdir):
    erc = report._load(pdir, "erc.json") or {}
    return sum(1 for sh in erc.get("sheets", []) for v in sh.get("violations", []) if v.get("severity") == "error")


TICKS = {"schematic + placement": "Components & circuit, Schematic, Placement",
         "preflight": "Preflight checks", "routing + DRC + noise checks": "Routing, DRC & noise checks",
         "manufacturing files": "Manufacturing files"}


def _checkpoint(done, summary, d, nxt):
    """One line Claude can relay and tick in its checklist: what finished, what's next."""
    from . import estimate
    line = "checkpoint: %s done -- %s [tick: %s]" % (done, summary, TICKS.get(done, done))
    if nxt:
        est, _, _ = estimate.estimate(d)
        lo, hi = est.get(nxt, (0, 0))
        line += "; next: %s %s" % ({"fab": "manufacturing files"}.get(nxt, nxt), estimate.span(lo, hi))
    ui.always(line)


def run(spec_path, out=None, do_fab=True, force=False, passes=100, timeout=600, until=None, resume=False):
    """until: stop after that stage ("build", "preflight", "route"); resume: continue after the
    last finished stage of a staged run (so Claude can tick a checklist between stages)."""
    from .spec import Design, SpecError
    ui.QUIET = True
    timings = {}
    t0 = time.time()

    # 1. check
    try:
        d = Design(spec_path)
    except SpecError as e:
        ui.always("ERROR: %s" % e)
        return 1
    d.validate()
    timings["check"] = round(time.time() - t0)
    if d.errors:
        ui.always("check  %d error(s) in the spec; nothing was built:" % len(d.errors))
        for e in d.errors[:15]:
            ui.always("  - " + e)
        if len(d.errors) > 15:
            ui.always("  - ... %d more (kipcb check %s)" % (len(d.errors) - 15, spec_path))
        return 1
    pdir = os.path.abspath(out) if out else os.path.join(d.dir, d.name)
    os.makedirs(pdir, exist_ok=True)

    digest = _spec_hash(spec_path)
    try:
        with open(_cache_file(pdir)) as f:
            last = json.load(f)
    except (OSError, ValueError):
        last = {}
    start = 0
    if resume:
        try:
            with open(_stage_file(pdir)) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        if st.get("hash") != digest or not os.path.exists(os.path.join(pdir, d.name + ".kicad_pcb")):
            ui.always("nothing to resume: the spec changed or no staged run exists; start with "
                      "`kipcb run %s --until build`" % spec_path)
            return 1
        start = STAGES.index(st["stage"]) + 1
        timings.update(st.get("timings", {}))
        if start >= len(STAGES):
            ui.always(report.text(report.write(pdir, d.name, timings)))
            return 0
    stop = STAGES.index(until) if until in STAGES else len(STAGES)

    def staged(stage):
        """Save progress after a stage; True if the run should stop here."""
        with open(_stage_file(pdir), "w") as f:
            json.dump({"hash": digest, "stage": stage, "timings": timings}, f)
        return STAGES.index(stage) >= stop

    if (not resume and not force and last.get("hash") == digest and last.get("ready")
            and os.path.exists(os.path.join(pdir, d.name + ".kicad_pcb"))):
        ui.always("unchanged since the last successful run; nothing to redo (use --force to rebuild)\n")
        ui.always(report.text(report.write(pdir, d.name, last.get("timings"))))
        return 0
    from . import estimate, progress
    est, _, _f = estimate.estimate(d)
    est_txt = {k: "~" + estimate.fmt((v[0] + v[1]) / 2) for k, v in est.items()}

    def mark(stage_name, ok, note=""):
        try:
            progress.stage(pdir, d.name, stage_name, ok, timings.get(stage_name), note, est_txt)
        except Exception:
            pass                      # the checklist is a nicety; never fail a run over it

    if not resume:
        ui.always("check  ok: %d parts, %d nets, %d warning(s)" % (len(d.components), len(d.nets), len(d.warnings)))
        for line in getattr(d, "block_summary", []):
            ui.always("       block " + line)
        for line in getattr(d, "support", []):
            ui.always("       support " + line)
        for line in estimate.lines(d, fab=do_fab):
            ui.always(line)

    # 2. build: schematic + placed board
    if start <= STAGES.index("build"):
        from . import build as buildmod
        t = time.time()
        rc = buildmod.build(spec_path, out, render=True, design=d)
        timings["build"] = round(time.time() - t)
        b = report._load(pdir, "build.json") or {}
        if b.get("placement_failed") or rc == 2:
            mark("build", False, "parts didn't fit")
            ui.always("build  stopped: %s did not fit on the board" % ", ".join(b.get("placement_failed", [])))
            ui.always("\n" + report.text(report.write(pdir, d.name, timings)))
            return 1
        ui.always("build  %.0f x %.0f mm, %d layers (%ds)" % (b.get("width", 0), b.get("height", 0),
                                                             b.get("layers", 2), timings["build"]))
        _checkpoint("schematic + placement", "%d parts on a %.0f x %.0f mm board, ERC %s" % (
            len(d.components), b.get("width", 0), b.get("height", 0),
            "ok" if not _erc_errors(pdir) else "%d error(s)" % _erc_errors(pdir)), d, "preflight")
        mark("build", True)
        if staged("build"):
            return 0

    # 3. preflight: everything that would make routing fail, checked in seconds instead of minutes
    if start <= STAGES.index("preflight"):
        from . import preflight
        t = time.time()
        fails = preflight.run(pdir, d.name, d)
        timings["preflight"] = round(time.time() - t)
        if fails:
            mark("preflight", False, "%d problem(s) to fix" % fails)
            ui.always("preflight  %d problem(s); not routing until they're fixed (fixes above)" % fails)
            ui.always("\n" + report.text(report.write(pdir, d.name, timings)))
            return 1
        ui.always("preflight  ok (%ds)" % timings["preflight"])
        _checkpoint("preflight", "the board can route cleanly", d, "route")
        mark("preflight", True)
        if staged("preflight"):
            return 0

    # 4. route (+ DRC and noise checks)
    if start <= STAGES.index("route"):
        from . import route as routemod
        t = time.time()
        rc = routemod.route(pdir, d.name, passes=passes, timeout=timeout)
        timings["route"] = round(time.time() - t)
        rj = report._load(pdir, "route.json") or {}
        if rc in (2, 3) or not rj:
            ui.always("route  not done (see above)")
            ui.always("\n" + report.text(report.write(pdir, d.name, timings)))
            return 1
        vias = rj.get("vias", 0)
        ui.always("route  %s, %d via%s (%ds)" % ("complete" if not rj.get("unconnected") else
                                                "%d unconnected" % rj["unconnected"],
                                                vias, "" if vias == 1 else "s", timings["route"]))
        drc = report._load(pdir, "drc.json") or {}
        errs = sum(1 for v in drc.get("violations", []) if v.get("severity") == "error")
        _checkpoint("routing + DRC + noise checks", "%s, DRC %s" % (
            "all connections routed" if not rj.get("unconnected") else "%d connection(s) left" % rj["unconnected"],
            "clean" if not errs else "%d error(s)" % errs), d, "fab" if do_fab else None)
        left = rj.get("unconnected") or errs
        mark("route", not left, ("%d unrouted" % rj["unconnected"]) if rj.get("unconnected") else
             ("%d DRC error(s)" % errs if errs else ""))
        if staged("route"):
            return 0

    # 5. manufacturing files
    if do_fab:
        from . import fab as fabmod, learn
        t = time.time()
        fmt = "jlcpcb" if learn.settings().get("fab_house", "JLCPCB") == "JLCPCB" else "generic"
        frc = fabmod.export(pdir, d.name, fmt)
        timings["fab"] = round(time.time() - t)
        ui.always("fab    %s (%ds)" % ("exported" if frc == 0 else "skipped: fix the DRC problems first",
                                      timings["fab"]))
        if frc == 0:
            _checkpoint("manufacturing files", "Gerbers, BOM and pick-and-place in fab/", d, None)
        mark("fab", frc == 0, "" if frc == 0 else "blocked by DRC")
    staged("fab")

    # printable extras for people (bottom view, board PDF); Claude doesn't need to read them
    from . import render as rendermod
    rendermod.render(pdir, d.name, "full")

    r = report.write(pdir, d.name, timings)
    ready = not r["blockers"]
    if ready:
        from . import blocks, learn
        if learn.enabled():
            blocks.remember(d)          # its parts become instant `part` names next time
    with open(_cache_file(pdir), "w") as f:
        json.dump({"hash": digest, "ready": ready, "timings": timings}, f)
    if timings.get("route") is not None:
        estimate.record(d, timings)       # future estimates use real timings of similar boards
    ui.always("\n" + report.text(r))
    return 0 if ready else 1
