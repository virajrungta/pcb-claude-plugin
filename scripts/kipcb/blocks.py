"""Prebuilt parts and circuit blocks, expanded into a spec before validation.

  {"ref": "R3", "part": "R0603", "value": "10k"}
      -> symbol, footprint (and an LCSC number for common values) filled in.

  "blocks": [{"use": "ldo_ams1117_3v3", "connect": {"VIN": "VBUS"}}]
      -> the block's parts (renumbered to fit), its internal nets, and its
         ports joined to the spec's nets. "omit": ["R1"] drops optional parts
         (e.g. a bus terminator) by their reference inside the block.

Sources: data/parts.json and data/blocks.json shipped with the plugin, plus
parts remembered from your own ready-to-order boards (see remember()).
"""

import json
import os
import re
import time

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "R": 1, "": 1}


class BlockError(Exception):
    pass


_JSON_CACHE = {}


def _load_json_cached(path):
    """parts.json / blocks.json / the parts memory, parsed once per process (re-read if changed)."""
    try:
        key = os.stat(path).st_mtime
    except OSError:
        return {}
    hit = _JSON_CACHE.get(path)
    if hit is None or hit[0] != key:
        hit = (key, _load_json(path))
        _JSON_CACHE[path] = hit
    return hit[1]


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def builtin_parts():
    return {k: v for k, v in _load_json_cached(os.path.join(DATA, "parts.json")).items() if not k.startswith("_")}


def builtin_blocks():
    return {k: v for k, v in _load_json_cached(os.path.join(DATA, "blocks.json")).items() if not k.startswith("_")}


def memory_path():
    from . import kienv
    return os.path.join(kienv.data_dir(), "parts_memory.json")


def remembered():
    return _load_json_cached(memory_path())


