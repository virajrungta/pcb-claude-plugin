"""Design spec loading, pin resolution and validation.

The spec is a JSON document (see skills/design/references/spec-format.md).
"""

import json
import os
import re

from . import fplib, kienv, symlib


DEFAULT_RULES = {
    "clearance": 0.2,
    "track": 0.25,
    "power_track": 0.5,
    "via_diameter": 0.6,
    "via_drill": 0.3,
    "edge_clearance": 0.5,
    "zone_clearance": 0.3,
}


class SpecError(Exception):
    pass


# Continuous dissipation (W) a package handles on a typical 2-layer board without a heatsink.
PACKAGE_WATTS = [("SOT-23", 0.35), ("SC-70", 0.25), ("SOT-353", 0.25), ("SOT-363", 0.25), ("SOT-89", 0.6),
                 ("SOT-223", 1.0), ("TO-252", 1.5), ("DPAK", 1.5), ("TO-263", 2.0), ("D2PAK", 2.0),
                 ("TO-220", 2.0), ("DFN", 1.0), ("QFN", 1.0), ("SOIC", 0.6), ("MSOP", 0.5), ("TSOT", 0.35)]


def rail_voltage(name, overrides=None):
    """Nominal voltage of a supply net from its name (+3V3, 5V, VBUS, +1V8...), or None."""
    n = name.lstrip("/")
    if overrides and n in overrides:
        return float(overrides[n])
    u = n.upper().lstrip("+")
    m = re.match(r"^(\d+)V(\d*)$", u) or re.match(r"^(\d+)\.(\d+)V$", u)
    if m:
        return float("%s.%s" % (m.group(1), m.group(2) or "0"))
    m = re.match(r"^V(\d+)V(\d*)$", u) or re.match(r"^VCC_?(\d+)V(\d*)$", u)
    if m:
        return float("%s.%s" % (m.group(1), m.group(2) or "0"))
    return {"VBUS": 5.0, "VUSB": 5.0, "USB_5V": 5.0, "VBAT": 3.7}.get(u)


def package_watts(footprint_id):
    name = footprint_id.split(":", 1)[-1].upper()
    for key, watts in PACKAGE_WATTS:
        if key in name:
            return key, watts
    return None, 0.5


def strip_markup(name):
    return re.sub(r"~\{([^}]*)\}", r"\1", name).replace("~", "")


class Component(object):
    def __init__(self, d, sym, fp):
        self.d = d
        self.ref = d["ref"]
        self.sym = sym
        self.fp = fp
        self.value = d.get("value") or (sym.props.get("Value") if sym else "") or ""
        self.place = d.get("place") or {}
        self.group = d.get("group", "")

    @property
    def symbol_id(self):
        return self.d["symbol"]

    @property
    def footprint_id(self):
        return self.d.get("footprint") or (self.sym.footprint if self.sym else "")


