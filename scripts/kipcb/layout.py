"""Layout rules from application notes and manufacturer guides, checked on the placed board.

Each finding cites its rule id and source, so a warning can be traced and weighed:
  USB  TI SLVA680 (ESD layout), Microchip AN26.2 (USB 2.0), Raspberry Pi RP2040 guide
  XT   ST AN2867 (oscillators), Espressif hardware design guidelines
  SW   ROHM 66AN015E check sheet, TI AN-1229 (SIMPLE SWITCHER layout)
  TH   Sensirion humidity/temperature design guide, Bosch BME280 mounting notes (distances heuristic)
  RF   Espressif ESP32 / ESP32-S3 PCB layout design
  PL / ME  placement grouping and mechanical keep-outs (JLCPCB placement guide; heuristics marked)

Runs before routing (preflight) on geometry + netlist only. FAIL only where a board is
likely not to work (USB ESD far from the connector, a buck hot loop spread out); the rest
are warnings with the fix.
"""

import math
import re

from .spec import strip_markup

FAIL, WARN = "FAIL", "WARN"

ESD_HINT = re.compile(r"ESD|USBLC|TPD\d|PRTR|SRV05|IP42\d\d|TVS|SP0503", re.I)
TEMP_SENSOR = re.compile(r"BME\d|BMP\d|SHT\d|SHTC|AHT\d|HDC\d|TMP1\d|Si70|HTU\d|DHT|MCP98|LM75|DS18|Sensor_Humidity", re.I)
USB_DATA = re.compile(r"^(D[+-]|DP|DN|DM|USB_?D[PNM+-]|UD[+-])$", re.I)


def _mm(v):
    return v / 1e6


