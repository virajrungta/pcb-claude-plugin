"""Symbol library access: search index, loading, flattening and pin geometry."""

import copy
import json
import math
import os
import re

from . import kienv, sexp
from .sexp import Sym

_CHUNK = re.compile(r'^(?:\t| {2})\(symbol "((?:[^"\\]|\\.)*)"', re.M)
_PROP = re.compile(r'\(property "(Description|ki_keywords|Footprint|Value|ki_fp_filters)"\s+"((?:[^"\\]|\\.)*)"')
_EXT = re.compile(r'\(extends "((?:[^"\\]|\\.)*)"\)')

_file_cache = {}
_sym_cache = {}


def _read(path):
    if path not in _file_cache:
        with open(path, encoding="utf-8") as f:
            _file_cache[path] = f.read()
    return _file_cache[path]


# ---------------------------------------------------------------- index

def build_index(libs):
    """Return list of entries {lib, name, desc, kw, fp, filters}; cached on disk."""
    cache_path = os.path.join(kienv.cache_dir(), "symbol_index.json")
    try:
        with open(cache_path) as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    changed = False
    out = []
    for nick, path in sorted(libs.items()):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        key = nick + "|" + path
        ent = cache.get(key)
        if not ent or ent.get("mtime") != mtime:
            ent = {"mtime": mtime, "syms": _index_file(path)}
            cache[key] = ent
            changed = True
        for s in ent["syms"]:
            d = dict(s)
            d["lib"] = nick
            out.append(d)
    if changed:
        from . import paths
        paths.save_json(cache_path, cache, indent=None)
    return out


def _index_file(path):
    text = _read(path)
    starts = [(m.start(), m.group(1)) for m in _CHUNK.finditer(text)]
    syms = []
    raw = {}
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        chunk = text[pos:end]
        props = {k: v for k, v in _PROP.findall(chunk)}
        ext = _EXT.search(chunk[:300])
        npins = len(re.findall(r"\(pin (?!_)", chunk)) if not ext else None
        raw[name] = {"name": name, "desc": props.get("Description", ""), "kw": props.get("ki_keywords", ""),
                     "fp": props.get("Footprint", ""), "filters": props.get("ki_fp_filters", ""),
                     "extends": ext.group(1) if ext else None, "pins": npins}
    for name, s in raw.items():
        if s["extends"] and s["extends"] in raw:
            p = raw[s["extends"]]
            s["pins"] = p["pins"]
            s["fp"] = s["fp"] or p["fp"]
            s["filters"] = s["filters"] or p["filters"]
        syms.append(s)
    return syms


def search(libs, query, limit=40):
    """Rank symbols by how well lib/name/description/keywords match all query words."""
    words = [w.lower() for w in query.split() if w.strip()]
    results = []
    for e in build_index(libs):
        full = ("%s:%s" % (e["lib"], e["name"])).lower()
        hay = " ".join([full, e["desc"].lower(), e["kw"].lower()])
        if not all(w in hay for w in words):
            continue
        score = 0
        for w in words:
            if w == e["name"].lower():
                score += 50
            elif e["name"].lower().startswith(w):
                score += 20
            elif w in full:
                score += 10
            elif w in e["kw"].lower():
                score += 4
            else:
                score += 1
        score -= len(e["name"]) * 0.05
        results.append((score, e))
    results.sort(key=lambda t: -t[0])
    return [e for _, e in results[:limit]]


# ---------------------------------------------------------------- loading

