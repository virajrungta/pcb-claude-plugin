"""spec.json -> KiCad project (schematic + placed board), with checks and previews."""

import json
import os

from . import checks, pcbgen, render as rendermod, schgen
from . import paths, ui
from .spec import Design, SpecError


def _load(spec_path):
    try:
        d = Design(spec_path)
    except SpecError as e:
        ui.always("ERROR: %s" % e)
        return None
    d.validate()
    for w in d.warnings:
        ui.say("WARNING: " + w)
    if d.errors:
        for e in d.errors:
            ui.always("ERROR: " + e)
        ui.always("Spec has %d error(s); run `kipcb check` and fix them first." % len(d.errors))
        return None
    return d


def _outdir(d, out):
    pdir = os.path.abspath(out) if out else os.path.join(d.dir, d.name)
    os.makedirs(pdir, exist_ok=True)
    return pdir


def build(spec_path, out=None, render=True, placement=None, design=None):
    d = design if design is not None else _load(spec_path)
    if d is None:
        return 1
    pdir = _outdir(d, out)
    for stale in ("route.json", "fab.json", "preflight.json", "noise.json"):   # they describe the old board
        try:
            os.remove(os.path.join(paths.reports(pdir), stale))
        except OSError:
            pass
    sch_path = os.path.join(pdir, d.name + ".kicad_sch")
    pcb_path = os.path.join(pdir, d.name + ".kicad_pcb")

    _write_lib_tables(d, pdir)
    sb = schgen.write(d, sch_path)
    ui.say("schematic: %s" % sch_path)
    # the board step rewrites the .kicad_pro, so it runs before ERC
    info = pcbgen.build(d, sb, pcb_path, placement)
    ui.say("board:     %s (%.1f x %.1f mm, %d layers)" % (pcb_path, info["width"], info["height"],
                                                       int(d.board.get("layers", 2))))
    _write_placement(pdir, info["placement"])
    if info["failed"]:
        ui.always("PLACEMENT FAILED for %s: no room even with tight spacing, so it is parked beside "
              "the board and the board cannot be routed. Enlarge the board, reduce fixed "
              "positions/mounting holes, or give explicit 'place' positions, then rebuild."
              % ", ".join(info["failed"]))

    rc = checks.erc(pdir, d.name)
    ui.say("(routing not done yet: expect DRC 'unconnected' items until `kipcb route`)")
    viol, unconnected, parity = checks.drc_result(pdir, d.name)
    placement_issues = [v for v in viol if v.get("type") in
                        ("courtyards_overlap", "malformed_courtyard", "copper_edge_clearance",
                         "items_not_allowed", "footprint_type_mismatch")]
    ui.say("DRC placement issues: %d, schematic-parity issues: %d" % (len(placement_issues), len(parity)))
    for line in checks.summarize(placement_issues + parity, 20)[1]:
        ui.say(line)
    from . import learn, noise
    ui.say("\nPlacement noise checks (decoupling, crystals, switch nodes):")
    rep = noise.check(pdir, d.name)
    noise.run(pdir, d.name, quiet=True, save=False)
    pads = sum(len(c.fp.pads) for c in d.components)
    area = info["width"] * info["height"]
    learn.record("build", features={"pads": pads, "parts": len(d.components), "nets": len(d.nets),
                                    "area_mm2": round(area, 1), "layers": int(d.board.get("layers", 2)),
                                    "pad_density": round(pads / (area / 100.0), 3)},
                 spacing=d.board.get("spacing", "normal"), placement_failed=len(info["failed"]),
                 noise_fail=rep.count("FAIL"), noise_warn=rep.count("WARN"))
    _write_json(pdir, "build.json", {
        "spec": os.path.abspath(spec_path), "name": d.name, "title": d.raw.get("title", d.name),
        "width": info["width"], "height": info["height"], "layers": int(d.board.get("layers", 2)),
        "parts": len(d.components), "nets": len(d.nets), "pads": pads,
        "placement_failed": info["failed"], "notes": info.get("notes", []),
        "spec_warnings": d.warnings, "spec_notes": getattr(d, "notes", []),
        "erc": rc, "parity": len(parity), "placement_issues": len(placement_issues)})
    if render:
        for p in rendermod.render(pdir, d.name):
            if p.endswith(".png"):
                ui.say("preview:   %s" % p)
    ui.say("\nNext: look at previews/review.png, adjust 'place' hints if needed "
           "(current positions: reports/placement.json), then `kipcb route %s`." % pdir)
    if info["failed"]:
        return 2
    return 1 if (rc or parity) else 0


def replace(spec_path, out=None, render=True):
    return build(spec_path, out, render)


def _write_json(pdir, fname, data):
    from . import paths
    with open(os.path.join(paths.reports(pdir), fname), "w") as f:
        json.dump(data, f, indent=1)


def _write_placement(pdir, placement):
    from . import paths
    out = paths.reports(pdir)
    data = {ref: {"x": round(x, 2), "y": round(y, 2), "rot": round(rot) % 360}
            for ref, (x, y, rot) in sorted(placement.items())}
    with open(os.path.join(out, "placement.json"), "w") as f:
        json.dump(data, f, indent=1)


def _write_lib_tables(d, pdir):
    """Project lib tables for spec-provided libraries, so KiCad's GUI finds them too."""
    libs = d.raw.get("libraries") or {}
    for kind, fname, head in (("symbols", "sym-lib-table", "sym_lib_table"),
                              ("footprints", "fp-lib-table", "fp_lib_table")):
        entries = libs.get(kind) or {}
        if not entries:
            continue
        lines = ["(%s" % head, "  (version 7)"]
        for nick, path in sorted(entries.items()):
            full = path if os.path.isabs(path) or path.startswith("$") else os.path.join(d.dir, path)
            if not full.startswith("$"):
                full = "${KIPRJMOD}/" + os.path.relpath(os.path.abspath(full), pdir)
            lines.append('  (lib (name "%s")(type "KiCad")(uri "%s")(options "")(descr ""))' % (nick, full))
        lines.append(")")
        with open(os.path.join(pdir, fname), "w") as f:
            f.write("\n".join(lines) + "\n")
