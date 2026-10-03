"""Connectivity-driven component placement.

Coordinates are millimetres relative to the board's top-left corner, Y down
(KiCad convention). Rotation is CCW degrees as seen from the top.

Placement hints per component (spec "place"):
  {"x": 10, "y": 5, "rot": 90}      fixed position
  {"edge": "left", "at": 8, "rot"}  flush against a board edge (at = mm along edge)
  {"near": "U1"} / {"near": "U1.8"} pull strongly toward a part or pin
  {"rot": 0}                        restrict rotation only
  {"away_from": ["L1", "U5"], "min_dist": 8}   or per part: {"away_from": {"L1": 15, "U5": 10}}
                                    keep this part at least min_dist mm (edge
                                    to edge) from noisy/sensitive parts

Spacing: every part carries a per-side margin (bigger for ICs, which need room
to fan out their traces). A part pulled `near` another may sit closer to it.
"""

import math

GAP = 0.3          # legacy default gap, used for board-size estimates
BIG_NET = 6        # nets with more pins than this (GND, rails) get lower weight


_CS = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}   # exact cos/sin


def rot_pt(x, y, deg):
    cs = _CS.get(deg)
    if cs is None:
        a = math.radians(deg)
        cs = (round(math.cos(a), 9), round(math.sin(a), 9))
    c, s = cs
    return (x * c + y * s, -x * s + y * c)


