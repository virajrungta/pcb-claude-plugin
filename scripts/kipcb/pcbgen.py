"""Generate a placed (unrouted) KiCad board from a Design."""

import math
import re

from . import place
from . import ui
from .kicad import mm, pcbnew

ORIGIN = (50.0, 50.0)   # board top-left in KiCad page coordinates (mm)

HOLES = {"M2": ("MountingHole_2.2mm_M2", 2.2), "M2.5": ("MountingHole_2.7mm_M2.5", 2.7),
         "M3": ("MountingHole_3.2mm_M3", 3.2), "M4": ("MountingHole_4.3mm_M4", 4.3)}


def pcb_net_name(design, net, sch_builder):
    if net in design.power_nets and sch_builder._power_symbol(net) is not None:
        return net
    return "/" + net


def sexp_drills(fp):
    """Drill diameters (mm) of plated/unplated holes in a parsed footprint."""
    from . import sexp
    out = []
    for pad in sexp.find_all(fp.node, "pad"):
        d = sexp.find(pad, "drill")
        if d is not None:
            nums = [x for x in d[1:] if not isinstance(x, list) and str(x) != "oval"]
            if nums:
                out.append(float(nums[0]))
    return out


def unconnected_net_name(comp, number):
    name = ""
    for p in comp.sym.pins:
        if p["number"] == number:
            name = p["name"]
    name = name.replace("/", "{slash}").replace(" ", "{space}")
    if not name or name == "~":
        return "unconnected-(%s-Pad%s)" % (comp.ref, number)
    return "unconnected-(%s-%s-Pad%s)" % (comp.ref, name, number)


def _courtyard_area(design):
    """Sum of part courtyards including their spacing margins (mm²)."""
    area = 0.0
    for c in design.components:
        x0, y0, x1, y1 = c.fp.courtyard
        m = 2 * part_margin(design, c)
        area += (x1 - x0 + m) * (y1 - y0 + m)
    return area


def auto_sized(design):
    b = design.board
    return not (b.get("width") and b.get("height"))


def board_size(design):
    b = design.board
    if b.get("width") and b.get("height"):
        return float(b["width"]), float(b["height"])
    courtyard_area = area = _courtyard_area(design)
    area *= float(b.get("density_factor", 1.8))
    # learned sizing: the tightest area per pad that has routed on similar boards,
    # seeded by open-source boards from the knowledge base
    from . import knowledge, learn
    layers = int(b.get("layers", 2))
    pads = sum(len(c.fp.pads) for c in design.components)
    learned = learn.area_per_pad(layers, pads) if "density_factor" not in b else None
    if learned:
        area = max(learned * pads, courtyard_area * 1.25)
    else:
        prior = knowledge.layout_prior(layers)
        if prior and "density_factor" not in b:
            area = max(area, prior["p50"] * pads)
    if b.get("mounting_holes"):
        area += 4 * 7.0 * 7.0
    w = math.ceil(math.sqrt(area * 1.4)) + 4
    h = math.ceil(area / max(w - 4, 1)) + 4
    return float(b.get("width") or w), float(b.get("height") or h)


def hole_positions(design, W, H):
    mh = design.board.get("mounting_holes")
    if not mh:
        return None, []
    if isinstance(mh, dict) and "positions" in mh:
        size = mh.get("size", "M3")
        return size, [(float(p[0]), float(p[1])) for p in mh["positions"]]
    size = mh.get("size", "M3") if isinstance(mh, dict) else (mh if isinstance(mh, str) else "M3")
    inset = float(mh.get("inset", 3.5)) if isinstance(mh, dict) else 3.5
    return size, [(inset, inset), (W - inset, inset), (inset, H - inset), (W - inset, H - inset)]


def _resolve_near(design, hint):
    near = hint.get("near")
    if not near or "." not in near:
        return hint
    hint = dict(hint)
    ref, pin = near.split(".", 1)
    keys = design._resolve(near, "place")
    if keys:
        hint["near"] = "%s.%s" % (ref, keys[0][1])
    return hint


