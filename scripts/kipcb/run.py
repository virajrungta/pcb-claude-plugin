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


def run(spec_path, out=None, do_fab=True, force=False, passes=100, timeout=600):
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
    if (not force and last.get("hash") == digest and last.get("ready")
            and os.path.exists(os.path.join(pdir, d.name + ".kicad_pcb"))):
        ui.always("unchanged since the last successful run; nothing to redo (use --force to rebuild)\n")
        ui.always(report.text(report.write(pdir, d.name, last.get("timings"))))
        return 0
    ui.always("check  ok: %d parts, %d nets, %d warning(s)" % (len(d.components), len(d.nets), len(d.warnings)))
    for line in getattr(d, "block_summary", []):
        ui.always("       block " + line)
    for line in getattr(d, "support", []):
        ui.always("       support " + line)
    from . import estimate
    for line in estimate.lines(d, fab=do_fab):
        ui.always(line)

    # 2. build
    from . import build as buildmod
    t = time.time()
    rc = buildmod.build(spec_path, out, render=True, design=d)
    timings["build"] = round(time.time() - t)
    b = report._load(pdir, "build.json") or {}
    if b.get("placement_failed") or rc == 2:
        ui.always("build  stopped: %s did not fit on the board" % ", ".join(b.get("placement_failed", [])))
        ui.always("\n" + report.text(report.write(pdir, d.name, timings)))
        return 1
    ui.always("build  %.0f x %.0f mm, %d layers (%ds)" % (b.get("width", 0), b.get("height", 0),
                                                         b.get("layers", 2), timings["build"]))

    # 3. preflight: everything that would make routing fail, checked in seconds instead of minutes
    from . import preflight
    t = time.time()
    fails = preflight.run(pdir, d.name, d)
    timings["preflight"] = round(time.time() - t)
    if fails:
        ui.always("preflight  %d problem(s); not routing until they're fixed (fixes above)" % fails)
        ui.always("\n" + report.text(report.write(pdir, d.name, timings)))
        return 1
    ui.always("preflight  ok (%ds)" % timings["preflight"])

    # 4. route
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

    # 5. manufacturing files
    if do_fab:
        from . import fab as fabmod, learn
        t = time.time()
        fmt = "jlcpcb" if learn.settings().get("fab_house", "JLCPCB") == "JLCPCB" else "generic"
        frc = fabmod.export(pdir, d.name, fmt)
        timings["fab"] = round(time.time() - t)
        ui.always("fab    %s (%ds)" % ("exported" if frc == 0 else "skipped: fix the DRC problems first",
                                      timings["fab"]))

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
