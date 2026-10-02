"""ERC / DRC via kicad-cli with compact, actionable summaries."""

import collections
import json
import os

from . import kienv
from . import ui


def _out_dir(pdir):
    from . import paths
    return paths.reports(pdir)


def _report(args, rpt):
    """Run kicad-cli for a JSON report; if it wrote none, fail with kicad-cli's own message."""
    if os.path.exists(rpt):
        os.remove(rpt)                      # never read a stale report from an earlier run
    p = kienv.run_cli(args, check=False)
    if not os.path.exists(rpt):
        raise RuntimeError("kicad-cli %s produced no report (exit %s):\n%s" % (
            " ".join(args[:2]), p.returncode, (p.stdout or "").strip()[-1500:]))


def run_erc(pdir, name):
    rpt = os.path.join(_out_dir(pdir), "erc.json")
    _report(["sch", "erc", "--format", "json", "--severity-all", "-o", rpt,
             os.path.join(pdir, name + ".kicad_sch")], rpt)
    with open(rpt) as f:
        data = json.load(f)
    items = []
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            items.append(v)
    return items


def run_drc(pdir, name, parity=True):
    rpt = os.path.join(_out_dir(pdir), "drc.json")
    args = ["pcb", "drc", "--format", "json", "--severity-all", "-o", rpt]
    if parity and os.path.exists(os.path.join(pdir, name + ".kicad_sch")):
        args.append("--schematic-parity")
    args.append(os.path.join(pdir, name + ".kicad_pcb"))
    _report(args, rpt)
    with open(rpt) as f:
        data = json.load(f)
    return data


def _describe(v):
    items = "; ".join(i.get("description", "") for i in v.get("items", [])[:3])
    return "%s: %s [%s]" % (v.get("severity", "?"), v.get("description", v.get("type")), items)


def summarize(violations, limit=12):
    lines = []
    by_type = collections.Counter(v.get("type", "?") for v in violations)
    for v in violations[:limit]:
        lines.append("  - " + _describe(v))
    if len(violations) > limit:
        lines.append("  ... %d more (see report)" % (len(violations) - limit))
    return by_type, lines


def erc(pdir, name):
    items = run_erc(pdir, name)
    errs = [v for v in items if v.get("severity") == "error"]
    ui.say("ERC: %d error(s), %d warning(s)" % (len(errs), len(items) - len(errs)))
    _, lines = summarize(items)
    ui.say("\n".join(lines))
    return 1 if errs else 0


def drc_result(pdir, name):
    data = run_drc(pdir, name)
    viol = data.get("violations", [])
    unconnected = data.get("unconnected_items", [])
    parity = data.get("schematic_parity", [])
    return viol, unconnected, parity


def drc(pdir, name, quiet=False):
    viol, unconnected, parity = drc_result(pdir, name)
    errs = [v for v in viol if v.get("severity") == "error"]
    ui.say("DRC: %d error(s), %d warning(s), %d unconnected, %d schematic-parity issue(s)" % (
        len(errs), len(viol) - len(errs), len(unconnected), len(parity)))
    if not quiet:
        by_type, lines = summarize(errs + [v for v in viol if v not in errs])
        if by_type:
            ui.say("  by type: " + ", ".join("%s=%d" % kv for kv in by_type.most_common()))
        ui.say("\n".join(lines))
        if unconnected:
            ui.say("  unconnected:")
            ui.say("\n".join(summarize(unconnected, 15)[1]))
        if parity:
            ui.say("  parity:")
            ui.say("\n".join(summarize(parity, 15)[1]))
    return 1 if (errs or unconnected or parity) else 0