SPACING = {  # per-side courtyard margin (mm): passives, extra for ICs/fine-pitch parts
    "compact": (0.2, 0.2),
    "normal": (0.5, 0.6),
    "roomy": (0.9, 1.0),
}


def part_margin(design, comp):
    base, ic_extra = SPACING.get(design.board.get("spacing", "normal"), SPACING["normal"])
    npads = len({p["number"] for p in comp.fp.pads if p["number"]})
    is_ic = comp.ref.rstrip("0123456789") in ("U", "IC") and npads >= 6
    escape = 0.0
    if is_ic and npads >= 16:
        pitch = comp.fp.pitch() or 9.0
        escape = 0.8 if pitch <= 0.42 else 0.5 if pitch <= 0.52 else 0.0   # room to fan tracks out
    from . import learn   # footprints that caused unrouted connections before get more room
    return base + (ic_extra if is_ic else 0.0) + escape + learn.extra_margin(comp.footprint_id)


FILL_TARGET = 0.45     # share of the usable board the parts (with their spacing) should cover


def _spread(design, parts, W, H):
    """On a board with spare room, widen the spacing so the layout uses the board instead of
    packing into one corner (a dense cluster is what makes routing fail). Parts that must sit
    next to a pin (decoupling, crystal caps: anything with a "near" hint) keep their spacing;
    fine-pitch chips get the most extra room. Returns (mm added, fill before, fill after) or None."""
    if design.board.get("spacing") == "compact":
        return None
    edge = design.rules["edge_clearance"]
    usable = max(1.0, (W - 2 * edge) * (H - 2 * edge))

    def size(p):
        x0, y0, x1, y1 = p.court
        return x1 - x0, y1 - y0

    def weight(p):
        if p.hint.get("near"):
            return 0.0
        return 1.5 if p.npads >= 24 else 1.0

    def fill(delta):
        tot = 0.0
        for p in parts:
            w, h = size(p)
            m = p.margin + delta * weight(p)
            tot += (w + 2 * m) * (h + 2 * m)
        return tot / usable

    before = fill(0.0)
    if before >= FILL_TARGET:
        return None
    lo, hi = 0.0, 3.0
    if fill(hi) <= FILL_TARGET:
        lo = hi
    else:
        for _ in range(30):
            mid = (lo + hi) / 2
            if fill(mid) < FILL_TARGET:
                lo = mid
            else:
                hi = mid
    delta = round(lo, 2)
    if delta < 0.1:
        return None
    for p in parts:
        p.spread = delta * weight(p)
    after = sum((size(p)[0] + 2 * (p.margin + p.spread)) * (size(p)[1] + 2 * (p.margin + p.spread))
                for p in parts) / usable
    return delta, 100 * before, 100 * after


def _dependents(design):
    """How many parts asked to sit next to each part ("near": "U1.VI" -> U1)."""
    n = {}
    for c in design.components:
        near = (c.place or {}).get("near") if isinstance(c.place, dict) else None
        if near:
            ref = near.split(".", 1)[0]
            n[ref] = n.get(ref, 0) + 1
    return n


def _auto_hints(design):
    """Placement knowledge applied without the user asking (layout.py rules TH-03, SW-08):
    temperature/humidity sensors keep away from heat (MCUs, modules, regulators, inductors),
    crystals keep away from switching inductors. A spec's own away_from wins."""
    from . import layout
    # TH-03: 10 mm from MCUs/modules, 15 mm from regulators and inductors (+1 mm slack)
    heat, inductors = {}, []
    for c in design.components:
        ref = c.ref
        kind = re.match(r"^[A-Za-z]+", ref).group(0).upper()
        if kind in ("U", "IC") and "Regulator_" in c.symbol_id:
            heat[ref] = 16.0
        elif kind in ("U", "IC") and (len(c.sym.pins) >= 24 or "RF_Module" in c.symbol_id):
            heat[ref] = 11.0
        if kind == "L":
            inductors.append(ref)
            heat[ref] = 16.0
    hints = {}
    for c in design.components:
        place = c.place if isinstance(c.place, dict) else {}
        if "away_from" in place or "x" in place:
            continue
        kind = re.match(r"^[A-Za-z]+", c.ref).group(0).upper()
        if kind in ("U", "IC") and layout.TEMP_SENSOR.search("%s %s" % (c.value, c.symbol_id)):
            far = {r: d for r, d in heat.items() if r != c.ref}
            if far:
                hints[c.ref] = {"away_from": far}
        elif kind == "Y" and inductors:
            hints[c.ref] = {"away_from": {r: 11.0 for r in inductors}}
    return hints