class Design(object):
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.dir = os.path.dirname(self.path)
        with open(self.path) as f:
            try:
                self.raw = json.load(f)
            except ValueError as e:
                raise SpecError("spec is not valid JSON: %s" % e)
        from . import blocks
        try:
            self.raw, self.block_summary = blocks.expand(self.raw)
        except blocks.BlockError as e:
            raise SpecError(str(e))
        self.support = self.raw.pop("_support", [])     # advanced blocks: set expectations up front
        r = self.raw
        self.name = r.get("name") or os.path.splitext(os.path.basename(path))[0]
        if not re.match(r"^[A-Za-z0-9_\-]+$", self.name):
            raise SpecError("name must match [A-Za-z0-9_-]+")
        self.board = r.get("board", {})
        self.rules = dict(DEFAULT_RULES)
        self.rules.update(r.get("rules", {}))
        pn = r.get("power_nets", [])
        self.power_nets = pn.split() if isinstance(pn, str) else list(pn)
        libs = r.get("libraries", {})
        self.sym_libs = kienv.lib_table("sym", self.dir, _rel(libs.get("symbols", {}), self.dir))
        self.fp_libs = kienv.lib_table("fp", self.dir, _rel(libs.get("footprints", {}), self.dir))
        self.errors = []
        self.warnings = []
        self.components = []
        self.by_ref = {}
        self.nets = {}          # net name -> [(ref, pin_number)]
        self.pin_net = {}       # (ref, pin_number) -> net
        self.no_connect = set()
        self.auto_classes = []
        self._load()
        self._fit_tracks_to_pitch()
        self._support_levels()

    def _support_levels(self):
        """Fine-pitch chips outside blocks get the same up-front note as advanced blocks."""
        layers = int(self.board.get("layers", 2))
        noted = " ".join(self.support)
        for c in self.components:
            if c.fp is None or len(c.fp.pads) < 32 or c.ref.rstrip("0123456789") not in ("U", "IC"):
                continue
            p = c.fp.pitch()
            if p and p <= 0.42 and layers <= 2 and c.value.lower() not in noted.lower():
                self.support.append("%s (%s) is advanced: %.1f mm-pitch pins on 2 layers; autorouting may leave a "
                                    "few connections near it (4 layers or hand-finishing in KiCad)" % (c.ref, c.value, p))

    def _fit_tracks_to_pitch(self):
        """Nets that touch fine-pitch pads (QFN-56 at 0.4 mm, LQFP at 0.5 mm...) get a netclass
        that can reach those pads, using the clearance and track width real boards use at that
        pitch (knowledge base); otherwise the router can't connect them at all."""
        from . import knowledge
        user_nets = {n for nc in self.raw.get("net_classes", []) for n in nc.get("nets", [])}
        user_rules = self.raw.get("rules", {})
        gnd = self.board.get("ground_pour", "GND")
        floor = 0.127                                   # JLCPCB / PCBWay standard minimum
        base_clr = self.rules["clearance"]
        need = {}                                       # net -> (fine clearance, max width there)
        for c in self.components:
            if c.fp is None or not c.fp.track_limits(base_clr):
                continue
            learned = knowledge.pitch_rules(c.fp.pitch() or 9.0) or {}
            typical = (learned.get("track_at_pad_mm") or {}).get("p50")
            clr = (learned.get("clearance_mm") or {}).get("p50")
            clr = base_clr if (not clr or "clearance" in user_rules) else min(base_clr, max(floor, clr))
            at_base = c.fp.track_limits(base_clr)
            at_fine = c.fp.track_limits(clr)
            for num, lim in at_base.items():
                net = self.pin_net.get((c.ref, num))
                if not net or net in user_nets:
                    continue
                power = net in self.power_nets and net != gnd
                width = self.rules["power_track"] if power else self.rules["track"]
                if lim >= width:
                    continue                            # the normal rules already reach this pad
                w = at_fine.get(num, lim)
                if typical and typical < w:
                    w = typical                         # what designers use there, if narrower
                old = need.get(net)
                need[net] = (min(clr, old[0]) if old else clr, min(w, old[1]) if old else w)
        snap = lambda w: max(floor, int(w / 0.005 + 1e-6) * 0.005)
        # unused pins of those chips get the same (smaller) clearance, or a track entering a pin
        # would still have to keep the default clearance from its unused neighbours
        fine_refs = sorted({ref for ref, num in self.pin_net if self.pin_net[(ref, num)] in need})
        groups = {}
        for net, (clr, w) in sorted(need.items()):
            power = net in self.power_nets and net != gnd
            groups.setdefault("PowerFine" if power else "Fine", []).append((net, clr, w))
        for name, rows in sorted(groups.items()):
            if name == "Fine" and "track" in user_rules:
                continue
            w = min(r[2] for r in rows)
            clr = min(r[1] for r in rows)
            cls = {"name": name, "track": round(snap(w), 3), "nets": [r[0] for r in rows]}
            if clr < base_clr:
                cls["clearance"] = round(clr, 3)
            if w < floor:
                cls["clearance"] = floor                # pitch too tight even at the minimum width
            if name == "Fine":
                cls["patterns"] = ["unconnected-(%s-*" % r for r in fine_refs]
            self.auto_classes.append(cls)

    # ------------------------------------------------------------ loading
    def _load(self):
        comps = self.raw.get("components")
        if not comps:
            raise SpecError("spec has no components")
        seen = set()
        for d in comps:
            ref = d.get("ref")
            if not ref or not re.match(r"^[A-Za-z]+[0-9]+$", ref):
                self.errors.append("component %r: ref must look like R1, U3, J2" % ref)
                continue
            if ref in seen:
                self.errors.append("duplicate ref %s" % ref)
                continue
            seen.add(ref)
            if "symbol" not in d:
                self.errors.append("%s: missing 'symbol'" % ref)
                continue
            try:
                sym = symlib.load(self.sym_libs, d["symbol"])
            except KeyError as e:
                self.errors.append("%s: %s (use `kipcb sym-search`)" % (ref, e.args[0]))
                continue
            fp = None
            fp_id = d.get("footprint") or sym.footprint
            if not fp_id:
                self.errors.append("%s: no footprint given and symbol has no default "
                                   "(filters: %s)" % (ref, sym.props.get("ki_fp_filters", "-")))
            else:
                try:
                    fp = fplib.load(self.fp_libs, fp_id)
                except KeyError as e:
                    self.errors.append("%s: %s (use `kipcb fp-search`)" % (ref, e.args[0]))
            c = Component(d, sym, fp)
            if fp is not None:
                c.d["footprint"] = fp_id
                self._check_fp(c)
            self.components.append(c)
            self.by_ref[ref] = c

        for net, pins in (self.raw.get("nets") or {}).items():
            if isinstance(pins, str):          # compact form: "J1.VBUS U1.VI C1.1"
                pins = pins.split()
            if not re.match(r"^[^\s{}]+$", net):
                self.errors.append("net %r: names cannot contain spaces or braces" % net)
                continue
            for p in pins:
                for key in self._resolve(p, net):
                    if key in self.pin_net and self.pin_net[key] != net:
                        self.errors.append("pin %s.%s is in both %s and %s" % (key[0], key[1], self.pin_net[key], net))
                        continue
                    if key in self.pin_net:
                        continue
                    self.pin_net[key] = net
                    self.nets.setdefault(net, []).append(key)
        nc = self.raw.get("no_connect", [])
        for p in (nc.split() if isinstance(nc, str) else nc):
            for key in self._resolve(p, "no_connect"):
                if key in self.pin_net:
                    self.errors.append("pin %s.%s is marked no_connect but is on net %s" % (key[0], key[1], self.pin_net[key]))
                self.no_connect.add(key)
        for n in self.power_nets:
            if n not in self.nets:
                self.warnings.append("power net %s has no pins" % n)
        self.notes = []

    def _resolve(self, ref_pin, ctx):
        if "." not in ref_pin:
            self.errors.append("%s: %r must be REF.PIN (e.g. U1.3 or U1.VCC)" % (ctx, ref_pin))
            return []
        ref, pin = ref_pin.split(".", 1)
        c = self.by_ref.get(ref)
        if c is None:
            if ref in [d.get("ref") for d in self.raw.get("components", [])]:
                return []  # component failed to load; already reported
            self.errors.append("%s: unknown component %s" % (ctx, ref))
            return []
        pins = c.sym.pins
        by_num = [p for p in pins if p["number"] == pin]
        if by_num:
            return [(ref, pin)]
        for matcher in (lambda p: p["name"] == pin,
                        lambda p: strip_markup(p["name"]).lower() == pin.lower(),
                        lambda p: pin.lower() in [s.lower() for s in strip_markup(p["name"]).split("/")]):
            hits = sorted({p["number"] for p in pins if matcher(p)})
            if hits:
                return [(ref, n) for n in hits]
        names = ", ".join("%s=%s" % (p["number"], strip_markup(p["name"])) for p in pins[:40])
        self.errors.append("%s: %s has no pin %r. Pins: %s" % (ctx, ref, pin, names))
        return []

    def _check_fp(self, c):
        pads = set(c.fp.pad_numbers())
        pins = {p["number"] for p in c.sym.pins if p["type"] != "no_connect" or p["number"] in pads}
        missing = sorted(pins - pads, key=fplib._natkey)
        if missing:
            self.errors.append("%s: symbol pins %s have no pad in footprint %s (pads: %s)" % (
                c.ref, ",".join(missing), c.footprint_id, ",".join(sorted(pads, key=fplib._natkey))))
        extra = sorted(pads - {p["number"] for p in c.sym.pins}, key=fplib._natkey)
        if c.d.get("footprint_checked"):
            return                # catalog part: symbol/footprint pairing already verified
        if extra:
            self.warnings.append("%s: footprint pads %s have no symbol pin (left unconnected)" % (c.ref, ",".join(extra)))
        filters = c.sym.props.get("ki_fp_filters", "").split()
        fname = c.footprint_id.split(":", 1)[-1]
        if filters and not any(_fp_filter_match(f, fname, c.footprint_id) for f in filters):
            self.warnings.append("%s: footprint %s does not match symbol's filters (%s) -- double-check the package" % (
                c.ref, c.footprint_id, " ".join(filters)))

    # ------------------------------------------------------------ checks
    def validate(self):
        """Run electrical sanity checks. Fills self.errors / self.warnings."""
        for c in self.components:
            for p in c.sym.pins:
                key = (c.ref, p["number"])
                if key in self.pin_net or key in self.no_connect or p["type"] == "no_connect":
                    continue
                if p["hidden"] and p["type"] == "power_in":
                    continue
                self.errors.append("%s pin %s (%s, %s) is unconnected: add it to a net or to no_connect" % (
                    c.ref, p["number"], strip_markup(p["name"]) or "~", p["type"]))
        for net, pins in self.nets.items():
            if len(pins) == 1:
                self.warnings.append("net %s has only one pin (%s.%s)" % (net, pins[0][0], pins[0][1]))
            types = [self.pin_type(k) for k in pins]
            outs = [k for k, t in zip(pins, types) if t in ("output", "power_out")]
            if len(outs) > 1 and net not in self.power_nets:
                self.warnings.append("net %s has multiple outputs driving it: %s" % (net, _fmt(outs)))
            if "power_in" in types and not any(t == "power_out" for t in types) and net not in self.power_nets:
                self.warnings.append("net %s feeds power_in pins but is not in power_nets and has no power_out" % net)
        if not self.errors:
            self.electrical_checks()
        return not self.errors

    # ------------------------------------------------------------ electrical
    def supply_of(self, comp, rails):
        """The single supply voltage an IC runs from, or None (unknown or several)."""
        vs = set()
        for p in comp.sym.pins:
            if p["type"] != "power_in":
                continue
            net = self.pin_net.get((comp.ref, p["number"]))
            if net and net in rails:
                vs.add(rails[net])
        return vs.pop() if len(vs) == 1 else None

    def electrical_checks(self):
        """Logic-level compatibility and power budget / regulator heat. Fills warnings and notes."""
        overrides = self.raw.get("rails") or {}
        rails = {}
        for net in self.nets:
            v = rail_voltage(net, overrides)
            if v is not None and v > 0:
                rails[net] = v

        # logic levels: chips on different supplies driving one signal net
        chip = lambda c: c.ref.rstrip("0123456789") in ("U", "IC", "M", "A")
        for net, pins in sorted(self.nets.items()):
            if net in self.power_nets or net in rails:
                continue
            sides = {}
            for ref, num in pins:
                c = self.by_ref[ref]
                if not chip(c) or self.pin_type((ref, num)) in ("passive", "power_in", "power_out", "no_connect"):
                    continue
                v = self.supply_of(c, rails)
                if v is not None:
                    sides.setdefault(v, set()).add(ref)
            if len(sides) > 1 and max(sides) - min(sides) > 0.6:
                desc = " and ".join("%s (%gV)" % (", ".join(sorted(refs)), v) for v, refs in sorted(sides.items()))
                self.warnings.append("logic levels: net %s connects %s; check the lower-voltage part is "
                                     "tolerant of %gV, or add a level shifter" % (net, desc, max(sides)))

        # capacitor voltage ratings (catalog parts carry the rating of their LCSC part)
        for c in self.components:
            rating = c.d.get("voltage_rating")
            if not rating:
                continue
            vmax = max([rails.get(self.pin_net.get((c.ref, p["number"])), 0) for p in c.sym.pins] or [0])
            if vmax > 0.8 * float(rating):
                self.warnings.append("%s (%s, rated %gV) sits on a %gV rail; use a higher-voltage part "
                                     "(rating at least 1.25x the rail)" % (c.ref, c.value, float(rating), vmax))

        # power budget per rail, from component current_ma
        loads = {}
        any_current = False
        for c in self.components:
            ma = c.d.get("current_ma")
            if ma is None:
                continue
            any_current = True
            supplies = {self.pin_net.get((c.ref, p["number"])) for p in c.sym.pins if p["type"] == "power_in"}
            supplies = [n for n in supplies if n in rails]
            for n in supplies[:1]:
                loads[n] = loads.get(n, 0.0) + float(ma)
        if not any_current:
            self.notes.append("power budget: add \"current_ma\" to the main loads (MCU, radio, LEDs, "
                              "motors) to check regulator heat and rail current")
            return
        for c in self.components:
            outs = [self.pin_net.get((c.ref, p["number"])) for p in c.sym.pins if p["type"] == "power_out"]
            ins = [self.pin_net.get((c.ref, p["number"])) for p in c.sym.pins if p["type"] == "power_in"]
            outs = [n for n in outs if n in rails]
            ins = [n for n in ins if n in rails and n not in outs]
            if not outs or not ins:
                continue
            vout, vin = rails[outs[0]], max(rails[n] for n in ins)
            if vin <= vout:
                continue
            amps = loads.get(outs[0], 0.0) / 1000.0   # its own quiescent current is already on the input
            loads[ins[0]] = loads.get(ins[0], 0.0) + amps * 1000.0   # regulator draws its load from the input
            switching = "Switching" in c.symbol_id or "switch" in c.sym.props.get("Description", "").lower()
            if switching:
                self.notes.append("power: %s (%s) supplies %s with %.0f mA (switching regulator)" % (
                    c.ref, c.value, outs[0], amps * 1000))
                continue
            watts = (vin - vout) * amps
            pkg, limit = package_watts(c.footprint_id)
            line = "%s (%s) %gV->%gV at %.0f mA dissipates %.2f W; %s handles about %.1f W" % (
                c.ref, c.value, vin, vout, amps * 1000, watts, pkg or "this package", limit)
            if watts > limit:
                self.warnings.append("regulator heat: %s. Use a bigger package, a lower input voltage or a "
                                     "switching regulator" % line)
            elif watts > 0.75 * limit:
                self.warnings.append("regulator heat: %s (close to the limit; add copper around it)" % line)
            else:
                self.notes.append("power: " + line)
        for net, ma in sorted(loads.items()):
            self.notes.append("rail %s carries about %.0f mA" % (net, ma))

    def pin_type(self, key):
        c = self.by_ref[key[0]]
        for p in c.sym.pins:
            if p["number"] == key[1]:
                return p["type"]
        return "unspecified"

    def net_needs_flag(self, net):
        """Power nets without a power_out pin need a PWR_FLAG for ERC."""
        if net not in self.power_nets:
            return False
        return not any(self.pin_type(k) == "power_out" for k in self.nets.get(net, []))

    def summary(self):
        lines = ["design %s: %d components, %d nets" % (self.name, len(self.components), len(self.nets))]
        return "\n".join(lines)


def _fp_filter_match(pattern, name, full):
    # KiCad filters use * and ?; library authors often write '?' where zero chars also occur
    rx = "^" + "".join(".*" if ch == "*" else ".?" if ch == "?" else re.escape(ch) for ch in pattern) + "$"
    target = full if ":" in pattern else name
    return re.match(rx, target, re.I) is not None


def _rel(d, base):
    return {k: (v if os.path.isabs(v) or v.startswith("$") else os.path.join(base, v)) for k, v in d.items()}


def _fmt(keys):
    return ", ".join("%s.%s" % k for k in keys)
