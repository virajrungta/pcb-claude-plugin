"""Manufacturing outputs: Gerbers, drill, BOM, pick-and-place, zip."""

import csv
import glob
import os
import zipfile

from . import checks, kienv
from . import ui


def _layers(pcb_path):
    with open(pcb_path, encoding="utf-8") as f:
        head = f.read(20000)
    inner = [l for l in ("In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu") if '"%s"' % l in head]
    return ["F.Cu"] + inner + ["B.Cu", "F.Paste", "B.Paste", "F.Silkscreen", "B.Silkscreen",
                               "F.Mask", "B.Mask", "Edge.Cuts"]


def export(pdir, name, fab="jlcpcb", force=False):
    pcb = os.path.join(pdir, name + ".kicad_pcb")
    sch = os.path.join(pdir, name + ".kicad_sch")
    viol, unconnected, parity = checks.drc_result(pdir, name)
    errs = [v for v in viol if v.get("severity") == "error"]
    if (errs or unconnected) and not force:
        ui.always("Refusing to export: DRC has %d error(s) and %d unconnected item(s). "
              "Run `kipcb drc %s` and fix them first." % (len(errs), len(unconnected), pdir))
        return 1

    fdir = os.path.join(pdir, "fab")
    gdir = os.path.join(fdir, "gerbers")
    os.makedirs(gdir, exist_ok=True)
    for old in glob.glob(os.path.join(gdir, "*")):
        os.remove(old)

    kienv.run_cli(["pcb", "export", "gerbers", "--layers", ",".join(_layers(pcb)),
                   "--subtract-soldermask", "--use-drill-file-origin", "-o", gdir + os.sep, pcb])
    kienv.run_cli(["pcb", "export", "drill", "--format", "excellon", "--excellon-units", "mm",
                   "--drill-origin", "plot", "--generate-map", "--map-format", "gerberx2", "-o", gdir + os.sep, pcb])

    zpath = os.path.join(fdir, "%s-gerbers.zip" % name)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(gdir)):
            z.write(os.path.join(gdir, f), f)

    # pick and place
    pos_raw = os.path.join(fdir, "%s-pos.csv" % name)
    kienv.run_cli(["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
                   "--exclude-dnp", "--use-drill-file-origin", "-o", pos_raw, pcb])
    cpl = os.path.join(fdir, "%s-cpl.csv" % name)
    with open(pos_raw, newline="") as f:
        rows = list(csv.DictReader(f))
    with open(cpl, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in rows:
            w.writerow([r["Ref"], "%smm" % r["PosX"], "%smm" % r["PosY"],
                        "Top" if r["Side"].lower().startswith("top") else "Bottom", r["Rot"]])

    # BOM
    bom = os.path.join(fdir, "%s-bom.csv" % name)
    if os.path.exists(sch):
        raw = os.path.join(fdir, ".bom_raw.csv")
        kienv.run_cli(["sch", "export", "bom", "--fields", "Value,Reference,Footprint,LCSC,MPN,${QUANTITY}",
                       "--labels", "Comment,Designator,Footprint,LCSC Part #,MPN,Qty",
                       "--group-by", "Value,Footprint,LCSC", "--ref-range-delimiter", "",
                       "--exclude-dnp", "-o", raw, sch])
        with open(raw, newline="") as f:
            rows = list(csv.reader(f))
        os.remove(raw)
        missing = []
        codes = {}
        with open(bom, "w", newline="") as f:
            w = csv.writer(f)
            for i, r in enumerate(rows):
                if i > 0 and len(r) > 2:
                    r[2] = r[2].split(":", 1)[-1]          # drop the library nickname
                    if fab == "jlcpcb" and not r[3]:
                        missing.append(r[1])
                    elif r[3]:
                        codes[r[3].strip()] = r[1]
                w.writerow(r)
    else:
        missing, codes = [], {}

    stock = []
    if fab == "jlcpcb" and codes and os.environ.get("KIPCB_OFFLINE") != "1":
        from . import lcsc                                   # live JLCPCB stock; silent when offline
        try:
            stock = ["%s (%s): %s" % (codes[c], c, msg) for c, msg in lcsc.check_bom(list(codes))]
        except Exception:
            stock = []

    import json
    from . import paths
    with open(os.path.join(paths.reports(pdir), "fab.json"), "w") as f:
        json.dump({"format": fab, "files": [os.path.relpath(p, pdir) for p in (zpath, bom, cpl) if os.path.exists(p)],
                   "missing_lcsc": missing, "stock_issues": stock, "parity_issues": len(parity)}, f, indent=1)
    ui.say("fab outputs in %s" % fdir)
    ui.say("  %s   <- upload to the PCB order page" % os.path.relpath(zpath, pdir))
    ui.say("  %s" % os.path.relpath(bom, pdir))
    ui.say("  %s   <- pick-and-place (CPL) for assembly" % os.path.relpath(cpl, pdir))
    if missing:
        ui.say("NOTE: no LCSC part number for %s. Add \"lcsc\" to those components for JLCPCB assembly "
              "(or hand-solder / source them)." % ", ".join(missing))
    for line in stock:
        ui.say("WARNING: %s; pick another part (`kipcb lcsc -s \"<value> <package>\"`)" % line)
    if fab == "jlcpcb":
        ui.say("NOTE: check part rotations in JLCPCB's assembly preview; some footprints need a "
              "rotation offset (a known KiCad/JLC convention difference).")
    if parity:
        ui.say("WARNING: %d schematic/PCB parity issue(s); run `kipcb drc`." % len(parity))
    return 0