def compute_placement(design, sch_builder, W, H, holes, quiet=False, step=None, refine=2, seed=None):
    parts = []
    net_sizes = {n: len(p) for n, p in design.nets.items()}
    deps = _dependents(design)
    auto = _auto_hints(design)
    for c in design.components:
        pads = [(p["number"], p["x"], p["y"], design.pin_net.get((c.ref, p["number"])))
                for p in c.fp.pads]
        # a part with capacitors/resistors pinned next to it keeps other parts further away,
        # so those helpers find room at its pins (a regulator hugging a connector left its
        # input capacitor nowhere to go). The pinned parts themselves still sit close: their
        # gap to it is 2x their own small margin (place.Placer._pair_gap).
        # (not for 2-pad passives: a decoupling cap with its bulk cap "near" it must still
        # squeeze into the slot at its IC's pin)
        bonus = min(1.6, 0.6 * deps.get(c.ref, 0)) if len(c.fp.pads) > 2 else 0.0
        margin = part_margin(design, c) + bonus
        hint = _resolve_near(design, c.place)
        if c.ref in auto:
            hint = dict(hint or {}, **auto[c.ref])
        parts.append(place.Part(c.ref, c.fp.courtyard, pads, hint,
                                len(c.fp.pads), margin, c.fp.court_rects))
        parts[-1].bonus = bonus
    obstacles = []
    for (x, y), r in holes:
        obstacles.append((x - r, y - r, x + r, y + r))
    spread = _spread(design, parts, W, H)
    pl = place.Placer(W, H, parts, net_sizes, design.rules["edge_clearance"], obstacles)
    failed = pl.run(step=step or (0.5 if max(W, H) < 120 else 1.0), refine_passes=refine, seed=seed)
    if not quiet:
        if spread:    # informational; not in pl.log, which auto-shrink reads as "too tight"
            ui.say("note: spread parts by +%.1f mm per side to use the board (%.0f%% -> %.0f%% filled)" % spread)
        for line in pl.log:
            ui.say("note: " + line)
    result = {p.ref: (p.x, p.y, p.rot) for p in parts if p.x is not None}
    return result, failed, list(pl.log)


