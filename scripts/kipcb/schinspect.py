"""`kipcb inspect` on a schematic: compare how each chip is wired with real open-source designs.

For every IC or module the knowledge base knows, each pin's wiring (kipcb.wiring patterns,
the same words pcb-knowledge counted) is checked against what most real designs do on that
pin: a capacitor to ground that most designs have and this one doesn't, a pull-up that's
missing, a pin left unconnected that real designs connect, and helper values far from the
usual one. Common practice, not a spec: the datasheet wins.
"""

import re

from . import knowledge, wiring

MIN_DESIGNS = 3        # parts seen in fewer real designs say too little
# Tested on 40 real open-source schematics (which should come out mostly clean): a warning
# needs strong agreement in enough designs and a pin-level part (decoupling, pull-up/down,
# a tie); series parts on signal pins depend on the application (LEDs on a 74HC595), so
# those, and weaker agreement, are notes only.
WARN_SHARE, WARN_DESIGNS = 0.75, 8
WARN_KINDS = ("C -> ", "R -> ")   # decoupling and pull-ups/downs; diodes, ferrites etc. are topology choices
NOTE_SHARE = 0.6
VALUE_SHARE, VALUE_DESIGNS = 0.6, 5

WHAT = {"R": "a resistor", "C": "a capacitor", "L": "an inductor", "FB": "a ferrite bead",
        "D": "a diode", "Y": "a crystal"}


def _describe(sh):
    """'C -> GND' -> 'a capacitor to GND'."""
    if sh.startswith("tied to "):
        return sh
    kind, _, dest = sh.partition(" -> ")
    to = {"GND": "to GND", "supply": "to the supply", "signal": "in series", "?": ""}.get(dest, "to " + dest[4:]
                                                                                           if dest.startswith("own pin")
                                                                                           else dest)
    return ("%s %s" % (WHAT.get(kind, kind), to)).strip()


def _kb_entry(kb, comp):
    """The knowledge-base entry for exactly this part (by part name, value or symbol), else
    (None, None). No fuzzy matching: another chip's statistics would be wrong advice."""
    parts, aliases = kb.get("parts", {}), kb.get("aliases", {})
    for q in (comp.get("part"), comp.get("value")):
        if q and q.strip().upper() in parts:
            return q.strip().upper(), parts[q.strip().upper()]
    sym = ("%s:%s" % (comp.get("lib"), comp.get("part"))).upper()
    for q in (sym, sym.split(":")[-1]):
        if q in aliases and aliases[q] in parts:
            return aliases[q], parts[aliases[q]]
    return None, None


def inspect(rec, kb=None):
    """[(level, text)] findings for an extracted schematic (wiring.read_netlist)."""
    kb = kb or knowledge.load() or {}
    if not kb.get("parts"):
        return [("info", "no knowledge base found; only basic checks ran")]
    comps = {c["ref"]: c for c in rec["components"]}
    mine = {}                                        # ref -> {pin label: patterns}
    for ref, key, pin, pats in wiring.pin_patterns(rec):
        mine.setdefault(ref, {})[pin.upper()] = pats
    findings, compared = [], []
    for ref in sorted(mine, key=lambda r: (wiring.ref_prefix(r), int(re.sub(r"\D", "", r) or 0))):
        key, info = _kb_entry(kb, comps[ref])
        if not info or info["designs"] < MIN_DESIGNS or not info.get("shapes"):
            continue
        n = info["designs"]
        compared.append("%s %s (%d designs)" % (ref, key, n))
        patterns = {p.upper(): v for p, v in info["pins"].items()}
        for pin, ranked in sorted((p.upper(), v) for p, v in info["shapes"].items()):
            if pin not in mine[ref]:
                continue
            pats = mine[ref][pin]
            have = {wiring.shape(p) for p in pats} - {None}
            for sh, count in ranked:
                share = count / float(n)
                if share < NOTE_SHARE or sh == "not connected" or sh in have:
                    continue
                strong = share >= WARN_SHARE and n >= WARN_DESIGNS
                if sh.startswith("tied to"):
                    if "not connected" in have:
                        findings.append(("warn" if strong else "info",
                                         "%s %s pin %s is unconnected; %d of %d real designs tie it %s" % (
                                             ref, key, pin, count, n, sh[5:])))
                    continue
                level = ("warn" if strong and sh.startswith(WARN_KINDS) and not sh.endswith(("-> signal", "-> ?"))
                         else "info")
                thing = _describe(sh)                    # "a capacitor to GND"
                if "not connected" in have:
                    text = "%s %s pin %s is unconnected; %d of %d real designs put %s there" % (
                        ref, key, pin, count, n, thing)
                else:
                    text = "%s %s pin %s has no %s; %d of %d real designs have one" % (
                        ref, key, pin, thing.split(" ", 1)[1], count, n)
                findings.append((level, text))
            # same part present, value far from what designs use
            for pattern, count in patterns.get(pin, []):
                sh = wiring.shape(pattern)
                if (count / float(n) < VALUE_SHARE or n < VALUE_DESIGNS or not sh or sh not in have
                        or sh.startswith(("tied", "not")) or "?" in pattern):
                    continue
                if pattern not in pats:
                    mine_vals = sorted(p.split()[1] for p in pats if wiring.shape(p) == sh)
                    if not mine_vals or any("?" in v or "e+" in v for v in mine_vals):
                        continue          # unparseable values ("600R@100MHz" ferrites, unitless caps)
                    findings.append(("info", "%s %s pin %s: %s here; real designs mostly use %s (%d of %d)" % (
                        ref, key, pin, ", ".join(mine_vals), pattern.split()[1], count, n)))
    findings = _dedupe(findings)
    head = ("compared %d part(s) with real designs: %s" % (len(compared), "; ".join(compared))) if compared else \
        "none of the chips here are in the knowledge base yet (it covers parts seen in 3+ open-source designs)"
    return [("ok" if compared else "info", head)] + findings


def _dedupe(findings):
    seen, out = set(), []
    for f in findings:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out
