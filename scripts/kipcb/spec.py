"""Design spec loading, pin resolution and validation.

The spec is a JSON document (see skills/design/references/spec-format.md).
"""

import json
import os
import re

from . import fplib, kienv, symlib

POWER_OUT_TYPES = ("power_out",)
DRIVER_TYPES = ("output", "power_out", "bidirectional", "tri_state", "open_collector", "open_emitter")

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
        r = self.raw
        self.name = r.get("name") or os.path.splitext(os.path.basename(path))[0]
        if not re.match(r"^[A-Za-z0-9_\-]+$", self.name):
            raise SpecError("name must match [A-Za-z0-9_-]+")
        self.board = r.get("board", {})
        self.rules = dict(DEFAULT_RULES)
        self.rules.update(r.get("rules", {}))
        self.power_nets = list(r.get("power_nets", []))
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
        self._load()

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
        for p in self.raw.get("no_connect", []):
            for key in self._resolve(p, "no_connect"):
                if key in self.pin_net:
                    self.errors.append("pin %s.%s is marked no_connect but is on net %s" % (key[0], key[1], self.pin_net[key]))
                self.no_connect.add(key)
        for n in self.power_nets:
            if n not in self.nets:
                self.warnings.append("power net %s has no pins" % n)

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
        return not self.errors

    def pin_type(self, key):
        c = self.by_ref[key[0]]
        for p in c.sym.pins:
            if p["number"] == key[1]:
                return p["type"]
        return "unspecified"

    def pin_name(self, key):
        c = self.by_ref[key[0]]
        for p in c.sym.pins:
            if p["number"] == key[1]:
                return strip_markup(p["name"])
        return ""

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
