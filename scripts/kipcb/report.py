"""The final summary of a run: where the project is, what was built, which checks
passed, the files to send to the manufacturer, and what still needs attention.

Built from the result files the steps leave in reports/, so `kipcb report`
can print it again at any time. Also saved as reports/REPORT.md.
"""

import json
import os

from . import paths


def _load(pdir, fname):
    try:
        with open(os.path.join(paths.reports(pdir), fname)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def collect(pdir, name, timings=None):
    build = _load(pdir, "build.json") or {}
    route = _load(pdir, "route.json")
    fab = _load(pdir, "fab.json")
    erc = _load(pdir, "erc.json") or {}
    drc = _load(pdir, "drc.json") or {}
    noise = _load(pdir, "noise.json") or []

    erc_items = [v for s in erc.get("sheets", []) for v in s.get("violations", [])]
    erc_err = sum(1 for v in erc_items if v.get("severity") == "error")
    drc_err = [v for v in drc.get("violations", []) if v.get("severity") == "error"]
    unconnected = len(drc.get("unconnected_items", [])) if route else None
    parity = len(drc.get("schematic_parity", []))
    nz = {lvl: [i for i in noise if i["level"] == lvl] for lvl in ("PASS", "WARN", "FAIL")}

    blockers, attention = [], []
    if build.get("placement_failed"):
        blockers.append("parts left off the board: %s" % ", ".join(build["placement_failed"]))
    if erc_err:
        blockers.append("%d ERC error(s) (reports/erc.json)" % erc_err)
    if route is None:
        blockers.append("not routed yet")
    else:
        if drc_err:
            blockers.append("%d DRC error(s) (reports/drc.json)" % len(drc_err))
        if unconnected:
            blockers.append("%d unconnected item(s)" % unconnected)
    if parity:
        blockers.append("%d schematic/board mismatch(es)" % parity)
    for i in nz["FAIL"]:
        blockers.append("noise: %s" % i["message"])
    if route is not None and fab is None and not blockers:
        blockers.append("manufacturing files not exported yet (kipcb fab)")

    attention += ["noise: %s" % i["message"] for i in nz["WARN"]]
    attention += build.get("spec_warnings", [])
    attention += build.get("notes", [])
    if fab and fab.get("missing_lcsc"):
        refs = [r for line in fab["missing_lcsc"] for r in line.split(",")]
        shown = ", ".join(refs[:6]) + (" and %d more" % (len(refs) - 6) if len(refs) > 6 else "")
        attention.append("%d part(s) have no LCSC number (needed for JLCPCB assembly): %s" % (len(refs), shown))

    return {
        "name": name, "title": build.get("title", name), "project_dir": pdir,
        "project": os.path.join(pdir, name + ".kicad_pro"), "spec": build.get("spec"),
        "board": build, "route": route, "fab": fab,
        "erc_errors": erc_err, "drc_errors": len(drc_err), "unconnected": unconnected, "parity": parity,
        "noise": {k: len(v) for k, v in nz.items()},
        "power": [n for n in build.get("spec_notes", []) if n.startswith(("power:", "rail "))],
        "blockers": blockers, "attention": attention, "timings": timings or {},
    }


def _status(r):
    if not r["blockers"]:
        return "READY TO ORDER"
    if r["route"] is None:
        return "BUILT, NOT ROUTED"
    return "NEEDS WORK"


def text(r, max_attention=8):
    b = r["board"]
    lines = ["%s: %s" % (_status(r), r["title"])]
    lines.append("Project   %s" % r["project"])
    if b:
        lines.append("Board     %.0f x %.0f mm, %d layers, %d parts, %d nets" % (
            b.get("width", 0), b.get("height", 0), b.get("layers", 2), b.get("parts", 0), b.get("nets", 0)))
    checks = ["ERC %s" % ("ok" if not r["erc_errors"] else "%d errors" % r["erc_errors"])]
    if r["route"] is not None:
        checks.append("DRC %s" % ("ok" if not r["drc_errors"] and not r["unconnected"] else
                                  "%d errors, %d unconnected" % (r["drc_errors"], r["unconnected"] or 0)))
    checks.append("parity %s" % ("ok" if not r["parity"] else "%d issues" % r["parity"]))
    n = r["noise"]
    if sum(n.values()):
        checks.append("noise %d pass / %d warn / %d fail" % (n["PASS"], n["WARN"], n["FAIL"]))
    lines.append("Checks    " + ", ".join(checks))
    for p in r["power"][:3]:
        lines.append("Power     " + p.replace("power: ", ""))
    if r["fab"]:
        lines.append("Fab       " + ", ".join(r["fab"]["files"]))
    previews = [p for p in ("previews/review.png", "previews/schematic.png")
                if os.path.exists(os.path.join(r["project_dir"], p))]
    if previews:
        lines.append("Previews  " + ", ".join(previews))
    lines.append("Report    reports/REPORT.md")
    if r["blockers"]:
        lines.append("Blocking")
        lines += ["  - " + x for x in r["blockers"]]
    if r["attention"]:
        lines.append("Check")
        lines += ["  - " + x for x in r["attention"][:max_attention]]
        if len(r["attention"]) > max_attention:
            lines.append("  - ... %d more in reports/REPORT.md" % (len(r["attention"]) - max_attention))
    t = r["timings"]
    if t:
        lines.append("Time      %s (total %ds)" % (", ".join("%s %ds" % kv for kv in t.items()),
                                                    sum(t.values())))
    return "\n".join(lines)


def markdown(r):
    b = r["board"]
    md = ["# %s" % r["title"], "", "**Status:** %s" % _status(r), ""]
    md += ["| | |", "|---|---|",
           "| Project | `%s` |" % r["project"],
           "| Design spec | `%s` |" % (r["spec"] or "-")]
    if b:
        md.append("| Board | %.0f x %.0f mm, %d layers, %d parts, %d nets |" % (
            b.get("width", 0), b.get("height", 0), b.get("layers", 2), b.get("parts", 0), b.get("nets", 0)))
    if r["route"]:
        md.append("| Routing | %s; %d vias; %s mm of signal on the bottom layer |" % (
            r["route"].get("chosen", "-"), r["route"].get("vias", 0), r["route"].get("bottom_mm", "-")))
    md.append("| ERC | %s |" % ("no errors" if not r["erc_errors"] else "%d errors" % r["erc_errors"]))
    if r["route"] is not None:
        md.append("| DRC | %d errors, %d unconnected |" % (r["drc_errors"], r["unconnected"] or 0))
    md.append("| Schematic vs board | %s |" % ("match" if not r["parity"] else "%d issues" % r["parity"]))
    n = r["noise"]
    md.append("| Noise checks | %d pass, %d warn, %d fail |" % (n["PASS"], n["WARN"], n["FAIL"]))
    if r["fab"]:
        md.append("| Manufacturing files | %s |" % ", ".join("`%s`" % f for f in r["fab"]["files"]))
    if r["timings"]:
        md.append("| Time | %s |" % ", ".join("%s %ds" % kv for kv in r["timings"].items()))
    if r["power"]:
        md += ["", "## Power", ""] + ["- " + p for p in r["power"]]
    if r["blockers"]:
        md += ["", "## Blocking", ""] + ["- " + x for x in r["blockers"]]
    if r["attention"]:
        md += ["", "## Worth checking", ""] + ["- " + x for x in r["attention"]]
    md += ["", "## Next", "",
           "- Open in KiCad: `open \"%s\"`" % r["project"],
           "- Order boards: upload `fab/%s-gerbers.zip`; for assembly also the BOM and CPL files" % r["name"],
           ""]
    return "\n".join(md)


def write(pdir, name, timings=None):
    r = collect(pdir, name, timings)
    with open(os.path.join(paths.reports(pdir), "REPORT.md"), "w") as f:
        f.write(markdown(r))
    return r
