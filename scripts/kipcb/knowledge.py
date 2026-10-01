"""Reference knowledge mined from open-source KiCad designs (built by pcb-knowledge).

`kipcb ref <part>` shows how real designs wire a part: which passives sit on
each pin, their values, and where they go. Layout statistics from real
boards also seed kipcb's local learning.
"""

import json
import os


def kb_path():
    env = os.environ.get("KIPCB_KNOWLEDGE")
    if env:
        return env
    base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base, "kipcb", "knowledge.json")


_cache = {}


def load(path=None):
    path = path or kb_path()
    if path in _cache:
        return _cache[path]
    try:
        with open(path) as f:
            kb = json.load(f)
    except (OSError, ValueError):
        kb = None
    _cache[path] = kb
    return kb


def find(kb, query):
    """Best-matching part keys for a symbol id, part name or value."""
    q = query.strip().upper()
    parts = kb.get("parts", {})
    if q in parts:
        return [q]
    if q in kb.get("aliases", {}):
        return [kb["aliases"][q]]
    tail = q.split(":")[-1]
    if tail in parts:
        return [tail]
    hits = [k for k in parts if tail in k or k in tail]
    return sorted(hits, key=lambda k: (-parts[k]["designs"], len(k)))[:5]


def print_part(query, path=None):
    kb = load(path)
    if kb is None:
        print("No knowledge base installed at %s.\nBuild one with the pcb-knowledge repo "
              "(pkb fetch / extract / build / install)." % (path or kb_path()))
        return 2
    keys = find(kb, query)
    if not keys:
        print("%r is not in the knowledge base (%d parts from %d open-source projects)." % (
            query, len(kb["parts"]), kb.get("projects", 0)))
        return 1
    key = keys[0]
    info = kb["parts"][key]
    n = info["designs"]
    print("%s: seen in %d open-source design repositories" % (key, n))
    if info.get("lib"):
        print("  symbol:     %s" % info["lib"])
    if info.get("footprints"):
        print("  footprints: %s" % ", ".join(info["footprints"]))
    print("  pin wiring (share of designs):")
    for pin, pats in sorted(info["pins"].items(), key=lambda kv: -kv[1][0][1]):
        txt = "; ".join("%s %d%%" % (p, round(100.0 * c / n)) for p, c in pats)
        print("    %-14s %s" % (pin, txt))
    if len(keys) > 1:
        print("  (also matched: %s)" % ", ".join(keys[1:]))
    print("Treat these as common practice, not a spec: the datasheet wins.")
    return 0


def layout_prior(layers=2):
    """Area per pad (mm²) at the 25th/50th percentile of real routed boards, or None."""
    kb = load()
    if not kb:
        return None
    d = (kb.get("layout", {}).get("area_per_pad_mm2") or {}).get(str(layers))
    if not d or d.get("n", 0) < 5:
        return None
    return d
