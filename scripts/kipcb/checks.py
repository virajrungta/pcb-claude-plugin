"""ERC / DRC via kicad-cli with compact, actionable summaries."""

import collections
import json
import os

from . import kienv


def _out_dir(pdir):
    d = os.path.join(pdir, "out")
    os.makedirs(d, exist_ok=True)
    return d


def run_erc(pdir, name):
    rpt = os.path.join(_out_dir(pdir), "erc.json")
    kienv.run_cli(["sch", "erc", "--format", "json", "--severity-all", "-o", rpt,
                   os.path.join(pdir, name + ".kicad_sch")], check=False)
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
    kienv.run_cli(args, check=False)
    with open(rpt) as f:
        data = json.load(f)
    return data


def _describe(v):
    items = "; ".join(i.get("description", "") for i in v.get("items", [])[:3])
    return "%s: %s [%s]" % (v.get("severity", "?"), v.get("description", v.get("type")), items)


def summarize(violations, limit=40):
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
    print("ERC: %d error(s), %d warning(s)" % (len(errs), len(items) - len(errs)))
    _, lines = summarize(items)
    print("\n".join(lines))
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
    print("DRC: %d error(s), %d warning(s), %d unconnected, %d schematic-parity issue(s)" % (
        len(errs), len(viol) - len(errs), len(unconnected), len(parity)))
    if not quiet:
        by_type, lines = summarize(errs + [v for v in viol if v not in errs])
        if by_type:
            print("  by type: " + ", ".join("%s=%d" % kv for kv in by_type.most_common()))
        print("\n".join(lines))
        if unconnected:
            print("  unconnected:")
            print("\n".join(summarize(unconnected, 15)[1]))
        if parity:
            print("  parity:")
            print("\n".join(summarize(parity, 15)[1]))
    return 1 if (errs or unconnected or parity) else 0