def _box_gap(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _bbox(item):
    b = item.GetBoundingBox()
    return (_mm(b.GetX()), _mm(b.GetY()), _mm(b.GetRight()), _mm(b.GetBottom()))


class _Board(object):
    """Design (netlist, pin names) joined with the placed board (positions)."""

    def __init__(self, design, board):
        self.d = design
        self.fps = {fp.GetReference(): fp for fp in board.GetFootprints()}
        self.comps = {c.ref: c for c in design.components}
        self.gnd = design.board.get("ground_pour", "GND")
        bb = board.GetBoardEdgesBoundingBox()
        self.edge = (_mm(bb.GetX()), _mm(bb.GetY()), _mm(bb.GetRight()), _mm(bb.GetBottom()))
        self._pin_names = {}
        for c in design.components:
            for p in c.sym.pins:
                self._pin_names[(c.ref, p["number"])] = strip_markup(p["name"] or "")

    def kind(self, ref):
        m = re.match(r"^[A-Za-z]+", ref)
        return m.group(0).upper() if m else ""

    def nets_of(self, ref):
        return {n for (r, num), n in self.d.pin_net.items() if r == ref}

    def pins_on(self, net):
        return self.d.nets.get(net, [])

    def pad(self, ref, num):
        fp = self.fps.get(ref)
        return fp.FindPadByNumber(num) if fp is not None else None

    def pad_box(self, ref, num):
        p = self.pad(ref, num)
        return _bbox(p) if p is not None else None

    def part_box(self, ref):
        fp = self.fps.get(ref)
        if fp is None:
            return None
        b = fp.GetBoundingBox(False, False)
        return (_mm(b.GetX()), _mm(b.GetY()), _mm(b.GetRight()), _mm(b.GetBottom()))

    def part_gap(self, a, b):
        ba, bb = self.part_box(a), self.part_box(b)
        return _box_gap(ba, bb) if ba and bb else None

    def pin_gap(self, ref_a, net, ref_b):
        """Smallest pad gap between ref_a's and ref_b's pads on one net."""
        best = None
        for (r1, n1) in self.pins_on(net):
            if r1 != ref_a:
                continue
            for (r2, n2) in self.pins_on(net):
                if r2 != ref_b:
                    continue
                a, b = self.pad_box(r1, n1), self.pad_box(r2, n2)
                if a and b:
                    g = _box_gap(a, b)
                    best = g if best is None else min(best, g)
        return best

    def edge_gap(self, ref):
        b = self.part_box(ref)
        if not b:
            return None
        x0, y0, x1, y1 = self.edge
        return max(0.0, min(b[0] - x0, b[1] - y0, x1 - b[2], y1 - b[3]))

    def text(self, ref):
        c = self.comps.get(ref)
        return "%s %s %s" % (c.value, c.symbol_id, c.footprint_id) if c else ""

    def is_ic(self, ref):
        c = self.comps.get(ref)
        return self.kind(ref) in ("U", "IC") and c is not None and len(c.sym.pins) >= 6

    def big_ic(self, ref):
        c = self.comps.get(ref)
        return self.is_ic(ref) and (len(c.sym.pins) >= 24 or "RF_Module" in c.symbol_id)


def check(design, board):
    """[(rule id, level, message, fix)] for the placed board."""
    B = _Board(design, board)
    out = []
    for rule in (_usb, _crystal, _buck, _thermal, _rf_module, _orphans, _input_protection, _mounting_holes):
        try:
            out += rule(B)
        except Exception as e:          # a rule must never break preflight
            out.append(("--", WARN, "layout rule %s could not run: %s" % (rule.__name__.strip("_"), e), None))
    return [(r, lvl, msg + _real_boards(r, board), fix) for r, lvl, msg, fix in out]


# rule -> the pair distance real boards were measured on (kipcb.placestats.pairs)
REAL_PAIR = {"USB-02": "esd_connector", "XT-02": "crystal_cap", "SW-07": "bootstrap"}


def _real_boards(rule, board):
    """' (real boards: half within X mm, 90% within Y mm)' from the knowledge base, if known."""
    key = REAL_PAIR.get(rule)
    if not key:
        return ""
    try:
        from . import knowledge
        kb = knowledge.placement() or {}
        ref = kb.get(str(min(board.GetCopperLayerCount(), 4))) or {}
        d = (ref.get("pairs_mm") or {}).get(key) or (kb.get("all", {}).get("pairs_mm") or {}).get(key)
    except Exception:
        return ""
    if not d or d.get("n", 0) < 20:
        return ""
    return " [real boards: half within %.1f mm, 90%% within %.1f mm]" % (d["p50"], d["p90"])


# ------------------------------------------------------------------ USB

def _usb(B):
    out = []
    for cref, c in B.comps.items():
        if B.kind(cref) not in ("J", "USB", "P", "CN"):
            continue
        data = {B.d.pin_net.get((cref, p["number"])) for p in c.sym.pins if USB_DATA.match(strip_markup(p["name"] or ""))}
        data.discard(None)
        if not data:
            continue
        mcu = None
        for net in data:
            for (r, num) in B.pins_on(net):
                if r != cref and B.big_ic(r):
                    mcu = r
        # ESD protection (USB-02)
        for eref in B.comps:
            if eref == cref or not ESD_HINT.search(B.text(eref)) or not (B.nets_of(eref) & data):
                continue
            d = min(filter(None, [B.pin_gap(eref, n, cref) for n in data]), default=None)
            if d is None:
                d = B.part_gap(eref, cref)
            if d is None:
                continue
            if d > 10:
                lvl = FAIL
            elif d > 5:
                lvl = WARN
            else:
                lvl = None
            if lvl:
                out.append(("USB-02", lvl, "USB ESD protection %s is %.1f mm from %s's data pins (aim < 5 mm: "
                            "it must catch the discharge right at the connector; TI SLVA680)" % (eref, d, cref),
                            "{\"place\": {\"near\": \"%s\"}} on %s" % (cref, eref)))
            if mcu:
                dm = B.part_gap(eref, mcu)
                if dm is not None and dm < B.part_gap(eref, cref) - 1.0:
                    out.append(("USB-02", WARN, "%s sits closer to the MCU %s than to the connector %s; ESD "
                                "protection belongs at the connector (TI SLVA680)" % (eref, mcu, cref), None))
        # series resistors at the MCU (USB-04)
        if mcu:
            for net in data:
                for (r, num) in B.pins_on(net):
                    if B.kind(r) != "R":
                        continue
                    other = [n for n in B.nets_of(r) if n != net]
                    # the resistor's far side goes to the MCU USB pin
                    for n2 in other:
                        if any(rr == mcu for rr, _ in B.pins_on(n2)):
                            g = B.pin_gap(r, n2, mcu)
                            if g is not None and g > 5:
                                out.append(("USB-04", WARN, "USB series resistor %s is %.1f mm from %s's USB pin "
                                            "(aim < 3 mm; RP2040 hardware design guide)" % (r, g, mcu),
                                            "{\"place\": {\"near\": \"%s\"}} on %s" % (mcu, r)))
        # CC pull-downs near a USB-C connector (USB-06)
        for p in c.sym.pins:
            if strip_markup(p["name"] or "").upper() not in ("CC1", "CC2"):
                continue
            net = B.d.pin_net.get((cref, p["number"]))
            for (r, num) in B.pins_on(net) if net else []:
                if B.kind(r) == "R":
                    g = B.pin_gap(r, net, cref)
                    if g is not None and g > 10:
                        out.append(("USB-06", WARN, "USB-C CC resistor %s is %.1f mm from %s (keep < 10 mm)" % (
                            r, g, cref), "{\"place\": {\"near\": \"%s\"}} on %s" % (cref, r)))
    return out


# ------------------------------------------------------------------ crystal

def _crystal(B):
    out = []
    for yref in B.comps:
        if B.kind(yref) not in ("Y", "X") or B.kind(yref) == "X" and not re.search("Crystal", B.text(yref), re.I):
            continue
        xnets = [n for n in B.nets_of(yref) if n and n != B.gnd]
        dists = []
        for net in xnets:
            for (r, num) in B.pins_on(net):
                if B.kind(r) == "C" and B.gnd in B.nets_of(r):
                    g = B.pin_gap(r, net, yref)
                    if g is not None:
                        dists.append((g, r))
                if B.kind(r) == "R":
                    # series resistor (RP2040's 1k on XOUT): at the chip side (XT-04)
                    for n2 in B.nets_of(r):
                        if n2 == net:
                            continue
                        for (ic, _) in B.pins_on(n2):
                            if B.is_ic(ic):
                                g = B.pin_gap(r, n2, ic)
                                if g is not None and g > 3:
                                    out.append(("XT-04", WARN, "crystal series resistor %s is %.1f mm from %s "
                                                "(put it at the chip's XOUT pin; RP2040 guide, ST AN2867)" % (r, g, ic),
                                                "{\"place\": {\"near\": \"%s\"}} on %s" % (ic, r)))
        for g, r in dists:
            if g > 2.5:
                out.append(("XT-02", WARN, "load cap %s is %.1f mm from crystal %s (aim < 2 mm; ST AN2867)" % (
                    r, g, yref), "{\"place\": {\"near\": \"%s\"}} on %s" % (yref, r)))
        if len(dists) >= 2:
            ds = sorted(d for d, _ in dists)
            if ds[-1] - ds[0] > 1.5:
                out.append(("XT-02", WARN, "crystal %s's load caps are placed unevenly (%.1f vs %.1f mm); keep "
                            "them symmetric (ST AN2867)" % (yref, ds[0], ds[-1]), None))
    return out


# ------------------------------------------------------------------ switching regulators

def _pin_net_by_name(B, ref, names):
    c = B.comps[ref]
    for p in c.sym.pins:
        if strip_markup(p["name"] or "").upper() in names:
            net = B.d.pin_net.get((ref, p["number"]))
            if net:
                return net, p["number"]
    return None, None


def _buck(B):
    out = []
    for uref, c in B.comps.items():
        if not B.is_ic(uref):
            continue
        sw, _ = _pin_net_by_name(B, uref, {"SW", "LX", "PH", "SW1"})
        if not sw or "Regulator_Switching" not in c.symbol_id and not re.search("buck|step.?down", c.sym.props.get("Description", ""), re.I):
            continue
        vin, _ = _pin_net_by_name(B, uref, {"VIN", "IN", "PVIN", "VCC"})
        # SW-01: input cap hot loop
        if vin:
            loops = []
            for (r, _) in B.pins_on(vin):
                if B.kind(r) == "C" and B.gnd in B.nets_of(r):
                    a = B.pin_gap(r, vin, uref)
                    b = B.pin_gap(r, B.gnd, uref)
                    if a is not None and b is not None:
                        loops.append((a + b, r))
            if loops:
                best, r = min(loops)
                if best > 10:
                    out.append(("SW-01", FAIL, "buck %s's input loop (%s to VIN and GND) is %.1f mm; keep the input "
                                "cap straddling VIN and GND, < 6 mm in total (ROHM, TI AN-1229)" % (uref, r, best),
                                "{\"place\": {\"near\": \"%s.%s\"}} on %s" % (uref, "VIN", r)))
                elif best > 6:
                    out.append(("SW-01", WARN, "buck %s's input loop (%s) is %.1f mm; aim < 6 mm (ROHM)" % (
                        uref, r, best), "{\"place\": {\"near\": \"%s\"}} on %s" % (uref, r)))
        # SW-07: bootstrap cap
        bst, _ = _pin_net_by_name(B, uref, {"BST", "BOOT", "CB", "BS", "BOOST"})
        if bst:
            for (r, _) in B.pins_on(bst):
                if B.kind(r) == "C":
                    g = B.pin_gap(r, bst, uref)
                    if g is not None and g > 3:
                        out.append(("SW-07", WARN, "bootstrap cap %s is %.1f mm from %s's BST pin (aim < 2 mm)" % (
                            r, g, uref), "{\"place\": {\"near\": \"%s\"}} on %s" % (uref, r)))
        # SW-06: feedback divider at FB, away from the inductor
        fb, _ = _pin_net_by_name(B, uref, {"FB", "VFB", "ADJ"})
        inductors = [r for (r, _) in B.pins_on(sw) if B.kind(r) == "L"]
        if fb in set(B.d.power_nets) | {B.gnd}:
            fb = None            # fixed-output part: FB senses the rail itself, there's no divider
        if fb:
            for (r, _) in B.pins_on(fb):
                if B.kind(r) != "R":
                    continue
                g = B.pin_gap(r, fb, uref)
                if g is not None and g > 4:
                    out.append(("SW-06", WARN, "feedback resistor %s is %.1f mm from %s's FB pin (aim < 4 mm; "
                                "the FB node picks up noise; ROHM)" % (r, g, uref),
                                "{\"place\": {\"near\": \"%s\"}} on %s" % (uref, r)))
                for L in inductors:
                    gl = B.part_gap(r, L)
                    if gl is not None and gl < 1.0:
                        out.append(("SW-06", WARN, "feedback resistor %s touches inductor %s; keep FB parts away "
                                    "from the inductor (TI AN-1229)" % (r, L), None))
        # SW-08: switch node / inductor away from crystals and sensors
        for L in inductors:
            for vref in B.comps:
                if B.kind(vref) == "Y" or TEMP_SENSOR.search(B.text(vref)):
                    g = B.part_gap(L, vref)
                    if g is not None and g < 9.8:
                        out.append(("SW-08", WARN, "inductor %s (switch node of %s) is %.1f mm from %s; keep "
                                    "switching parts >= 10 mm from crystals and sensors (ROHM, ST AN2867)" % (
                                        L, uref, g, vref), "{\"place\": {\"away_from\": [\"%s\"]}} on %s" % (L, vref)))
    return out


# ------------------------------------------------------------------ heat-sensitive sensors

def _thermal(B):
    out = []
    for sref in B.comps:
        if B.kind(sref) not in ("U", "IC") or not TEMP_SENSOR.search(B.text(sref)):
            continue
        for oref, c in B.comps.items():
            if oref == sref:
                continue
            hot = None
            limit = 0
            if B.big_ic(oref):
                hot, limit = "the MCU/module", 10
            elif "Regulator_" in c.symbol_id or B.kind(oref) == "L":
                hot, limit = "a regulator/inductor", 15
            elif B.kind(oref) in ("LED",) or "LED" in c.symbol_id:
                hot, limit = "an LED", 10
            if not hot:
                continue
            g = B.part_gap(sref, oref)
            if g is not None and g < limit - 0.2:
                out.append(("TH-03", WARN, "temperature/humidity sensor %s is %.1f mm from %s %s (heat skews the "
                            "reading; keep >= %d mm, heuristic from Sensirion/Bosch guidance)" % (sref, g, hot, oref, limit),
                            "{\"place\": {\"away_from\": [\"%s\"], \"min_dist\": %d}} on %s, or a board edge" % (
                                oref, limit, sref)))
    return out


# ------------------------------------------------------------------ RF modules

def _rf_module(B):
    out = []
    for mref, c in B.comps.items():
        if "RF_Module" not in c.symbol_id:
            continue
        g = B.edge_gap(mref)
        if g is not None and g > 1.0:
            out.append(("RF-01", WARN, "radio module %s is %.1f mm from the board edge; its antenna end should sit "
                        "at (or over) the edge with no copper under it (Espressif PCB layout guide)" % (mref, g),
                        "{\"place\": {\"edge\": \"top\"}} on %s" % mref))
    return out


# ------------------------------------------------------------------ grouping

def _orphans(B):
    """PL-02: a passive serving exactly one IC should sit near that IC."""
    out = []
    power = set(B.d.power_nets) | {B.gnd}
    far = []
    for pref in B.comps:
        if B.kind(pref) not in ("R", "C", "L", "FB"):
            continue
        signal = [n for n in B.nets_of(pref) if n and n not in power]
        owners = set()
        for n in signal:
            for (r, _) in B.pins_on(n):
                if r != pref and B.is_ic(r):
                    owners.add(r)
                elif r != pref and B.kind(r) not in ("R", "C", "L", "FB", "TP"):
                    owners.add("*")          # also touches a connector/other part: not an orphan case
        if len(owners) != 1 or "*" in owners:
            continue
        ic = owners.pop()
        g = B.part_gap(pref, ic)
        if g is not None and g > 10:
            far.append((g, pref, ic))
    for g, pref, ic in sorted(far, reverse=True)[:4]:
        out.append(("PL-02", WARN, "%s only serves %s but sits %.1f mm away; support parts belong next to their "
                    "chip (keep < 10 mm)" % (pref, ic, g), "{\"place\": {\"near\": \"%s\"}} on %s" % (ic, pref)))
    return out


def _input_protection(B):
    """PL-04: fuse / input TVS / reverse-polarity part next to the power connector."""
    out = []
    for jref, c in B.comps.items():
        if B.kind(jref) not in ("J", "USB", "P", "CN"):
            continue
        pwr = {B.d.pin_net.get((jref, p["number"])) for p in c.sym.pins
               if re.match(r"^(VBUS|VIN|V\+|\+?\d+V\d*|VCC|PWR)$", strip_markup(p["name"] or ""), re.I)}
        pwr.discard(None)
        for net in pwr:
            for (r, _) in B.pins_on(net):
                text = B.text(r)
                if B.kind(r) == "F" or B.kind(r) == "D" and re.search("TVS|SMAJ|SMBJ|SS[0-9]|Schottky", text, re.I):
                    g = B.part_gap(r, jref)
                    if g is not None and g > 10:
                        out.append(("PL-04", WARN, "input protection %s is %.1f mm from power connector %s; put "
                                    "it right after the connector (keep < 10 mm)" % (r, g, jref),
                                    "{\"place\": {\"near\": \"%s\"}} on %s" % (jref, r)))
    return out


def _mounting_holes(B):
    """ME-01: screw heads and washers need a clear ring around the hole (M3: 3.5 mm radius)."""
    out = []
    holes = [(ref, fp) for ref, fp in B.fps.items() if "MountingHole" in fp.GetFPIDAsString()]
    for href, hfp in holes:
        hb = B.part_box(href)
        cx, cy = (hb[0] + hb[2]) / 2, (hb[1] + hb[3]) / 2
        hole_r = (hb[2] - hb[0]) / 2
        keep = max(hole_r + 1.0, 3.5 if hole_r > 1.4 else 3.0)       # M3+ : 3.5 mm, M2/M2.5: 3.0 mm
        close = []
        for ref in B.comps:
            b = B.part_box(ref)
            if not b:
                continue
            dx = max(b[0] - cx, 0, cx - b[2])
            dy = max(b[1] - cy, 0, cy - b[3])
            if math.hypot(dx, dy) < keep:
                close.append(ref)
        if close:
            out.append(("ME-01", WARN, "%s inside mounting hole %s's screw-head keep-out (%.1f mm radius)" % (
                ", ".join(close[:4]), href, keep), "move them out, or use a smaller hole size"))
    return out