def _set_rules(board, design):
    pn = pcbnew()
    r = design.rules
    ds = board.GetDesignSettings()
    classes = design.raw.get("net_classes", []) + getattr(design, "auto_classes", [])
    # board-wide minimums must not be stricter than any class, or every legitimately
    # close track in that class becomes a DRC error
    ds.m_MinClearance = mm(min([r["clearance"]] + [c.get("clearance", r["clearance"]) for c in classes]))
    ds.m_TrackMinWidth = mm(min([r["track"], 0.15] + [c.get("track", r["track"]) for c in classes]))
    ds.m_CopperEdgeClearance = mm(r["edge_clearance"])
    # allow the smallest hole a chosen footprint actually uses (e.g. module thermal vias)
    holes = [r["via_drill"], 0.3]
    for c in design.components:
        for pad in sexp_drills(c.fp):
            holes.append(pad)
    ds.m_MinThroughDrill = mm(max(0.15, min(holes)))
    ds.m_MinResolvedSpokes = 1
    ds.m_HoleClearance = mm(0.25)
    ds.SetBoardThickness(mm(float(design.board.get("thickness", 1.6))))
    ns = ds.m_NetSettings
    # NewBoard reloads an existing .kicad_pro: drop the previous build's classes and assignments,
    # or stale ones merge with the new (e.g. "Power,PowerFine" with the wrong width)
    ns.ClearNetclassPatternAssignments()
    ns.ClearNetclasses()
    dflt = ns.GetDefaultNetclass()
    dflt.SetClearance(mm(r["clearance"]))
    dflt.SetTrackWidth(mm(r["track"]))
    dflt.SetViaDiameter(mm(r["via_diameter"]))
    dflt.SetViaDrill(mm(r["via_drill"]))
    power = pn.NETCLASS("Power")
    power.SetClearance(mm(r["clearance"]))
    power.SetTrackWidth(mm(r["power_track"]))
    power.SetViaDiameter(mm(max(r["via_diameter"], 0.8)))
    power.SetViaDrill(mm(max(r["via_drill"], 0.4)))
    ns.SetNetclass("Power", power)
    ground = pn.NETCLASS("Ground")   # poured net: routed thin, the pour carries current
    ground.SetClearance(mm(r["clearance"]))
    ground.SetTrackWidth(mm(r["track"]))
    ground.SetViaDiameter(mm(r["via_diameter"]))
    ground.SetViaDrill(mm(r["via_drill"]))
    ns.SetNetclass("Ground", ground)
    for extra in design.raw.get("net_classes", []) + getattr(design, "auto_classes", []):
        nc = pn.NETCLASS(extra["name"])
        nc.SetClearance(mm(extra.get("clearance", r["clearance"])))
        nc.SetTrackWidth(mm(extra.get("track", r["track"])))
        nc.SetViaDiameter(mm(extra.get("via_diameter", r["via_diameter"])))
        nc.SetViaDrill(mm(extra.get("via_drill", r["via_drill"])))
        ns.SetNetclass(extra["name"], nc)
    return ns


def _outline(board, W, H, radius):
    pn = pcbnew()
    ox, oy = ORIGIN
    edge = pn.Edge_Cuts

    def seg(x0, y0, x1, y1):
        s = pn.PCB_SHAPE(board, pn.SHAPE_T_SEGMENT)
        s.SetStart(pn.VECTOR2I(mm(ox + x0), mm(oy + y0)))
        s.SetEnd(pn.VECTOR2I(mm(ox + x1), mm(oy + y1)))
        s.SetLayer(edge); s.SetWidth(mm(0.1))
        board.Add(s)

    def arc(cx, cy, sx, sy, deg):
        s = pn.PCB_SHAPE(board, pn.SHAPE_T_ARC)
        s.SetCenter(pn.VECTOR2I(mm(ox + cx), mm(oy + cy)))
        s.SetStart(pn.VECTOR2I(mm(ox + sx), mm(oy + sy)))
        s.SetArcAngleAndEnd(pn.EDA_ANGLE(deg, pn.DEGREES_T), True)
        s.SetLayer(edge); s.SetWidth(mm(0.1))
        board.Add(s)

    r = max(0.0, min(radius, W / 2, H / 2))
    seg(r, 0, W - r, 0); seg(W, r, W, H - r); seg(W - r, H, r, H); seg(0, H - r, 0, r)
    if r > 0:
        arc(W - r, r, W - r, 0, 90)
        arc(W - r, H - r, W, H - r, 90)
        arc(r, H - r, r, H, 90)
        arc(r, r, 0, r, 90)


def _holes(design, W, H):
    hole_size, hole_pos = hole_positions(design, W, H)
    holes = []
    if hole_pos:
        fpname, dia = HOLES.get(hole_size, HOLES["M3"])
        r = dia / 2 + 2.0   # keep parts clear of the screw head
        holes = [((x, y), r) for x, y in hole_pos]
    return hole_size, hole_pos, holes


