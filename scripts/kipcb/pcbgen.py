"""Generate a placed (unrouted) KiCad board from a Design."""

import math
import os

from . import place
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
    from . import learn   # footprints that caused unrouted connections before get more room
    return base + (ic_extra if is_ic else 0.0) + learn.extra_margin(comp.footprint_id)


def compute_placement(design, sch_builder, W, H, holes, quiet=False, step=None, refine=2):
    parts = []
    net_sizes = {n: len(p) for n, p in design.nets.items()}
    for c in design.components:
        pads = [(p["number"], p["x"], p["y"], design.pin_net.get((c.ref, p["number"])))
                for p in c.fp.pads]
        parts.append(place.Part(c.ref, c.fp.courtyard, pads, _resolve_near(design, c.place),
                                len(c.fp.pads), part_margin(design, c), c.fp.court_rects))
    obstacles = []
    for (x, y), r in holes:
        obstacles.append((x - r, y - r, x + r, y + r))
    pl = place.Placer(W, H, parts, net_sizes, design.rules["edge_clearance"], obstacles)
    failed = pl.run(step=step or (0.5 if max(W, H) < 120 else 1.0), refine_passes=refine)
    if not quiet:
        for line in pl.log:
            print("note: " + line)
    result = {p.ref: (p.x, p.y, p.rot) for p in parts if p.x is not None}
    return (result, failed, pl.log) if quiet else (result, failed)


def _set_rules(board, design):
    pn = pcbnew()
    r = design.rules
    ds = board.GetDesignSettings()
    ds.m_MinClearance = mm(r["clearance"])
    ds.m_TrackMinWidth = mm(min(r["track"], 0.15))
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
    for extra in design.raw.get("net_classes", []):
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
    return W, H, best


def build(design, sch_builder, pcb_path, placement=None):
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
    for net in sorted(design.nets):
        name = pcb_net_name(design, net, sch_builder)
        names[net] = name
        ni = pn.NETINFO_ITEM(board, name)
        board.Add(ni)
        netinfo[net] = ni
        if net == design.board.get("ground_pour", "GND"):
            ns.SetNetclassPatternAssignment(name, "Ground")
        elif net in design.power_nets:
            ns.SetNetclassPatternAssignment(name, "Power")
    for extra in design.raw.get("net_classes", []):
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
                print("note: auto-size shrank the board from %.0f x %.0f to %.0f x %.0f mm" % (W, H, W2, H2))
                W, H, placement, failed = W2, H2, shrunk, []
                hole_size, hole_pos, holes = _holes(design, W, H)
        if not shrunk:
            placement, failed = compute_placement(design, sch_builder, W, H, holes)
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
    return {"width": W, "height": H, "placement": placement, "failed": failed}


def read_placement(pcb_path):
    """Current footprint positions (board-relative mm) from an existing board."""
    pn = pcbnew()
    board = pn.LoadBoard(pcb_path)
    ox, oy = ORIGIN
    out = {}
    for fp in board.GetFootprints():
        p = fp.GetPosition()
        out[fp.GetReference()] = (p.x / 1e6 - ox, p.y / 1e6 - oy, fp.GetOrientationDegrees())
    return out
