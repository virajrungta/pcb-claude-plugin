"""Footprint library access: search index and pad/courtyard extraction."""

import json
import os
import re

from . import kienv, sexp

_DESCR = re.compile(r'\(descr "((?:[^"\\]|\\.)*)"')
_TAGS = re.compile(r'\(tags "((?:[^"\\]|\\.)*)"')
_fp_cache = {}


def build_index(libs):
    cache_path = os.path.join(kienv.cache_dir(), "footprint_index.json")
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
            fps = []
            for fn in sorted(os.listdir(path)):
                if not fn.endswith(".kicad_mod"):
                    continue
                try:
                    with open(os.path.join(path, fn), encoding="utf-8") as f:
                        head = f.read(4000)
                except OSError:
                    continue
                d = _DESCR.search(head)
                t = _TAGS.search(head)
                fps.append({"name": fn[:-len(".kicad_mod")], "desc": d.group(1) if d else "",
                            "tags": t.group(1) if t else ""})
            ent = {"mtime": mtime, "fps": fps}
            cache[key] = ent
            changed = True
        for fp in ent["fps"]:
            d = dict(fp)
            d["lib"] = nick
            out.append(d)
    if changed:
        with open(cache_path, "w") as f:
            json.dump(cache, f)
    return out


def search(libs, query, limit=40):
    words = [w.lower() for w in query.split() if w.strip()]
    res = []
    for e in build_index(libs):
        full = ("%s:%s" % (e["lib"], e["name"])).lower()
        hay = " ".join([full, e["desc"].lower(), e["tags"].lower()])
        if not all(w in hay for w in words):
            continue
        score = sum(10 if w in full else 1 for w in words) - len(e["name"]) * 0.05
        res.append((score, e))
    res.sort(key=lambda t: -t[0])
    return [e for _, e in res[:limit]]


class Footprint(object):
    def __init__(self, fp_id, path, node):
        self.fp_id = fp_id
        self.path = path
        self.node = node
        self.descr = str(sexp.value(node, "descr", ""))
        attr = sexp.find(node, "attr")
        self.attrs = [str(a) for a in attr[1:]] if attr else []
        self.pads = []
        for pad in sexp.find_all(node, "pad"):
            at = sexp.find(pad, "at")
            size = sexp.find(pad, "size")
            layers = sexp.find(pad, "layers")
            self.pads.append({
                "number": str(pad[1]),
                "type": str(pad[2]),
                "shape": str(pad[3]),
                "x": float(at[1]), "y": float(at[2]),
                "w": float(size[1]) if size else 0.0, "h": float(size[2]) if size else 0.0,
                "layers": [str(l) for l in layers[1:]] if layers else [],
            })
        self.courtyard = self._bbox(("F.CrtYd", "B.CrtYd"))
        if self.courtyard is None:
            self.courtyard = self._pad_bbox(0.5)

    @property
    def smd(self):
        return "smd" in self.attrs

    def pad_numbers(self):
        return sorted({p["number"] for p in self.pads if p["number"]}, key=_natkey)

    def _pad_bbox(self, margin):
        if not self.pads:
            return (-1.0, -1.0, 1.0, 1.0)
        xs = [p["x"] - p["w"] / 2 for p in self.pads] + [p["x"] + p["w"] / 2 for p in self.pads]
        ys = [p["y"] - p["h"] / 2 for p in self.pads] + [p["y"] + p["h"] / 2 for p in self.pads]
        return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)

    def _bbox(self, layer_names):
        xs, ys = [], []
        for g in self.node:
            if not isinstance(g, list) or not g or not str(g[0]).startswith("fp_"):
                continue
            if str(sexp.value(g, "layer", "")) not in layer_names:
                continue
            if g[0] == "fp_circle":
                c = sexp.find(g, "center"); e = sexp.find(g, "end")
                r = ((float(e[1]) - float(c[1])) ** 2 + (float(e[2]) - float(c[2])) ** 2) ** 0.5
                xs += [float(c[1]) - r, float(c[1]) + r]; ys += [float(c[2]) - r, float(c[2]) + r]
                continue
            for k in ("start", "end", "mid", "center"):
                c = sexp.find(g, k)
                if c is not None:
                    xs.append(float(c[1])); ys.append(float(c[2]))
            pts = sexp.find(g, "pts")
            if pts:
                for xy in sexp.find_all(pts, "xy"):
                    xs.append(float(xy[1])); ys.append(float(xy[2]))
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))


def _natkey(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def load(libs, fp_id):
    if fp_id in _fp_cache:
        return _fp_cache[fp_id]
    if ":" not in fp_id:
        raise KeyError("footprint id must be 'Library:Name', got %r" % fp_id)
    nick, name = fp_id.split(":", 1)
    if nick not in libs:
        raise KeyError("footprint library %r not found" % nick)
    path = os.path.join(libs[nick], name + ".kicad_mod")
    if not os.path.exists(path):
        raise KeyError("footprint %r not found in library %r" % (name, nick))
    with open(path, encoding="utf-8") as f:
        node = sexp.loads(f.read())
    fp = Footprint(fp_id, path, node)
    _fp_cache[fp_id] = fp
    return fp
