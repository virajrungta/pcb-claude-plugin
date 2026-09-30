"""Connectivity-driven component placement.

Coordinates are millimetres relative to the board's top-left corner, Y down
(KiCad convention). Rotation is CCW degrees as seen from the top.

Placement hints per component (spec "place"):
  {"x": 10, "y": 5, "rot": 90}      fixed position
  {"edge": "left", "at": 8, "rot"}  flush against a board edge (at = mm along edge)
  {"near": "U1"} / {"near": "U1.8"} pull strongly toward a part or pin
  {"rot": 0}                        restrict rotation only
"""

import math

GAP = 0.3          # min gap between courtyards (mm)
BIG_NET = 6        # nets with more pins than this (GND, rails) get lower weight


def rot_pt(x, y, deg):
    a = math.radians(deg)
    c, s = round(math.cos(a), 9), round(math.sin(a), 9)
    return (x * c + y * s, -x * s + y * c)


def rot_box(box, deg):
    pts = [rot_pt(box[0], box[1], deg), rot_pt(box[2], box[1], deg),
           rot_pt(box[0], box[3], deg), rot_pt(box[2], box[3], deg)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


class Part(object):
    def __init__(self, ref, courtyard, pads, hint, npads):
        self.ref = ref
        self.court = courtyard           # (x0,y0,x1,y1) rel. to origin, rot 0
        self.pads = pads                 # [(pad_number, x, y, net)]
        self.hint = hint or {}
        self.npads = npads
        self.x = self.y = None
        self.rot = 0
        self.fixed = False

    def box(self, x=None, y=None, rot=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        rot = self.rot if rot is None else rot
        b = rot_box(self.court, rot)
        return (x + b[0], y + b[1], x + b[2], y + b[3])

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
    def _overlaps(self, box, skip):
        for b in self.obstacles:
            if _intersect(box, b, GAP):
                return True
        for q in self.parts:
            if q is skip or q.x is None:
                continue
            if _intersect(box, q.box(), GAP):
                return True
        return False

    def _inside(self, box, part):
        m = self.margin
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
        if pin:
            for num, x, y, net in q.pad_abs(q.x, q.y, q.rot):
                if num == pin:
                    return (x, y)
            # pin may be a net-name style reference resolved upstream
        return ((q.box()[0] + q.box()[2]) / 2, (q.box()[1] + q.box()[3]) / 2)

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
            b = part.box(x, y, rot)
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            cost += 4.0 * math.hypot(cx - near[0], cy - near[1])
        # mild pull toward the board centre keeps the layout compact
        cost += 0.02 * math.hypot(x - self.W / 2, y - self.H / 2)
        return cost

    def _target(self, part, placed_pads):
        near = self._near_target(part)
        if near:
            return near
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
        for _, x, y in cands:
            for rot in self._rotations(part):
                box = part.box(x, y, rot)
                if not self._inside(box, part) or self._overlaps(box, part):
                    continue
                feasible += 1
                c = self._cost(part, x, y, rot, placed_pads, near)
                if best is None or c < best[0]:
                    best = (c, x, y, rot)
            if feasible > 250:
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
                if not self._inside(box, part) or self._overlaps(box, part):
                    continue
                c = self._cost(part, x, y, rot, placed_pads, self._near_target(part))
                c += 0.05 * abs(s - along / 2)  # prefer centred on the edge
                if best is None or c < best[0]:
                    best = (c, x, y, rot)
        if best is None:
            return False
        part.x, part.y, part.rot = best[1], best[2], best[3]
        return True

    def run(self, step=0.5, refine_passes=2):
        failed = []
        # 1. fixed parts
        for p in self.parts:
            if "x" in p.hint and "y" in p.hint:
                p.x, p.y, p.rot = float(p.hint["x"]), float(p.hint["y"]), p.hint.get("rot", 0) % 360
                p.fixed = True
        # 2. edge parts, biggest first
        for p in sorted([p for p in self.parts if p.x is None and p.hint.get("edge")], key=lambda p: -p.npads):
            if not self._place_edge(p, step):
                failed.append(p.ref)
        # 3. everything else in connectivity order
        remaining = [p for p in self.parts if p.x is None and p.ref not in failed]
        if not any(q.x is not None for q in self.parts) and remaining:
            first = max(remaining, key=lambda p: p.npads)
            first.x, first.y = self.W / 2, self.H / 2
            if not self._inside(first.box(), first) or self._overlaps(first.box(), first):
                first.x = None
            remaining = [p for p in remaining if p.x is None]
        while remaining:
            placed_refs_pads = self._placed_pads(None)
            def score(p):
                links = sum(1 for n, _, _, net in p.pads if net in placed_refs_pads
                            and self.net_sizes.get(net, 0) <= BIG_NET)
                near_ready = 1 if self._near_target(p) else 0
                blocked = 1 if p.hint.get("near") and not near_ready else 0
                return (-blocked, near_ready, links, p.npads)
            nxt = max(remaining, key=score)
            remaining.remove(nxt)
            if not self._place_free(nxt, step):
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
        return failed


def _intersect(a, b, gap):
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _close(v, w):
    return abs(v[0] - w[0]) < 1e-6 and abs(v[1] - w[1]) < 1e-6