def shrink_to_fit(design, sch_builder, W, H, step=0.92, max_steps=6):
    """Shrink an auto-sized board while every part still fits at full spacing.

    Stops above two floors: enough free area left for routing (1.45x the
    parts' footprint including spacing), and the area per pad at which
    similar boards failed to route before (learned)."""
    from . import learn
    layers = int(design.board.get("layers", 2))
    pads = sum(len(c.fp.pads) for c in design.components)
    floor = 1.45 * _courtyard_area(design)
    if design.board.get("mounting_holes"):
        floor += 4 * 7.0 * 7.0
    learned_floor = learn.min_area_per_pad(layers, pads)
    if learned_floor:
        floor = max(floor, learned_floor * pads)
    # no denser than most real boards that routed completely at this layer count (knowledge base)
    from . import knowledge
    real = knowledge.routed_density(layers)
    conns = sum(len(p) - 1 for p in design.nets.values() if len(p) > 1)
    if real and real.get("p75"):
        floor = max(floor, conns / real["p75"] * 100.0)
    best = None
    for _ in range(max_steps):
        W2, H2 = float(math.floor(W * step)), float(math.floor(H * step))
        if W2 * H2 < floor:
            break
        _, _, holes = _holes(design, W2, H2)
        # coarse grid for the trials (fast); the final size gets a fine placement below
        placement, failed, log = compute_placement(design, sch_builder, W2, H2, holes, quiet=True,
                                                   step=1.0, refine=0)
        if failed or log:        # anything stranded or squeezed means it's too tight
            break
        W, H, best = W2, H2, placement
    if best is not None:
        _, _, holes = _holes(design, W, H)
        fine, failed, log = compute_placement(design, sch_builder, W, H, holes, quiet=True)
        if not failed and not log:
            best = fine
        else:
            # the fine grid squeezed something the coarse trial didn't: polish the trial
            # instead (refinement only moves a part where it's better off)
            fine, failed, log = compute_placement(design, sch_builder, W, H, holes, quiet=True, seed=best)
            if not failed and not log:
                best = fine
    return W, H, best


