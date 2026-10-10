"""How each pin of a chip is wired, as short patterns ("C 100nF -> GND", "R 10k -> +3V3",
"tied to GND", "not connected"). Shared by pcb-knowledge, which counts these patterns across
real open-source designs, and `kipcb inspect` on a schematic, which compares a user's
schematic with them, so both describe pins in exactly the same words.

    read_netlist(sch_path)   -> {"components": [...], "nets": [...]} from a .kicad_sch
    pin_patterns(rec)        -> yields (ref, part key, pin label, {patterns}) per IC pin
    shape(pattern)           -> the pattern without its value ("C -> GND"), or None
"""

import collections
import os
import re
import tempfile

ANCHOR = ("U", "IC", "M", "A", "MOD")
HELPER = {"R": "R", "C": "C", "L": "L", "FB": "FB", "D": "D", "LED": "D", "Y": "Y", "X": "Y", "Q": "Q", "SW": "SW"}
GND_RX = re.compile(r"^/?(.*[/_])?(GND[A-Z]*|AGND|DGND|PGND|VSS[A-Z]*|0V|EARTH|GROUND)$", re.I)
PWR_RX = re.compile(r"^/?(.*[/_])?(\+?\d+V\d*|\+?\d+\.\d+V|VCC\w*|VDD\w*|VBUS|VIN|VBAT\w*|V_?SYS|3V3\w*|5V\w*|12V|V\+|"
                    r"AVCC|AVDD|DVDD|IOVDD)$", re.I)
SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "R": 1, "r": 1, "": 1}
NOT_A_CHIP = re.compile(r"(HOLE|MOUNT|CONN|HEADER|TESTPOINT|TEST_POINT|LOGO|FIDUCIAL|JUMPER|PIN_?HEADER|"
                        r"SCREW|TERMINAL|SOCKET)", re.I)


def ref_prefix(ref):
    return re.sub(r"[\d_]+$", "", ref.split(".")[0]).upper()


def net_class(name):
    if GND_RX.match(name):
        return "GND"
    if PWR_RX.match(name):
        return "PWR"
    return "SIG"


def rail(name):
    return name.lstrip("/").split("/")[-1].upper()


def _fmt(x):
    return "%.3g" % x