def si_value(v):
    """'10k' / '4k7' / '100nF' / '0.1u' -> float, or None."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([pnuµmkKMR]?)(\d*)", str(v or ""))
    if not m:
        return None
    num = float(m.group(1) + ("." + m.group(3) if m.group(3) and m.group(2) else ""))
    return num * SI.get(m.group(2), 1)


def lookup_part(name):
    """Catalog entry for a part name (case-insensitive; HEADER_1x04 style patterns), or None."""
    parts = builtin_parts()
    key = name.upper()
    for k, v in parts.items():
        if k.upper() == key:
            return dict(v)
    m = re.match(r"^HEADER_([12])X(\d{1,2})$", key)
    if m:
        tpl = parts["HEADER_%sxNN" % m.group(1)]
        nn = "%02d" % int(m.group(2))
        return {k: (v.replace("NN", nn) if isinstance(v, str) else v) for k, v in tpl.items()}
    for k, v in remembered().items():
        if v.get("value", "").upper() == key:
            return dict(v)
    return None


def expand_part(comp):
    """Fill symbol/footprint/value/lcsc/current_ma of a component that names a catalog part."""
    name = comp.get("part")
    if not name:
        return comp
    entry = lookup_part(name)
    if entry is None:
        raise BlockError("%s: unknown part %r (see `kipcb parts`)" % (comp.get("ref", "?"), name))
    out = dict(comp)
    for key in ("symbol", "footprint"):
        if key not in out and entry.get(key):
            out[key] = entry[key]
    if out.get("footprint") == entry.get("footprint") and entry.get("footprint"):
        out["footprint_checked"] = True       # catalog pairing, verified against KiCad's libraries
    if "value" not in out and entry.get("value"):
        out["value"] = entry["value"]
    if "current_ma" not in out and "current_ma" in entry:
        out["current_ma"] = entry["current_ma"]
    lcsc = entry.get("lcsc")
    if "lcsc" not in out and lcsc:
        if isinstance(lcsc, str):
            out["lcsc"] = lcsc
        else:
            key = match_value(out.get("value"), lcsc)
            if key:
                out["lcsc"] = lcsc[key]
                if "voltage_rating" not in out and key in entry.get("volts", {}):
                    out["voltage_rating"] = entry["volts"][key]
    return out


def match_value(value, table):
    """Key of `table` matching a component value: '10k' ~ '10000', '100nF' ~ '0.1u', 'Red LED' ~ 'red'."""
    want = si_value(value)
    text = str(value or "").lower()
    for key in table:
        have = si_value(key)
        if want is not None and have is not None and abs(have - want) <= abs(want) * 1e-6:
            return key
    for key in table:
        if si_value(key) is None and re.search(r"\b%s\b" % re.escape(key.lower()), text):
            return key
    return None


def _prefix(ref):
    return re.match(r"^[A-Za-z]+", ref).group(0)


def _subst(obj, params):
    if isinstance(obj, str):
        return re.sub(r"\{(\w+)\}", lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), obj)
    if isinstance(obj, list):
        return [_subst(x, params) for x in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, params) for k, v in obj.items()}
    return obj


def _pins(v):
    return v.split() if isinstance(v, str) else list(v)


def expand(raw):
    """Return (expanded spec dict, summary lines). The input is not modified."""
    spec = dict(raw)
    comps = [expand_part(c) for c in raw.get("components", [])]
    nets = {k: _pins(v) for k, v in (raw.get("nets") or {}).items()}
    nc = _pins(raw.get("no_connect", []))
    pn = raw.get("power_nets", [])
    power = _pins(pn)
    used = {c.get("ref") for c in comps}
    counters = {}
    summary = []
    catalog = builtin_blocks()

    def next_ref(prefix):
        n = counters.get(prefix, 0)
        while True:
            n += 1
            if "%s%d" % (prefix, n) not in used:
                counters[prefix] = n
                used.add("%s%d" % (prefix, n))
                return "%s%d" % (prefix, n)

    names = set()
    deferred = []
    for idx, inst in enumerate(raw.get("blocks", [])):
        use = inst.get("use")
        if use not in catalog:
            raise BlockError("block %r not found (see `kipcb blocks`)" % use)
        b = catalog[use]
        name = inst.get("name") or (use if use not in names else "%s%d" % (use, idx + 1))
        if name in names:
            raise BlockError("two blocks are named %r; give each a unique \"name\"" % name)
        names.add(name)
        params = dict(b.get("params", {}))
        params.update(inst.get("params", {}))
        body = _subst({k: b.get(k) for k in ("components", "nets", "no_connect")}, params)
        ports = [n[1:] for n in body["nets"] if n.startswith("@")]
        connect = dict(b.get("defaults", {}))
        connect.update(inst.get("connect", {}))
        unknown = [p for p in connect if p not in ports and p not in b.get("defaults", {})]
        if unknown:
            raise BlockError("block %s (%s) has no port %s; ports: %s" % (name, use, ", ".join(unknown), " ".join(ports)))
        optional = set(b.get("optional", []))

        omit = set(inst.get("omit", []))
        local = {c["ref"] for c in body["components"]}
        if omit - local:
            raise BlockError("block %s (%s) has no part %s to omit; parts: %s" % (
                name, use, ", ".join(sorted(omit - local)), " ".join(sorted(local))))
        body["components"] = [c for c in body["components"] if c["ref"] not in omit]
        kept = lambda pin: pin.partition(".")[0] not in omit
        body["nets"] = {k: [p for p in _pins(v) if kept(p)] for k, v in body["nets"].items()}
        body["nets"] = {k: v for k, v in body["nets"].items() if v}
        body["no_connect"] = [p for p in _pins(body.get("no_connect") or []) if kept(p)]

        mapping = {}
        for c in body["components"]:
            mapping[c["ref"]] = next_ref(_prefix(c["ref"]))

        def remap(pin):
            ref, _, rest = pin.partition(".")
            return mapping.get(ref, ref) + ("." + rest if rest else "")

        for c in body["components"]:
            c = expand_part(dict(c))
            c["ref"] = mapping[c["ref"]]
            c.setdefault("group", name)
            if isinstance(c.get("place"), dict) and "near" in c["place"]:
                if c["place"]["near"].partition(".")[0] in omit:
                    c["place"] = {k: v for k, v in c["place"].items() if k != "near"}
                else:
                    c["place"] = dict(c["place"], near=remap(c["place"]["near"]))
            c.pop("part", None)
            comps.append(c)

        for net, pins in body["nets"].items():
            pins = [remap(p) for p in _pins(pins)]
            if net.startswith("@"):
                port = net[1:]
                target = connect.get(port, None if port in optional else port)
                if target is None and port in b.get("autojoin", []):
                    deferred.append((name, port, pins))  # joins a same-named net if one turns up
                    continue
                if target in (None, ""):
                    if len(pins) == 1:
                        nc += pins                      # unused single pin: deliberate no-connect
                    else:
                        nets["%s_%s" % (name, port)] = pins   # still joins parts inside the block
                    continue
                nets.setdefault(target, []).extend(pins)
                if port in b.get("power", []) and target not in power:
                    power.append(target)
            else:
                nets["%s_%s" % (name, net)] = pins
                if net in b.get("power", []):
                    power.append("%s_%s" % (name, net))   # internal supply, e.g. a driver's own regulator
        nc += [remap(p) for p in _pins(body.get("no_connect") or [])]
        label = name if name == use else "%s (%s)" % (name, use)
        summary.append("%s: %s" % (label, " ".join(mapping[r] for r in mapping)))
        if b.get("support") == "advanced":
            spec.setdefault("_support", []).append("%s is advanced: %s" % (label, b.get("support_note", "")))

    # optional bus ports (USB_DP, SWDIO, NRST...) join a net of the same name made by the spec or
    # another block, or each other when two blocks offer the same one; otherwise they stay unused
    for bname, port, pins in deferred:
        if port in nets or sum(1 for d in deferred if d[1] == port) > 1:
            nets.setdefault(port, []).extend(pins)
        elif len(pins) == 1:
            nc += pins
        else:
            nets["%s_%s" % (bname, port)] = pins
    spec["components"] = comps
    spec["nets"] = nets
    spec["no_connect"] = nc
    spec["power_nets"] = power
    spec.pop("blocks", None)
    return spec, summary


def remember(design):
    """Add a ready-to-order board's parts to the personal catalog (used counts, last use)."""
    path = memory_path()
    mem = remembered()
    for c in design.components:
        d = c.d
        if c.ref.rstrip("0123456789") in ("R", "C", "L", "FB"):
            continue      # passives are covered by R0603/C0603-style parts
        key = "%s|%s|%s" % (d.get("symbol"), c.footprint_id, c.value)
        e = mem.get(key) or {"symbol": d.get("symbol"), "footprint": c.footprint_id, "value": c.value, "uses": 0}
        for k in ("lcsc", "mpn", "current_ma"):
            if d.get(k) is not None:
                e[k] = d[k]
        e["uses"] = e.get("uses", 0) + 1
        e["last"] = time.strftime("%Y-%m-%d")
        mem[key] = e
    from . import paths
    paths.save_json(path, mem)