def rot_box(box, deg):
    pts = [rot_pt(box[0], box[1], deg), rot_pt(box[2], box[1], deg),
           rot_pt(box[0], box[3], deg), rot_pt(box[2], box[3], deg)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


class Part(object):
    def __init__(self, ref, courtyard, pads, hint, npads, margin=GAP / 2, rects=None):
        self.ref = ref
        self.margin = margin
        self.spread = 0.0                # extra room on a roomy board, only between spread parts
        self.bonus = 0.0                 # part of margin kept for this part's own helpers
        self.squeezed = False
        self.rects = rects or [courtyard]
        self.court = courtyard           # (x0,y0,x1,y1) rel. to origin, rot 0
        self.pads = pads                 # [(pad_number, x, y, net)]
        self.hint = hint or {}
        self.npads = npads
        self.x = self.y = None
        self.rot = 0
        self.fixed = False
        self._rot_cache = {}             # rot -> (rotated courtyard box, rotated rects)
        near = self.hint.get("near")
        self.near_ref = near.split(".", 1)[0] if near else None

    def _rotated(self, rot):
        r = self._rot_cache.get(rot)
        if r is None:
            r = (rot_box(self.court, rot), [rot_box(b, rot) for b in self.rects])
            self._rot_cache[rot] = r
        return r

    def box(self, x=None, y=None, rot=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        b = self._rotated(self.rot if rot is None else rot)[0]
        return (x + b[0], y + b[1], x + b[2], y + b[3])

    def rect_boxes(self, x=None, y=None, rot=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        return [(x + b[0], y + b[1], x + b[2], y + b[3])
                for b in self._rotated(self.rot if rot is None else rot)[1]]

    def pad_abs(self, x, y, rot):
        out = []
        for num, px, py, net in self.pads:
            rx, ry = rot_pt(px, py, rot)
            out.append((num, x + rx, y + ry, net))
        return out

    def mating_dir(self):
        """Unit vector (rot 0) from the pads toward the far side of the body.

        Connector footprints put their pads at the back and the body/opening
        at the front, so this points at the mating face."""
        if not self.pads:
            return None
        cx = sum(p[1] for p in self.pads) / len(self.pads)
        cy = sum(p[2] for p in self.pads) / len(self.pads)
        bx = (self.court[0] + self.court[2]) / 2 - cx
        by = (self.court[1] + self.court[3]) / 2 - cy
        if abs(bx) < 1.0 and abs(by) < 1.0:
            return None
        return (1 if bx > 0 else -1, 0) if abs(bx) > abs(by) else (0, 1 if by > 0 else -1)


class Placer(object):
    def __init__(self, width, height, parts, net_sizes, edge_margin=0.5, obstacles=None):
        self.W, self.H = width, height
        self.parts = parts
        self.by_ref = {p.ref: p for p in parts}
        self.net_sizes = net_sizes
        self.margin = edge_margin
        self.obstacles = list(obstacles or [])   # fixed boxes, e.g. mounting holes
        self.log = []

    # ---------------------------------------------------------- geometry
    def _pair_gap(self, p, q):
        if _near_ref(p) == q.ref or _near_ref(q) == p.ref or p.squeezed or q.squeezed:
            return 2 * min(p.margin, q.margin)
        extra = p.spread + q.spread if (p.spread and q.spread) else 0.0
        # a part's bonus (room kept at its pins for its own helpers) doesn't apply against
        # another part's helper: that one sits at its own target, not in this part's pin area
        mp = p.margin - (p.bonus if q.near_ref else 0.0)
        mq = q.margin - (q.bonus if p.near_ref else 0.0)
        return mp + mq + extra

    def _blockers(self, part):
        """Everything already on the board, computed once per placement search (nothing moves
        while one part looks for its spot): [(inflated box, gap, rects)], the box grown by
        the gap so the common case (far apart) is four comparisons."""
        out = []
        for b in self.obstacles:
            g = part.margin
            out.append(((b[0] - g, b[1] - g, b[2] + g, b[3] + g), g, [b]))
        for q in self.parts:
            if q is part or q.x is None:
                continue
            b, g = q.box(), self._pair_gap(part, q)
            out.append(((b[0] - g, b[1] - g, b[2] + g, b[3] + g), g, q.rect_boxes()))
        return out

    def _overlaps(self, box, skip, rects=None, blockers=None):
        """box: bounding box of the candidate; rects: its exact courtyard rectangles."""
        rects = rects or [box]
        x0, y0, x1, y1 = box
        for qb, gap, qrects in (self._blockers(skip) if blockers is None else blockers):
            if x1 <= qb[0] or qb[2] <= x0 or y1 <= qb[1] or qb[3] <= y0:
                continue   # bounding boxes apart: no need for detail
            if any(_intersect(r, s_, gap) for r in rects for s_ in qrects):
                return True
        return False

    def _inside(self, box, part):
        m = max(self.margin, part.margin)
        edge = part.hint.get("edge")
        x0, y0, x1, y1 = box
        tol = 1e-6
        ok_l = x0 >= m - tol or edge == "left"
        ok_r = x1 <= self.W - m + tol or edge == "right"
        ok_t = y0 >= m - tol or edge == "top"
        ok_b = y1 <= self.H - m + tol or edge == "bottom"
        return ok_l and ok_r and ok_t and ok_b and x0 < self.W and y0 < self.H and x1 > 0 and y1 > 0

    # ---------------------------------------------------------- cost
    def _near_target(self, part):
        near = part.hint.get("near")
        if not near:
            return None
        ref, _, pin = near.partition(".")
        q = self.by_ref.get(ref)
        if q is None or q.x is None:
            return None
        return self._anchor(q, q.x, q.y, q.rot, pin, part)

    def _anchor(self, q, x, y, rot, pin, asker):
        """Where `asker` (near-hinted to q) should sit, with q at (x, y, rot): q's pin if
        named, else q's pads that asker connects to (an ESD array near a USB connector
        belongs at the data pins, not the middle of the receptacle body), else q's centre."""
        pads = q.pad_abs(x, y, rot)
        if pin:
            for num, px, py, net in pads:
                if num == pin:
                    return (px, py, net)
        mine = {n for _, _, _, n in asker.pads if n}
        shared = [(px, py, n) for _, px, py, n in pads if n in mine]
        pts = [t for t in shared if self.net_sizes.get(t[2], 0) <= BIG_NET] or shared
        if pts and len(pts) < len(pads):
            return (sum(t[0] for t in pts) / len(pts), sum(t[1] for t in pts) / len(pts),
                    frozenset(t[2] for t in pts))
        b = q.box(x, y, rot)
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, None)

    @staticmethod
    def _near_dist(part, x, y, rot, target):
        """Distance from part to a near-target. When the target is a pin, measure
        from this part's pad on the same net (e.g. a cap's supply pad to the IC's
        VDD pin): that is the loop that matters for decoupling."""
        tx, ty, net = target
        if net:      # one net (a pin), or the nets this part shares with the target part
            nets = net if isinstance(net, frozenset) else (net,)
            ds = [math.hypot(px - tx, py - ty) for num, px, py, n in part.pad_abs(x, y, rot) if n in nets]
            if ds:
                return min(ds)
        b = part.box(x, y, rot)
        return math.hypot((b[0] + b[2]) / 2 - tx, (b[1] + b[3]) / 2 - ty)

    def _placed_pads(self, skip):
        pads = {}
        for q in self.parts:
            if q is skip or q.x is None:
                continue
            for num, x, y, net in q.pad_abs(q.x, q.y, q.rot):
                if net:
                    pads.setdefault(net, []).append((x, y))
        return pads

    def _cost(self, part, x, y, rot, placed_pads, near):
        cost = 0.0
        for num, px, py, net in part.pad_abs(x, y, rot):
            if not net or net not in placed_pads:
                continue
            d = min(math.hypot(px - qx, py - qy) for qx, qy in placed_pads[net])
            w = 0.25 if self.net_sizes.get(net, 0) > BIG_NET else 1.0
            cost += w * d
        if near:
            cost += 4.0 * self._near_dist(part, x, y, rot, near)
        # parts that asked to sit near this one pull it back toward them
        for q in self.parts:
            if q is part or q.x is None or _near_ref(q) != part.ref:
                continue
            _, _, pin = q.hint["near"].partition(".")
            # a part with its own near target (a decoupling cap at its pin) follows that
            # first; its own helpers (the bulk cap "near" it) pull more weakly
            w = 1.0 if near else 4.0
            cost += w * self._near_dist(q, q.x, q.y, q.rot, self._anchor(part, x, y, rot, pin, q))
        b = part.box(x, y, rot)
        for ref, need in _away(part.hint):
            q = self.by_ref.get(ref)
            if q is not None and q.x is not None:
                d = _box_dist(b, q.box())
                if d < need:
                    cost += 25.0 * (need - d)
        # the reverse: parts that asked to stay away from this one
        for q in self.parts:
            if q is part or q.x is None or not q.hint.get("away_from"):
                continue
            for ref, need in _away(q.hint):
                if ref == part.ref:
                    d = _box_dist(b, q.box())
                    if d < need:
                        cost += 25.0 * (need - d)
        # mild pull toward the board centre keeps the layout compact
        cost += 0.02 * math.hypot(x - self.W / 2, y - self.H / 2)
        return cost

    def _target(self, part, placed_pads):
        near = self._near_target(part)
        if near:
            return near[:2]
        pts = []
        for num, px, py, net in part.pads:
            if net in placed_pads and self.net_sizes.get(net, 0) <= BIG_NET:
                pts += placed_pads[net]
        if not pts:
            for num, px, py, net in part.pads:
                pts += placed_pads.get(net, [])
        if pts:
            return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        return (self.W / 2, self.H / 2)

    # ---------------------------------------------------------- placing
    def _rotations(self, part):
        if "rot" in part.hint:
            return [part.hint["rot"] % 360]
        if part.npads <= 1:
            return [0]
        return [0, 90, 180, 270]

    def _keeps_away(self, part):
        if getattr(self, "_away_refs", None) is None:
            self._away_refs = set()
            for q in self.parts:
                pairs = _away(q.hint)
                if pairs:
                    self._away_refs.add(q.ref)
                    self._away_refs.update(r for r, _ in pairs)
        return part.ref in self._away_refs

    def _place_free(self, part, step):
        placed_pads = self._placed_pads(part)
        tx, ty = self._target(part, placed_pads)
        near = self._near_target(part)
        cands = []
        nx, ny = int(self.W / step) + 1, int(self.H / step) + 1
        for i in range(nx):
            for j in range(ny):
                x, y = i * step, j * step
                cands.append(((x - tx) ** 2 + (y - ty) ** 2, x, y))
        cands.sort()
        best = None
        feasible = 0
        blockers = self._blockers(part)
        rots = self._rotations(part)
        # parts that must keep a distance (sensor <-> heat, crystal <-> inductor) usually find
        # their spot well away from their connections: search the whole board for them
        limit = None if self._keeps_away(part) else 250
        for _, x, y in cands:
            for rot in rots:
                box = part.box(x, y, rot)
                if not self._inside(box, part) or self._overlaps(box, part, part.rect_boxes(x, y, rot), blockers):
                    continue
                feasible += 1
                c = self._cost(part, x, y, rot, placed_pads, near)
                if best is None or c < best[0]:
                    best = (c, x, y, rot)
            if limit and feasible > limit:
                break
        if best is None:
            return False
        part.x, part.y, part.rot = best[1], best[2], best[3]
        return True

    def _place_edge(self, part, step):
        edge = part.hint["edge"]
        outward = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}[edge]
        if "rot" in part.hint:
            rots = [part.hint["rot"] % 360]
        else:
            md = part.mating_dir()
            rots = [0, 90, 180, 270]
            if md:
                rots = [r for r in rots if _close(rot_pt(md[0], md[1], r), outward)] or rots
        placed_pads = self._placed_pads(part)
        best = None
        blockers = self._blockers(part)
        for rot in rots:
            b = rot_box(part.court, rot)
            # flush: courtyard face on the board edge
            if edge == "left":
                fixed_x, fixed_y = -b[0], None
            elif edge == "right":
                fixed_x, fixed_y = self.W - b[2], None
            elif edge == "top":
                fixed_x, fixed_y = None, -b[1]
            else:
                fixed_x, fixed_y = None, self.H - b[3]
            along = self.H if fixed_x is not None else self.W
            positions = []
            if "at" in part.hint:
                positions = [part.hint["at"]]
            else:
                k = 0.0
                while k <= along:
                    positions.append(k)
                    k += step
            for s in positions:
                x = fixed_x if fixed_x is not None else s
                y = fixed_y if fixed_y is not None else s
                box = part.box(x, y, rot)
                if not self._inside(box, part) or self._overlaps(box, part, part.rect_boxes(x, y, rot), blockers):
                    continue
                c = self._cost(part, x, y, rot, placed_pads, self._near_target(part))
                c += 0.05 * abs(s - along / 2)  # prefer centred on the edge
                if best is None or c < best[0]:
                    best = (c, x, y, rot)
        if best is None:
            return False
        part.x, part.y, part.rot = best[1], best[2], best[3]
        return True

    def _place_tighter(self, part, step, edge=False):
        """Retry a part that didn't fit with progressively smaller spacing.

        A roomy spacing preset on a small board can leave no spot for a large
        part; squeezing it in is far better than parking it off the board."""
        original = part.margin
        part.squeezed = True     # the tight gap applies on both sides, not just this part's
        for margin in (original * 0.5, 0.1):
            part.margin = margin
            ok = self._place_edge(part, step) if edge else self._place_free(part, step)
            if ok:
                self.log.append("%s placed with reduced spacing (%.2f mm instead of %.2f mm)"
                                % (part.ref, margin, original))
                return True
        part.margin = original
        part.squeezed = False
        return False

    def run(self, step=0.5, refine_passes=2, seed=None):
        """Place every part; returns the refs that didn't fit. `seed` {ref: (x, y, rot)}: start
        from that placement (e.g. a coarse-grid trial) and only refine it on this grid."""
        failed = []
        if seed:
            for p in self.parts:
                if p.ref in seed:
                    p.x, p.y, p.rot = seed[p.ref]
                    p.fixed = "x" in p.hint and "y" in p.hint
        # 1. fixed parts
        for p in self.parts:
            if "x" in p.hint and "y" in p.hint:
                p.x, p.y, p.rot = float(p.hint["x"]), float(p.hint["y"]), p.hint.get("rot", 0) % 360
                p.fixed = True
        # 2. edge parts, biggest first
        for p in sorted([p for p in self.parts if p.x is None and p.hint.get("edge")], key=lambda p: -p.npads):
            if not self._place_edge(p, step) and not self._place_tighter(p, step, edge=True):
                failed.append(p.ref)
        # 3. everything else in connectivity order
        remaining = [p for p in self.parts if p.x is None and p.ref not in failed]
        if not any(q.x is not None for q in self.parts) and remaining:
            first = max(remaining, key=lambda p: p.npads)
            first.x, first.y = self.W / 2, self.H / 2
            if not self._inside(first.box(), first) or self._overlaps(first.box(), first, first.rect_boxes()):
                first.x = None
            remaining = [p for p in remaining if p.x is None]
        while remaining:
            placed_refs_pads = self._placed_pads(None)
            def score(p):
                links = sum(1 for n, _, _, net in p.pads if net in placed_refs_pads
                            and self.net_sizes.get(net, 0) <= BIG_NET)
                tgt = self._near_target(p)
                near_ready = 1 if tgt else 0
                blocked = 1 if p.hint.get("near") and not near_ready else 0
                # decoupling caps (pulled to a supply pin) claim their spot before
                # pull-ups and other helpers compete for the same corner
                decoupling = 1 if tgt and tgt[2] and self.net_sizes.get(tgt[2], 0) > BIG_NET else 0
                # a switching regulator's inductor goes before its helpers: the SW node must be short
                inductor = 1 if tgt and p.ref[:1] == "L" and p.ref[1:2].isdigit() else 0
                return (-blocked, inductor, decoupling, near_ready, links, p.npads)
            nxt = max(remaining, key=score)
            remaining.remove(nxt)
            if not self._place_free(nxt, step) and not self._place_tighter(nxt, step):
                failed.append(nxt.ref)
        # 4. refinement: re-place each movable part with full knowledge of the others
        for _ in range(refine_passes):
            for p in sorted(self.parts, key=lambda p: p.npads):
                if p.fixed or p.x is None or p.hint.get("edge"):
                    continue
                old = (p.x, p.y, p.rot)
                pp = self._placed_pads(p)
                old_cost = self._cost(p, old[0], old[1], old[2], pp, self._near_target(p))
                p.x = None
                if not self._place_free(p, step):
                    p.x, p.y, p.rot = old
                    continue
                new_cost = self._cost(p, p.x, p.y, p.rot, pp, self._near_target(p))
                if new_cost > old_cost:
                    p.x, p.y, p.rot = old
        # 5. align resistors/capacitors to one orientation where it's free (assembly and
        #    inspection prefer it; real boards: 69% of R/C share one orientation)
        self.align_passives()
        return failed

    def align_passives(self, tolerance=0.15):
        """Turn 2-pad R/C parts to the board's dominant orientation family (0/180 or 90/270)
        when the part still fits and its connection cost grows by at most `tolerance`."""
        rc = [p for p in self.parts if p.x is not None and not p.fixed and "rot" not in p.hint
              and not p.hint.get("edge") and p.npads == 2 and p.ref[:1] in ("R", "C") and p.ref[1:2].isdigit()]
        if len(rc) < 4:
            return 0
        horiz = sum(1 for p in rc if p.rot % 180 == 0)
        family = 0 if horiz * 2 >= len(rc) else 90
        turned = 0
        for p in rc:
            if p.rot % 180 == family:
                continue
            old = (p.x, p.y, p.rot)
            pp = self._placed_pads(p)
            near = self._near_target(p)
            base = self._cost(p, p.x, p.y, p.rot, pp, near)
            blockers = self._blockers(p)
            best = None
            # turning in place usually collides with packed neighbours: nudge up to 1 mm too
            offsets = sorted(((dx * 0.25, dy * 0.25) for dx in range(-4, 5) for dy in range(-4, 5)),
                             key=lambda o: o[0] ** 2 + o[1] ** 2)
            for rot in (family, family + 180):
                for dx, dy in offsets:
                    x, y = old[0] + dx, old[1] + dy
                    box = p.box(x, y, rot)
                    if not self._inside(box, p) or self._overlaps(box, p, p.rect_boxes(x, y, rot), blockers):
                        continue
                    c = self._cost(p, x, y, rot, pp, near)
                    if c <= base * (1 + tolerance) + 1.5 and (best is None or c < best[0]):
                        best = (c, rot, x, y)
            if best is not None:
                p.rot, p.x, p.y = best[1], best[2], best[3]
                turned += 1
            else:
                p.x, p.y, p.rot = old
        if turned:
            self.log_info = getattr(self, "log_info", []) + ["aligned %d resistors/capacitors" % turned]
        return turned


def _away(hint):
    """[(ref, min_dist)] from an away_from hint: "U1", ["U1", "L1"] (all at min_dist,
    default 8 mm) or {"U1": 10, "L1": 15} (per part)."""
    away = hint.get("away_from")
    if not away:
        return []
    if isinstance(away, dict):
        return [(r, float(d)) for r, d in away.items()]
    need = float(hint.get("min_dist", 8.0))
    return [(r, need) for r in ([away] if isinstance(away, str) else away)]


def _near_ref(p):
    return p.near_ref


def _box_dist(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def _intersect(a, b, gap):
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _close(v, w):
    return abs(v[0] - w[0]) < 1e-6 and abs(v[1] - w[1]) < 1e-6
