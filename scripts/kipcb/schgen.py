"""Generate a KiCad 9 schematic (.kicad_sch) from a validated Design.

Layout strategy: every symbol unit is placed on a grid; each connected pin
gets a short wire stub ending in a net label (or a power symbol for power
nets). This produces a clean, fully-connected, ERC-checkable schematic that
humans can later tidy up in Eeschema without breaking the netlist.
"""

import datetime
import uuid

from . import sexp, symlib
from .sexp import Sym

GRID = 2.54
STUB = 2.54
FONT = [Sym("font"), [Sym("size"), 1.27, 1.27]]
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420), ("A1", 841, 594), ("A0", 1189, 841)]


def _r(v):
    return round(v, 4)


def _snap(v):
    return round(round(v / GRID) * GRID, 4)


class SchBuilder(object):
    def __init__(self, design):
        self.d = design
        self.ns = uuid.uuid5(uuid.NAMESPACE_URL, "kipcb:" + design.name)
        self.root_uuid = self.uid("root")
        self.items = []
        self.lib_symbols = {}
        self.pwr_n = 0
        self.flag_n = 0
        self.symbol_uuids = {}  # ref -> uuid of unit 1 (for PCB linking)

    def uid(self, key):
        return str(uuid.uuid5(self.ns, key))

    # ------------------------------------------------------------ helpers
    def _use_lib(self, sym):
        if sym.lib_id not in self.lib_symbols:
            self.lib_symbols[sym.lib_id] = sym.node

    def _power_symbol(self, net):
        for cand in (net, net.replace(".", "V") if "." in net else None):
            if not cand:
                continue
            try:
                s = symlib.load(self.d.sym_libs, "power:" + cand)
                if s.power:
                    return s
            except KeyError:
                pass
        return None

    @staticmethod
    def _outward(angle):
        """Direction (dx, dy) in schematic coords pointing away from the body."""
        a = angle % 360
        return {0: (-1, 0), 90: (0, 1), 180: (1, 0), 270: (0, -1)}.get(a, (-1, 0))

    # ------------------------------------------------------------ layout
    def _block(self, comp, unit):
        """Extents of a unit incl. stubs and labels, relative to symbol origin."""
        sym = comp.sym
        x0, y0, x1, y1 = sym.bbox(unit)
        # lib Y-up -> sch Y-down
        ext = [x0, -y1, x1, -y0]
        for p in sym.pins_for_unit(unit):
            key = (comp.ref, p["number"])
            net = self.d.pin_net.get(key)
            if not net:
                continue
            dx, dy = self._outward(p["angle"])
            px, py = p["x"], -p["y"]
            reach = STUB + (6.0 if net in self.d.power_nets else 1.5 + 1.1 * len(net))
            ex, ey = px + dx * reach, py + dy * reach
            ext = [min(ext[0], ex), min(ext[1], ey), max(ext[2], ex), max(ext[3], ey)]
        # room for reference/value text above & below
        return (ext[0] - 2, ext[1] - 5, ext[2] + 2, ext[3] + 5)

    def layout(self):
        blocks = []
        order = []
        groups = {}
        for c in self.d.components:
            groups.setdefault(c.group, []).append(c)
            if c.group not in order:
                order.append(c.group)
        for g in order:
            for c in groups[g]:
                for unit in sorted(c.sym.units):
                    blocks.append((c, unit, self._block(c, unit), g))
        gap = 5.08
        for paper, pw, ph in PAPERS:
            ml, mt, mr, mb = 20, 20, 20, 45
            usable_w, usable_h = pw - ml - mr, ph - mt - mb
            placed = []
            x, y, row_h = ml, mt, 0
            last_group = None
            ok = True
            for c, unit, (bx0, by0, bx1, by1), g in blocks:
                w, h = bx1 - bx0, by1 - by0
                newline = x + w > ml + usable_w or (last_group is not None and g != last_group and x > ml)
                if newline:
                    x, y = ml, y + row_h + gap
                    row_h = 0
                if y + h > mt + usable_h or w > usable_w:
                    ok = False
                    break
                placed.append((c, unit, _snap(x - bx0), _snap(y - by0)))
                x += w + gap
                row_h = max(row_h, h)
                last_group = g
            if ok:
                return paper, placed, (ml, y + row_h + gap, ml + usable_w, mt + usable_h)
        raise RuntimeError("design too large for a single A0 sheet")

    # ------------------------------------------------------------ emitters
    def _prop(self, name, val, x, y, hide=False, rot=0):
        eff = [Sym("effects"), FONT]
        if hide:
            eff.append([Sym("hide"), Sym("yes")])
        return [Sym("property"), name, val, [Sym("at"), _r(x), _r(y), rot], eff]

    def _place_symbol(self, sym, ref, value, x, y, unit, key, extra_props=None, footprint="", dnp=False,
                      rot=0, in_bom=True):
        self._use_lib(sym)
        u = self.uid(key)
        node = [Sym("symbol"), [Sym("lib_id"), sym.lib_id], [Sym("at"), _r(x), _r(y), rot],
                [Sym("unit"), unit], [Sym("exclude_from_sim"), Sym("no")],
                [Sym("in_bom"), Sym("yes" if in_bom else "no")], [Sym("on_board"), Sym("yes" if in_bom else "no")],
                [Sym("dnp"), Sym("yes" if dnp else "no")], [Sym("uuid"), u]]
        libprops = {str(p[1]): p for p in sexp.find_all(sym.node, "property")}

        def lp(name, dx, dy, hide):
            p = libprops.get(name)
            angle = 0
            if p is not None:
                at = sexp.find(p, "at")
                dx, dy = float(at[1]), -float(at[2])
                angle = int(float(at[3])) if len(at) > 3 else 0
                hide = hide or any(isinstance(e, list) and sexp.find(e, "hide") for e in p if isinstance(e, list) and e[0] == "effects")
            if rot:  # rotate text offset with the symbol (power symbols)
                import math
                a = math.radians(rot)
                dx, dy = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
                angle = 0
            return dx, dy, hide, angle

        dx, dy, h, ang = lp("Reference", 0, -3.81, False)
        node.append(self._prop("Reference", ref, x + dx, y + dy, h, ang))
        dx, dy, h, ang = lp("Value", 0, 3.81, False)
        node.append(self._prop("Value", value, x + dx, y + dy, h, ang))
        node.append(self._prop("Footprint", footprint, x, y, True))
        node.append(self._prop("Datasheet", sym.props.get("Datasheet", "~"), x, y, True))
        node.append(self._prop("Description", sym.props.get("Description", ""), x, y, True))
        for k, v in (extra_props or {}).items():
            node.append(self._prop(k, str(v), x, y, True))
        for p in sym.pins_for_unit(unit):
            node.append([Sym("pin"), p["number"], [Sym("uuid"), self.uid(key + "/pin/" + p["number"])]])
        node.append([Sym("instances"), [Sym("project"), self.d.name,
                                        [Sym("path"), "/" + self.root_uuid, [Sym("reference"), ref], [Sym("unit"), unit]]]])
        self.items.append(node)
        return u

    def _wire(self, x0, y0, x1, y1, key):
        self.items.append([Sym("wire"), [Sym("pts"), [Sym("xy"), _r(x0), _r(y0)], [Sym("xy"), _r(x1), _r(y1)]],
                           [Sym("stroke"), [Sym("width"), 0], [Sym("type"), Sym("default")]],
                           [Sym("uuid"), self.uid(key)]])

    def _label(self, net, x, y, dx, dy, key):
        angle = {(-1, 0): 180, (1, 0): 0, (0, -1): 90, (0, 1): 270}[(dx, dy)]
        just = [Sym("justify"), Sym("left" if angle in (0, 90) else "right"), Sym("bottom")]
        self.items.append([Sym("label"), net, [Sym("at"), _r(x), _r(y), angle],
                           [Sym("effects"), FONT, just], [Sym("uuid"), self.uid(key)]])

    def _power(self, psym, net, x, y, dx, dy, key):
        self.pwr_n += 1
        x0, y0, x1, y1 = psym.bbox(1)
        body_down = (y0 + y1) / 2 < 0  # GND-style symbols hang below their pin
        if body_down:
            rot = {(0, 1): 0, (0, -1): 180, (1, 0): 90, (-1, 0): 270}[(dx, dy)]
        else:
            rot = {(0, -1): 0, (0, 1): 180, (-1, 0): 90, (1, 0): 270}[(dx, dy)]
        self._place_symbol(psym, "#PWR%03d" % self.pwr_n, psym.props.get("Value", net), x, y, 1, key,
                           rot=rot, in_bom=False)

    def _no_connect(self, x, y, key):
        self.items.append([Sym("no_connect"), [Sym("at"), _r(x), _r(y)], [Sym("uuid"), self.uid(key)]])

    def _text(self, s, x, y, key, size=1.27):
        self.items.append([Sym("text"), s, [Sym("exclude_from_sim"), Sym("no")], [Sym("at"), _r(x), _r(y), 0],
                           [Sym("effects"), [Sym("font"), [Sym("size"), size, size]], [Sym("justify"), Sym("left"), Sym("bottom")]],
                           [Sym("uuid"), self.uid(key)]])

    # ------------------------------------------------------------ build
    def build(self):
        d = self.d
        paper, placed, free = self.layout()
        for c, unit, x, y in placed:
            extra = {}
            for k in ("mpn", "lcsc", "manufacturer"):
                if c.d.get(k):
                    extra[{"mpn": "MPN", "lcsc": "LCSC", "manufacturer": "Manufacturer"}[k]] = c.d[k]
            extra.update(c.d.get("fields", {}))
            key = "sym/%s/%d" % (c.ref, unit)
            u = self._place_symbol(c.sym, c.ref, c.value, x, y, unit, key, extra, c.footprint_id,
                                   dnp=bool(c.d.get("dnp")))
            if unit == min(c.sym.units):
                self.symbol_uuids[c.ref] = u
            drawn = set()
            for p in sorted(c.sym.pins_for_unit(unit), key=lambda p: p["hidden"]):
                pk = (c.ref, p["number"])
                if p["unit"] == 0 and unit != min(c.sym.units):
                    continue  # common pins: wire them once
                px, py = x + p["x"], y - p["y"]
                spot = (_r(px), _r(py))
                if spot in drawn:
                    continue  # stacked pin (e.g. hidden duplicate VBUS/GND) shares the visible pin's wiring
                drawn.add(spot)
                if pk in d.no_connect:
                    self._no_connect(px, py, "nc/%s/%s" % pk)
                    continue
                net = d.pin_net.get(pk)
                if not net:
                    continue
                dx, dy = self._outward(p["angle"])
                qx, qy = px + dx * STUB, py + dy * STUB
                self._wire(px, py, qx, qy, "w/%s/%s" % pk)
                psym = self._power_symbol(net) if net in d.power_nets else None
                if psym is not None:
                    self._power(psym, net, qx, qy, dx, dy, "pwr/%s/%s" % pk)
                else:
                    self._label(net, qx, qy, dx, dy, "lbl/%s/%s" % pk)
        # PWR_FLAGs for power nets with no power_out driver
        fx, fy = free[0], free[1] + 5
        flag_sym = None
        try:
            flag_sym = symlib.load(d.sym_libs, "power:PWR_FLAG")
        except KeyError:
            pass
        flagged = [n for n in d.power_nets if n in d.nets and d.net_needs_flag(n)]
        if flag_sym and flagged:
            self._text("Power flags (tell ERC these nets are driven from off-board / connectors)", fx, fy - 3,
                       "flagtext")
            for i, net in enumerate(flagged):
                x = _snap(fx + 5 + i * 22.86)
                y = _snap(fy + 7.62)
                self.flag_n += 1
                self._place_symbol(flag_sym, "#FLG%02d" % self.flag_n, "PWR_FLAG", x, y, 1, "flag/" + net,
                                   in_bom=False)
                self._wire(x, y, x, y + 5.08, "flagw/" + net)
                psym = self._power_symbol(net)
                if psym is not None:
                    self._power(psym, net, x, y + 5.08, 0, 1, "flagpwr/" + net)
                else:
                    self._label(net, x, y + 5.08, 0, 1, "flaglbl/" + net)

        title = self.d.raw.get("title", self.d.name)
        tree = [Sym("kicad_sch"), [Sym("version"), 20250114], [Sym("generator"), "eeschema"],
                [Sym("generator_version"), "9.0"], [Sym("uuid"), self.root_uuid], [Sym("paper"), paper],
                [Sym("title_block"), [Sym("title"), title],
                 [Sym("date"), datetime.date.today().isoformat()],
                 [Sym("rev"), str(self.d.raw.get("revision", "A"))],
                 [Sym("company"), str(self.d.raw.get("author", ""))],
                 [Sym("comment"), 1, "Generated by kipcb from %s" % self.d.name + ".json"]],
                [Sym("lib_symbols")] + [self.lib_symbols[k] for k in sorted(self.lib_symbols)]]
        tree += self.items
        tree += [[Sym("sheet_instances"), [Sym("path"), "/", [Sym("page"), "1"]]],
                 [Sym("embedded_fonts"), Sym("no")]]
        return sexp.dumps(tree) + "\n"


def write(design, path):
    b = SchBuilder(design)
    text = b.build()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return b