class Symbol(object):
    def __init__(self, lib_id, node):
        self.lib_id = lib_id
        self.node = node  # flattened, top-level name == lib_id
        self.props = {}
        for p in sexp.find_all(node, "property"):
            self.props[str(p[1])] = str(p[2])
        self.power = sexp.find(node, "power") is not None
        self.pins = []
        self.units = set()
        self.unit_names = {}
        base = lib_id.split(":", 1)[1]
        for sub in sexp.find_all(node, "symbol"):
            m = re.match(r"^(.*)_(\d+)_(\d+)$", str(sub[1]))
            if not m:
                continue
            unit, style = int(m.group(2)), int(m.group(3))
            if style not in (0, 1):
                continue
            if unit > 0:
                self.units.add(unit)
            un = sexp.find(sub, "unit_name")
            if un is not None:
                self.unit_names[unit] = str(un[1])
            for pin in sexp.find_all(sub, "pin"):
                at = sexp.find(pin, "at")
                name = sexp.find(pin, "name")
                number = sexp.find(pin, "number")
                length = sexp.value(pin, "length", 0)
                hidden = (sexp.find(pin, "hide") is not None and sexp.value(pin, "hide", "yes") == "yes") \
                    or Sym("hide") in pin
                self.pins.append({
                    "number": str(number[1]) if number else "",
                    "name": str(name[1]) if name else "",
                    "type": str(pin[1]),
                    "unit": unit,
                    "x": float(at[1]), "y": float(at[2]),
                    "angle": int(float(at[3])) if len(at) > 3 else 0,
                    "length": float(length),
                    "hidden": hidden,
                })
        if not self.units:
            self.units = {1}
        self._base = base

    @property
    def footprint(self):
        return self.props.get("Footprint", "")

    def pins_for_unit(self, unit):
        return [p for p in self.pins if p["unit"] in (0, unit)]

    def bbox(self, unit):
        """Bounding box (xmin, ymin, xmax, ymax) in library coords (Y up) for a unit."""
        xs, ys = [], []
        for sub in sexp.find_all(self.node, "symbol"):
            m = re.match(r"^(.*)_(\d+)_(\d+)$", str(sub[1]))
            if not m or int(m.group(3)) not in (0, 1) or int(m.group(2)) not in (0, unit):
                continue
            for g in sub[2:]:
                if not isinstance(g, list):
                    continue
                if g[0] == "rectangle":
                    for k in ("start", "end"):
                        c = sexp.find(g, k)
                        xs.append(float(c[1])); ys.append(float(c[2]))
                elif g[0] in ("polyline", "bezier"):
                    pts = sexp.find(g, "pts")
                    for xy in sexp.find_all(pts or [], "xy"):
                        xs.append(float(xy[1])); ys.append(float(xy[2]))
                elif g[0] == "circle":
                    c = sexp.find(g, "center"); r = float(sexp.value(g, "radius", 0))
                    xs += [float(c[1]) - r, float(c[1]) + r]; ys += [float(c[2]) - r, float(c[2]) + r]
                elif g[0] == "arc":
                    for k in ("start", "mid", "end"):
                        c = sexp.find(g, k)
                        if c:
                            xs.append(float(c[1])); ys.append(float(c[2]))
        for p in self.pins_for_unit(unit):
            xs.append(p["x"]); ys.append(p["y"])
            a = math.radians(p["angle"])
            xs.append(p["x"] + p["length"] * math.cos(a)); ys.append(p["y"] + p["length"] * math.sin(a))
        if not xs:
            return (-2.54, -2.54, 2.54, 2.54)
        return (min(xs), min(ys), max(xs), max(ys))


def load(libs, lib_id):
    if lib_id in _sym_cache:
        return _sym_cache[lib_id]
    if ":" not in lib_id:
        raise KeyError("symbol id must be 'Library:Name', got %r" % lib_id)
    nick, name = lib_id.split(":", 1)
    if nick not in libs:
        raise KeyError("symbol library %r not found" % nick)
    node = _load_raw(libs[nick], name)
    if node is None:
        raise KeyError("symbol %r not found in library %r" % (name, nick))
    node = copy.deepcopy(node)
    node[1] = lib_id
    sym = Symbol(lib_id, node)
    _sym_cache[lib_id] = sym
    return sym


def _load_raw(path, name, depth=0):
    text = _read(path)
    raw = sexp.extract_toplevel(text, "symbol", name)
    if raw is None:
        return None
    node = sexp.loads(raw)
    parent = sexp.value(node, "extends")
    if parent is None or depth > 5:
        return node
    base = _load_raw(path, str(parent), depth + 1)
    if base is None:
        raise KeyError("parent symbol %r of %r missing" % (parent, name))
    return _flatten(copy.deepcopy(base), node, str(parent), name)


def _flatten(base, child, parent_name, name):
    base[1] = name
    child_props = {str(p[1]): p for p in sexp.find_all(child, "property")}
    out = []
    for item in base:
        if isinstance(item, list) and item and item[0] == "property" and str(item[1]) in child_props:
            out.append(child_props.pop(str(item[1])))
        elif isinstance(item, list) and item and item[0] == "symbol":
            sub = item
            sub[1] = re.sub("^" + re.escape(parent_name) + "_", name + "_", str(sub[1]))
            out.append(sub)
        else:
            out.append(item)
    # insert remaining child-only properties after the last property
    idx = max([i for i, it in enumerate(out) if isinstance(it, list) and it and it[0] == "property"] or [1])
    for p in child_props.values():
        idx += 1
        out.insert(idx, p)
    for key in ("pin_names", "pin_numbers", "in_bom", "on_board", "exclude_from_sim"):
        c = sexp.find(child, key)
        if c is not None:
            for i, it in enumerate(out):
                if isinstance(it, list) and it and it[0] == key:
                    out[i] = c
    return out
