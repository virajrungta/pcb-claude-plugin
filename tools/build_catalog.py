#!/usr/bin/env python3
"""Fold data/jlc_parts.json (JLCPCB basic library) into data/parts.json.

Fills the value -> LCSC tables of the resistor and capacitor parts (R0402 ...
C1206) and the part numbers of discrete basic parts. Capacitors get the
highest voltage rating JLCPCB stocks as basic for that value and size, and
the rating is recorded so `kipcb check` can warn when a rail is too close to it.

    python3 tools/fetch_jlc_basic.py     # refresh the snapshot (network)
    python3 tools/build_catalog.py       # rebuild data/parts.json
"""

import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JLC = os.path.join(ROOT, "data", "jlc_parts.json")
PARTS = os.path.join(ROOT, "data", "parts.json")
SIZES = ("0402", "0603", "0805", "1206")


def _norm(num, unit):
    """'4.7', 'k' -> '4.7k'; drop trailing .0."""
    n = ("%g" % float(num))
    return n + unit


def resistor_value(desc):
    m = re.search(r"(\d+(?:\.\d+)?)([mkM]?)Ω", desc)
    if not m:
        return None
    if m.group(2) == "m":
        return "%g" % (float(m.group(1)) / 1000)      # 100mΩ -> 0.1 (current-sense shunts)
    return _norm(m.group(1), m.group(2))


def cap_value(desc):
    m = re.search(r"(\d+(?:\.\d+)?)([pnu])F", desc)
    v = re.search(r"(\d+(?:\.\d+)?)(k?)V\b", desc)
    if not m:
        return None, None
    volts = float(v.group(1)) * (1000 if v.group(2) else 1) if v else None
    return _norm(m.group(1), m.group(2)) + "F", volts


def build():
    jlc = json.load(open(JLC))
    parts = json.load(open(PARTS))
    res = {s: {} for s in SIZES}
    cap = {s: {} for s in SIZES}
    for p in jlc["parts"]:
        pkg = p["package"]
        if pkg not in SIZES:
            continue
        if p["category"].startswith("Chip Resistor"):
            val = resistor_value(p["describe"])
            if val and (val not in res[pkg] or p["stock"] > res[pkg][val][1]):
                res[pkg][val] = (p["code"], p["stock"])
        elif p["category"].startswith("Multilayer Ceramic"):
            val, volts = cap_value(p["describe"])
            if not val or volts is None or volts > 100:
                continue          # skip 2 kV safety caps
            old = cap[pkg].get(val)
            if old is None or (volts, p["stock"]) > (old[2], old[1]):
                cap[pkg][val] = (p["code"], p["stock"], volts)

    def order(d):
        from_si = {"m": 1e-3, "p": 1e-12, "n": 1e-9, "u": 1e-6, "": 1, "k": 1e3, "M": 1e6}
        key = lambda v: float(re.match(r"[\d.]+", v).group(0)) * from_si[re.sub(r"[\d.]|F$", "", v)]
        return sorted(d, key=key)

    for s in SIZES:
        r = parts.setdefault("R" + s, {"symbol": "Device:R", "kind": "resistor"})
        r["footprint"] = "Resistor_SMD:R_%s_%sMetric" % (s, {"0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216"}[s])
        r["lcsc"] = {v: res[s][v][0] for v in order(res[s])}
        c = parts.setdefault("C" + s, {"symbol": "Device:C", "kind": "capacitor"})
        c["footprint"] = "Capacitor_SMD:C_%s_%sMetric" % (s, {"0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216"}[s])
        c["lcsc"] = {v: cap[s][v][0] for v in order(cap[s])}
        c["volts"] = {v: cap[s][v][2] for v in order(cap[s])}
    parts["_about"] = ("Prebuilt parts: use {\"ref\": \"R1\", \"part\": \"R0603\", \"value\": \"10k\"} instead of "
                       "symbol/footprint. LCSC numbers are JLCPCB basic parts (no extra setup fee) from the "
                       "snapshot of %s; `kipcb lcsc <C#>` checks live stock." % jlc["fetched"][:10])
    write(parts)
    n = sum(len(res[s]) + len(cap[s]) for s in SIZES)
    print("parts.json: %d resistor/capacitor values with LCSC numbers" % n)


def write(parts):
    """One part per line, so diffs stay readable."""
    keys = [k for k in parts if k.startswith("_")] + [k for k in parts if not k.startswith("_")]
    lines = ["  %s: %s" % (json.dumps(k), json.dumps(parts[k], ensure_ascii=False)) for k in keys]
    with open(PARTS, "w") as f:
        f.write("{\n" + ",\n".join(lines) + "\n}\n")


if __name__ == "__main__":
    build()
