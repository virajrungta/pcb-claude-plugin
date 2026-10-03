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
        from . import paths
        paths.save_json(cache_path, cache, indent=None)
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
            w = float(size[1]) if size else 0.0
            h = float(size[2]) if size else 0.0
            if len(at) > 3 and int(round(float(at[3]))) % 180 == 90:
                w, h = h, w                      # rotated pad: keep w/h in footprint axes
            self.pads.append({
                "number": str(pad[1]),
                "type": str(pad[2]),
                "shape": str(pad[3]),
                "x": float(at[1]), "y": float(at[2]),
                "w": w, "h": h,
                "layers": [str(l) for l in layers[1:]] if layers else [],
            })
        self.courtyard = self._bbox(("F.CrtYd", "B.CrtYd"))
        if self.courtyard is None:
            self.courtyard = self._pad_bbox(0.5)
        self.court_rects = self._court_rects(("F.CrtYd", "B.CrtYd")) or [self.courtyard]

    @property
    def smd(self):
        return "smd" in self.attrs

    def row_pairs(self):
        """Neighbouring copper pads in the same row, computed once per footprint:
        [(a, b, axis, distance)] with axis "x" (same y) or "y" (same x), skipping stacked or
        overlapping pads of one number. Shared by pitch(), track_limits() and preflight."""
        if getattr(self, "_row_pairs", None) is None:
            cu = [p for p in self.pads if p["number"] and any(l.endswith(".Cu") for l in p["layers"])]
            pairs = []
            for i, a in enumerate(cu):
                for b in cu[i + 1:]:
                    if a["number"] == b["number"]:
                        continue
                    dx, dy = abs(a["x"] - b["x"]), abs(a["y"] - b["y"])
                    if dy < 0.01 and dx > (a["w"] + b["w"]) / 2:
                        pairs.append((a, b, "x", dx))
                    elif dx < 0.01 and dy > (a["h"] + b["h"]) / 2:
                        pairs.append((a, b, "y", dy))
            self._row_pairs = pairs
        return self._row_pairs

    def track_limits(self, clearance, margin=0.01, clr_of=None):
        """{pad number: widest track that can reach that pad without breaking clearance to the
        next pad in its row}, for pads where the pitch is a constraint (QFN, LQFP, TSSOP...).
        clr_of maps pad number -> its net's clearance; the larger of the two neighbours' counts."""
        key = (clearance, margin) if clr_of is None else None
        cache = self.__dict__.setdefault("_limits", {})
        if key is not None and key in cache:
            return dict(cache[key])
        out = {}
        for a, b, axis, d in self.row_pairs():
            if d >= 1.5:
                continue
            clr = clearance
            if clr_of:
                clr = max(clr_of.get(a["number"], clearance), clr_of.get(b["number"], clearance))
            half_a = (a["w"] if axis == "x" else a["h"]) / 2
            half_b = (b["w"] if axis == "x" else b["h"]) / 2
            # a track entering pad a must clear pad b, and the other way round
            out[a["number"]] = min(out.get(a["number"], 9.0), 2 * (d - half_b - clr - margin))
            out[b["number"]] = min(out.get(b["number"], 9.0), 2 * (d - half_a - clr - margin))
        if key is not None:
            cache[key] = dict(out)
        return out

    def pitch(self):
        """Smallest centre distance between neighbouring pads in a row (None for 2-pad parts)."""
        if "_pitch" not in self.__dict__:
            ds = [d for _, _, _, d in self.row_pairs()]
            self._pitch = min(ds) if ds else None
        return self._pitch

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


    def _court_rects(self, layer_names):
        """Courtyard as a list of rectangles (exact for rectilinear outlines).

        Module footprints often have T/L-shaped courtyards (e.g. an antenna
        keep-out wider than the pins); a single bounding box would block the
        spots right next to the pins where decoupling caps belong."""
        segs = []
        for g in self.node:
            if not isinstance(g, list) or not g or str(sexp.value(g, "layer", "")) not in layer_names:
                continue
            kind = str(g[0])
            if kind in ("fp_arc", "fp_circle", "fp_curve"):
                return None
            if kind == "fp_line":
                a, b = sexp.find(g, "start"), sexp.find(g, "end")
                segs.append(((float(a[1]), float(a[2])), (float(b[1]), float(b[2]))))
            elif kind == "fp_rect":
                a, b = sexp.find(g, "start"), sexp.find(g, "end")
                x0, y0, x1, y1 = float(a[1]), float(a[2]), float(b[1]), float(b[2])
                segs += [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
            elif kind == "fp_poly":
                pts = [(float(xy[1]), float(xy[2])) for xy in sexp.find_all(sexp.find(g, "pts") or [], "xy")]
                segs += [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        if not segs or any(abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6 for a, b in segs):
            return None   # empty or not rectilinear
        xs = sorted({round(p[0], 4) for sg in segs for p in sg})
        ys = sorted({round(p[1], 4) for sg in segs for p in sg})
        if len(xs) > 40 or len(ys) > 40:
            return None

        def inside(px, py):
            hit = False
            for (x0, y0), (x1, y1) in segs:
                if abs(x0 - x1) < 1e-6 and (y0 > py) != (y1 > py) and px < x0:
                    hit = not hit
            return hit

        rows = []
        for j in range(len(ys) - 1):
            cy = (ys[j] + ys[j + 1]) / 2
            run = None
            for i in range(len(xs) - 1):
                if inside((xs[i] + xs[i + 1]) / 2, cy):
                    run = [xs[i], ys[j], xs[i + 1], ys[j + 1]] if run is None else [run[0], ys[j], xs[i + 1], ys[j + 1]]
                elif run is not None:
                    rows.append(run); run = None
            if run is not None:
                rows.append(run)
        merged = []
        for r in rows:   # stack rows with identical x-extent
            for m in merged:
                if abs(m[0] - r[0]) < 1e-6 and abs(m[2] - r[2]) < 1e-6 and abs(m[3] - r[1]) < 1e-6:
                    m[3] = r[3]
                    break
            else:
                merged.append(list(r))
        return [tuple(m) for m in merged] or None


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