def build(design, sch_builder, pcb_path, placement=None):
    notes = []
    pn = pcbnew()
    board = pn.NewBoard(pcb_path)
    layers = int(design.board.get("layers", 2))
    board.SetCopperLayerCount(layers)
    ns = _set_rules(board, design)

    tb = board.GetTitleBlock()
    tb.SetTitle(design.raw.get("title", design.name))
    tb.SetRevision(str(design.raw.get("revision", "A")))
    tb.SetCompany(str(design.raw.get("author", "")))

    names = {}
    netinfo = {}
    classed = {n for extra in design.raw.get("net_classes", []) + getattr(design, "auto_classes", [])
               for n in extra.get("nets", [])}
    for net in sorted(design.nets):
        name = pcb_net_name(design, net, sch_builder)
        names[net] = name
        ni = pn.NETINFO_ITEM(board, name)
        board.Add(ni)
        netinfo[net] = ni
        if net in classed:
            continue        # one class per net: KiCad merges several into one with the wrong width
        if net == design.board.get("ground_pour", "GND"):
            ns.SetNetclassPatternAssignment(name, "Ground")
        elif net in design.power_nets:
            ns.SetNetclassPatternAssignment(name, "Power")
    for extra in getattr(design, "auto_classes", []):
        for pat in extra.get("patterns", []):
            ns.SetNetclassPatternAssignment(pat, extra["name"])
    for extra in design.raw.get("net_classes", []) + getattr(design, "auto_classes", []):
        for net in extra.get("nets", []):
            if net in names:
                ns.SetNetclassPatternAssignment(names[net], extra["name"])

    W, H = board_size(design)
    hole_size, hole_pos, holes = _holes(design, W, H)

    if placement is None:
        shrunk = None
        if auto_sized(design) and design.board.get("auto_shrink", True):
            W2, H2, shrunk = shrink_to_fit(design, sch_builder, W, H)
            if shrunk:
                notes.append("auto-size shrank the board from %.0f x %.0f to %.0f x %.0f mm" % (W, H, W2, H2))
                ui.say("note: " + notes[-1])
                W, H, placement, failed = W2, H2, shrunk, []
                hole_size, hole_pos, holes = _holes(design, W, H)
        if not shrunk:
            placement, failed, log = compute_placement(design, sch_builder, W, H, holes)
            notes.extend(log)
    else:
        failed = []

    ox, oy = ORIGIN
    for c in design.components:
        lib, name = c.footprint_id.split(":", 1)
        fp = pn.FootprintLoad(design.fp_libs[lib], name)
        if fp is None:
            raise RuntimeError("could not load footprint %s" % c.footprint_id)
        fp.SetFPIDAsString(c.footprint_id)
        fp.SetReference(c.ref)
        fp.SetValue(c.value)
        uid = sch_builder.symbol_uuids.get(c.ref)
        if uid:
            fp.SetPath(pn.KIID_PATH("/" + uid))
        fp.SetSheetfile(design.name + ".kicad_sch")
        fp.SetSheetname("/")
        for k, label in (("lcsc", "LCSC"), ("mpn", "MPN"), ("manufacturer", "Manufacturer")):
            if c.d.get(k):
                fp.SetField(label, str(c.d[k]))
                f = fp.GetFieldByName(label)
                if f is not None:
                    f.SetVisible(False)
        if c.d.get("dnp"):
            fp.SetDNP(True)
        # compact reference text keeps silkscreen legible on dense boards
        ref = fp.Reference()
        ref.SetTextSize(pn.VECTOR2I(mm(0.8), mm(0.8)))
        ref.SetTextThickness(mm(0.12))
        pin_info = {p["number"]: p for p in c.sym.pins}
        for pad in fp.Pads():
            key = (c.ref, pad.GetNumber())
            info = pin_info.get(pad.GetNumber())
            if info:
                pad.SetPinFunction(info["name"] if info["name"] != "~" else "")
                pad.SetPinType(info["type"])
            net = design.pin_net.get(key)
            if net:
                pad.SetNet(netinfo[net])
            elif pad.GetNumber() and any(p["number"] == key[1] for p in c.sym.pins):
                # eeschema names unconnected pins' nets; match it for schematic parity
                ncname = unconnected_net_name(c, key[1])
                if ncname not in netinfo:
                    netinfo[ncname] = pn.NETINFO_ITEM(board, ncname)
                    board.Add(netinfo[ncname])
                pad.SetNet(netinfo[ncname])
        board.Add(fp)
        if c.ref in placement:
            x, y, rot = placement[c.ref]
            fp.SetPosition(pn.VECTOR2I(mm(ox + x), mm(oy + y)))
            fp.SetOrientationDegrees(rot)
            if c.place.get("side") == "bottom":
                fp.Flip(fp.GetPosition(), pn.FLIP_DIRECTION_TOP_BOTTOM
                        if hasattr(pn, "FLIP_DIRECTION_TOP_BOTTOM") else False)
        else:
            # park unplaced parts beside the board so a human (or Claude) can move them
            fp.SetPosition(pn.VECTOR2I(mm(ox + W + 10), mm(oy + 5 * len(failed))))

    if hole_pos:
        fpname, dia = HOLES.get(hole_size, HOLES["M3"])
        for i, (x, y) in enumerate(hole_pos):
            if "MountingHole" not in design.fp_libs:
                raise RuntimeError("KiCad's MountingHole footprint library wasn't found; "
                                   "check KiCad's footprint libraries (kipcb doctor)")
            fp = pn.FootprintLoad(design.fp_libs["MountingHole"], fpname)
            fp.SetReference("H%d" % (i + 1))
            fp.SetValue(hole_size)
            fp.SetBoardOnly(True)
            fp.Reference().SetVisible(False)
            fp.SetExcludedFromBOM(True)
            fp.SetExcludedFromPosFiles(True)
            fp.SetPosition(pn.VECTOR2I(mm(ox + x), mm(oy + y)))
            board.Add(fp)

    _outline(board, W, H, float(design.board.get("corner_radius", 1.0)))
    # fab outputs are referenced to the board's bottom-left corner
    ds = board.GetDesignSettings()
    ds.SetAuxOrigin(pn.VECTOR2I(mm(ox), mm(oy + H)))
    ds.SetGridOrigin(pn.VECTOR2I(mm(ox), mm(oy + H)))
    pn.SaveBoard(pcb_path, board)
    return {"width": W, "height": H, "placement": placement, "failed": failed, "notes": notes}

