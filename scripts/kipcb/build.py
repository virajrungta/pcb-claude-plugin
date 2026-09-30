"""spec.json -> KiCad project (schematic + placed board), with checks and previews."""

import json
import os

from . import checks, pcbgen, render as rendermod, schgen
from .spec import Design, SpecError


def _load(spec_path):
    try:
        d = Design(spec_path)
    except SpecError as e:
        print("ERROR: %s" % e)
        return None
    d.validate()
    for w in d.warnings:
        print("WARNING: " + w)
    if d.errors:
        for e in d.errors:
            print("ERROR: " + e)
        print("Spec has %d error(s); run `kipcb check` and fix them first." % len(d.errors))
        return None
    return d


def _outdir(d, out):
    pdir = os.path.abspath(out) if out else os.path.join(d.dir, d.name)
    os.makedirs(pdir, exist_ok=True)
    return pdir


def build(spec_path, out=None, render=True, placement=None):
    d = _load(spec_path)
    if d is None:
        return 1
    pdir = _outdir(d, out)
    sch_path = os.path.join(pdir, d.name + ".kicad_sch")
    pcb_path = os.path.join(pdir, d.name + ".kicad_pcb")

    _write_lib_tables(d, pdir)
    sb = schgen.write(d, sch_path)
    print("schematic: %s" % sch_path)
    # the board step rewrites the .kicad_pro, so it runs before ERC
    info = pcbgen.build(d, sb, pcb_path, placement)
    print("board:     %s (%.1f x %.1f mm, %d layers)" % (pcb_path, info["width"], info["height"],
                                                       int(d.board.get("layers", 2))))
    _write_placement(pdir, info["placement"])
    if info["failed"]:
        print("PLACEMENT FAILED for %s: parked beside the board. Enlarge the board, "
              "or give explicit 'place' positions." % ", ".join(info["failed"]))

    rc = checks.erc(pdir, d.name)
    print("(routing not done yet: expect DRC 'unconnected' items until `kipcb route`)")
    viol, unconnected, parity = checks.drc_result(pdir, d.name)
    placement_issues = [v for v in viol if v.get("type") in
                        ("courtyards_overlap", "malformed_courtyard", "copper_edge_clearance",
                         "items_not_allowed", "footprint_type_mismatch")]
    print("DRC placement issues: %d, schematic-parity issues: %d" % (len(placement_issues), len(parity)))
    for line in checks.summarize(placement_issues + parity, 20)[1]:
        print(line)
    if render:
        for p in rendermod.render(pdir, d.name):
            if p.endswith(".png"):
                print("preview:   %s" % p)
    print("\nNext: inspect previews, adjust 'place' hints in the spec if needed "
          "(current positions: out/placement.json), then `kipcb route %s`." % pdir)
    return 1 if (rc or info["failed"] or parity) else 0


def replace(spec_path, out=None, render=True):
    return build(spec_path, out, render)


def _write_placement(pdir, placement):
    out = os.path.join(pdir, "out")
    os.makedirs(out, exist_ok=True)
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