def norm_value(v, kind):
    """'0.1uF' / '100n' / '100nF 16V' -> '100nF'; '4k7' -> '4.7k'."""
    v = (v or "").strip().split()[0] if v else ""
    m = re.match(r"^(\d+(?:\.\d+)?)([pnuµmkKMRr]?)(\d*)", v)
    if not m:
        return None          # placeholder like "C_Polarized" or "DNP": not a value
    if kind in ("C", "L") and not m.group(2):
        return m.group(1) + "?"   # unitless cap/inductor value: ambiguous (pF? uF?)
    num = float(m.group(1) + ("." + m.group(3) if m.group(3) and m.group(2) else ""))
    val = num * SI.get(m.group(2), 1)
    unit = {"C": "F", "L": "H"}.get(kind, "")
    if kind == "R":
        for div, suf in ((1e6, "M"), (1e3, "k"), (1, "")):
            if val >= div:
                return _fmt(val / div) + suf
        return _fmt(val)
    for div, suf in ((1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p")):
        if val >= div * 0.999:
            return _fmt(val / div) + suf + unit
    return v[:12]


def part_key(comp):
    part = comp.get("part") or comp.get("value") or ""
    return part.strip().upper()


def read_netlist(sch_path):
    """Components and nets of a KiCad schematic (via kicad-cli), or {"error": ...}."""
    from . import kienv, sexp
    fd, net_path = tempfile.mkstemp(suffix=".net")
    os.close(fd)
    try:
        p = kienv.run_cli(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", net_path, sch_path],
                          check=False)
        if p.returncode != 0 or not os.path.getsize(net_path):
            return {"error": "netlist export failed", "components": [], "nets": []}
        with open(net_path, encoding="utf-8", errors="replace") as f:
            tree = sexp.loads(f.read())
    finally:
        if os.path.exists(net_path):
            os.remove(net_path)
    rec = {"components": [], "nets": []}
    for c in sexp.find_all(sexp.find(tree, "components") or [], "comp"):
        ls = sexp.find(c, "libsource")
        rec["components"].append({
            "ref": str(sexp.value(c, "ref", "")), "value": str(sexp.value(c, "value", "")),
            "lib": str(sexp.value(ls, "lib", "")) if ls else "", "part": str(sexp.value(ls, "part", "")) if ls else "",
            "footprint": str(sexp.value(c, "footprint", "")),
        })
    for n in sexp.find_all(sexp.find(tree, "nets") or [], "net"):
        nodes = [[str(sexp.value(nd, "ref", "")), str(sexp.value(nd, "pin", "")),
                  str(sexp.value(nd, "pinfunction", "")), str(sexp.value(nd, "pintype", ""))]
                 for nd in sexp.find_all(n, "node")]
        rec["nets"].append({"name": str(sexp.value(n, "name", "")), "nodes": nodes})
    return rec


def pin_patterns(rec):
    """For every IC/module pin in an extracted schematic: (ref, part key, pin label, {patterns})."""
    comps = {c["ref"]: c for c in rec["components"]}
    net_nodes = {}
    ref_pins = collections.defaultdict(list)   # ref -> [(pin, net)]
    for n in rec["nets"]:
        net_nodes[n["name"]] = n["nodes"]
        for ref, pin, func, typ in n["nodes"]:
            ref_pins[ref].append((pin, n["name"]))
    for ref, c in comps.items():
        if ref_prefix(ref) not in ANCHOR:
            continue
        key = part_key(c)
        if not key or key in ("~", "?") or NOT_A_CHIP.search(key) or NOT_A_CHIP.search(c.get("lib", "")):
            continue
        for n in rec["nets"]:
            mine = [nd for nd in n["nodes"] if nd[0] == ref]
            if not mine:
                continue
            pin_label = mine[0][2] or mine[0][1]
            pats = set()
            cls = net_class(n["name"])
            if n["name"].startswith("unconnected-"):
                pats.add("not connected")
            elif cls == "GND":
                pats.add("tied to GND")
            elif cls == "PWR":
                pats.add("tied to %s" % rail(n["name"]))
            for oref, opin, ofunc, otype in n["nodes"]:
                if oref == ref:
                    continue
                kind = HELPER.get(ref_prefix(oref))
                oc = comps.get(oref, {})
                if kind in ("R", "C", "L", "FB", "D", "Y") and cls != "GND":   # every cap touches GND
                    other = [net for p2, net in ref_pins[oref] if p2 != opin]
                    far = other[0] if other else None
                    if far is None:
                        dest = "?"
                    elif net_class(far) == "GND":
                        dest = "GND"
                    elif net_class(far) == "PWR":
                        dest = rail(far)
                    else:
                        same = [nd for nd in net_nodes.get(far, []) if nd[0] == ref]
                        dest = ("own pin " + (same[0][2] or same[0][1])) if same else "signal"
                    if cls != "SIG" and dest not in ("GND",) and net_class(far or "") == "SIG":
                        continue   # on a shared rail, only rail-to-ground parts say something about this pin
                    if cls == "GND" and dest == "signal":
                        continue
                    val = norm_value(oc.get("value", ""), kind) if kind in ("R", "C", "L") else (oc.get("value", "")[:16])
                    pats.add("%s %s -> %s" % (kind, val or "?", dest))
                elif cls == "SIG" and ref_prefix(oref) in ("J", "P", "CN", "CONN"):
                    pats.add("to connector")
                elif cls == "SIG" and ref_prefix(oref) in ANCHOR:
                    pats.add("to %s" % (part_key(oc)[:24] or "IC"))
            yield ref, key, pin_label, pats


HELPER_RX = re.compile(r"^(R|C|L|FB|D|Y) (\S+) -> (.+)$")


def shape(pattern):
    """What a pattern says regardless of value and rail name: "C -> GND", "R -> supply",
    "R -> own pin EN", "tied to GND", "tied to supply", "not connected"; None for
    design-specific links ("to connector", "to <IC>"). Real designs disagree on values
    (10 uF vs 22 uF) far more than on whether the part is there at all."""
    m = HELPER_RX.match(pattern)
    if m:
        dest = m.group(3)
        if dest not in ("GND", "signal", "?") and not dest.startswith("own pin"):
            dest = "supply"
        return "%s -> %s" % (m.group(1), dest)
    if pattern == "tied to GND":
        return pattern
    if pattern.startswith("tied to "):
        return "tied to supply"
    if pattern == "not connected":
        return pattern
    return None
