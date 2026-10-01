"""Unit tests for kipcb's pure-Python core (no KiCad needed).

Run:  PYTHONPATH=scripts python3 -m unittest discover -s tests -v
"""

import json
import os
import tempfile
import unittest

from kipcb import fplib, learn, place, sexp, spec
from kipcb.sexp import Sym


class SexpTests(unittest.TestCase):
    def test_roundtrip_keeps_structure(self):
        text = '(kicad_sch (version 20250114) (title_block (title "A \\"quoted\\" name")) (at 1.27 -2.54 90))'
        tree = sexp.loads(text)
        self.assertEqual(tree[0], "kicad_sch")
        self.assertIsInstance(tree[0], Sym)
        again = sexp.loads(sexp.dumps(tree))
        self.assertEqual(again, tree)
        self.assertEqual(sexp.value(sexp.find(tree, "title_block"), "title"), 'A "quoted" name')

    def test_number_formatting(self):
        self.assertEqual(sexp.dumps([Sym("at"), 1.27, -2.5400, 0]), "(at 1.27 -2.54 0)")
        self.assertEqual(sexp.dumps([Sym("w"), 0.1 + 0.2]), "(w 0.3)")

    def test_extract_toplevel_symbol(self):
        lib = ('(kicad_symbol_lib\n\t(symbol "R"\n\t\t(property "Value" "R")\n\t)\n'
               '\t(symbol "C"\n\t\t(property "Value" "C (with paren)")\n\t)\n)')
        block = sexp.extract_toplevel(lib, "symbol", "C")
        self.assertEqual(sexp.value(sexp.loads(block), "property"), "Value")
        self.assertIn("with paren", block)
        self.assertIsNone(sexp.extract_toplevel(lib, "symbol", "L"))


class CourtyardTests(unittest.TestCase):
    def _fp(self, body):
        return fplib.Footprint("Test:FP", "", sexp.loads("(footprint \"FP\" %s)" % body))

    def test_rectilinear_courtyard_is_decomposed(self):
        # T shape: wide antenna section on top of a narrower body
        lines = [((-10, -10), (10, -10)), ((10, -10), (10, -5)), ((10, -5), (5, -5)), ((5, -5), (5, 5)),
                 ((5, 5), (-5, 5)), ((-5, 5), (-5, -5)), ((-5, -5), (-10, -5)), ((-10, -5), (-10, -10))]
        body = " ".join('(fp_line (start %s %s) (end %s %s) (layer "F.CrtYd"))' % (a[0], a[1], b[0], b[1])
                        for a, b in lines)
        fp = self._fp(body)
        self.assertEqual(fp.courtyard, (-10, -10, 10, 5))
        self.assertEqual(sorted(fp.court_rects), [(-10.0, -10.0, 10.0, -5.0), (-5.0, -5.0, 5.0, 5.0)])

    def test_curved_courtyard_falls_back_to_bbox(self):
        fp = self._fp('(fp_circle (center 0 0) (end 2 0) (layer "F.CrtYd"))')
        self.assertEqual(fp.court_rects, [fp.courtyard])


class PlacementTests(unittest.TestCase):
    def test_rotation_is_counter_clockwise_on_screen(self):
        x, y = place.rot_pt(1, 0, 90)
        self.assertAlmostEqual(x, 0)
        self.assertAlmostEqual(y, -1)   # Y grows downward, so CCW turns +X into -Y

    def test_parts_do_not_overlap_and_decoupling_hugs_its_pin(self):
        ic = place.Part("U1", (-3, -3, 3, 3), [("1", -2.5, -1, "VDD"), ("2", -2.5, 1, "GND"),
                                                ("3", 2.5, 0, "SIG")], {}, 8, margin=1.1)
        cap = place.Part("C1", (-1, -0.5, 1, 0.5), [("1", -0.5, 0, "VDD"), ("2", 0.5, 0, "GND")],
                         {"near": "U1.1"}, 2, margin=0.5)
        res = place.Part("R1", (-1, -0.5, 1, 0.5), [("1", -0.5, 0, "SIG"), ("2", 0.5, 0, "VDD")],
                         {}, 2, margin=0.5)
        pl = place.Placer(30, 20, [ic, cap, res], {"VDD": 3, "GND": 2, "SIG": 2})
        self.assertEqual(pl.run(), [])
        boxes = {p.ref: p.box() for p in (ic, cap, res)}
        for a in boxes:
            for b in boxes:
                if a < b:
                    self.assertFalse(place._intersect(boxes[a], boxes[b], 0), "%s overlaps %s" % (a, b))
        pin = [p for p in ic.pad_abs(ic.x, ic.y, ic.rot) if p[0] == "1"][0]
        vdd = [p for p in cap.pad_abs(cap.x, cap.y, cap.rot) if p[3] == "VDD"][0]
        self.assertLess(((pin[1] - vdd[1]) ** 2 + (pin[2] - vdd[2]) ** 2) ** 0.5, 3.0)

    def test_part_too_big_for_roomy_spacing_is_squeezed_in_not_dropped(self):
        ic = place.Part("U1", (-4, -3, 4, 3), [("1", -3, 0, "A")], {}, 8, margin=3.0)
        r = place.Part("R1", (-1, -0.5, 1, 0.5), [("1", 0, 0, "A")], {}, 2, margin=3.0)
        pl = place.Placer(12, 9, [ic, r], {"A": 2}, edge_margin=0.5)
        self.assertEqual(pl.run(), [])
        self.assertTrue(any("reduced spacing" in line for line in pl.log))

    def test_edge_part_sits_flush_and_faces_out(self):
        # connector: pads at the back (y=+2), body extends to y=-4 (the mating side)
        j = place.Part("J1", (-4, -4, 4, 3), [("1", -1, 2, "A"), ("2", 1, 2, "B")], {"edge": "left"}, 2)
        pl = place.Placer(30, 20, [j], {"A": 1, "B": 1})
        pl.run()
        self.assertAlmostEqual(j.box()[0], 0.0)          # flush with the left edge
        dx, dy = place.rot_pt(*j.mating_dir(), deg=j.rot)
        self.assertEqual((round(dx), round(dy)), (-1, 0))  # mating side points out of the board


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        os.environ["KIPCB_EXPERIENCE"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("KIPCB_EXPERIENCE", None)
        os.remove(self.tmp.name)

    def test_cold_start_uses_default_order(self):
        arms, _ = learn.rank_arms({"layers": 2, "pad_density": 5, "pads": 100})
        self.assertEqual(arms[:4], learn.ARMS[:4])

    def test_successful_arm_is_tried_first_on_similar_boards(self):
        feats = {"layers": 2, "pad_density": 6.0, "pads": 150}
        winner = (True, None)
        for _ in range(3):
            for arm in learn.ARMS[:3]:
                learn.record("route_attempt", features=feats, arm=list(arm), missing=4, bottom_mm=30, seconds=60)
            learn.record("route_attempt", features=feats, arm=list(winner), missing=0, bottom_mm=40, seconds=30)
        arms, _ = learn.rank_arms(dict(feats, pad_density=6.3))
        self.assertEqual(arms[0], winner)
        # a very different board is not swayed by that history
        arms_other, _ = learn.rank_arms({"layers": 4, "pad_density": 30, "pads": 900})
        self.assertEqual(arms_other[:4], learn.ARMS[:4])

    def test_hard_footprints_get_extra_margin(self):
        learn.record("route_final", features={"layers": 2, "pads": 10, "area_mm2": 100},
                     hard_footprints=["Connector_USB:X"], unconnected=1)
        self.assertAlmostEqual(learn.extra_margin("Connector_USB:X"), 0.4)
        self.assertEqual(learn.extra_margin("Other:Y"), 0)

    def test_runs_after_a_stranded_part_are_ignored(self):
        f = {"layers": 2, "pads": 44, "area_mm2": 600, "pad_density": 7.3}
        learn.record("build", features=dict(f, pads=42), placement_failed=1)
        learn.record("route_attempt", features=f, arm=[False, 3.0], missing=15, bottom_mm=0, seconds=94)
        learn.record("route_final", features=f, arm=[False, 3.0], unconnected=15,
                     hard_footprints=["Package_SO:SOIC-8"], auto_size=False)
        learn.record("build", features=dict(f, pads=42), placement_failed=0)
        learn.record("route_attempt", features=f, arm=[False, 3.0], missing=0, bottom_mm=5, seconds=4)
        self.assertEqual(len(learn.history("route_attempt")), 1)
        self.assertEqual(len(learn.history("route_attempt", include_tainted=True)), 2)
        self.assertEqual(learn.extra_margin("Package_SO:SOIC-8"), 0)

    def test_failure_at_a_size_that_also_routed_does_not_inflate_boards(self):
        f = {"layers": 2, "pads": 100, "area_mm2": 3000}
        learn.record("route_final", features=f, unconnected=1, auto_size=True, hard_footprints=[])
        learn.record("route_final", features=f, unconnected=0, auto_size=True, hard_footprints=[])
        self.assertAlmostEqual(learn.area_per_pad(2, 100), 30.0)
        self.assertIsNone(learn.min_area_per_pad(2, 100))
        tight = dict(f, area_mm2=2000)
        learn.record("route_final", features=tight, unconnected=4, auto_size=True, hard_footprints=[])
        self.assertAlmostEqual(learn.min_area_per_pad(2, 100), 22.0)

    def test_learning_can_be_disabled(self):
        os.environ["KIPCB_LEARN"] = "0"
        try:
            learn.record("build", features={})
            self.assertEqual(learn.history(), [])
        finally:
            os.environ.pop("KIPCB_LEARN")


class ElectricalTests(unittest.TestCase):
    def test_rail_voltages_from_net_names(self):
        cases = {"+3V3": 3.3, "+5V": 5.0, "3V3": 3.3, "+1V8": 1.8, "12V": 12.0, "VBUS": 5.0,
                 "+3.3V": 3.3, "/VCC_3V3": 3.3, "GND": None, "SDA": None}
        for name, volts in cases.items():
            self.assertEqual(spec.rail_voltage(name), volts, name)
        self.assertEqual(spec.rail_voltage("VCC", {"VCC": 3.3}), 3.3)

    def test_package_power_limits(self):
        self.assertEqual(spec.package_watts("Package_TO_SOT_SMD:SOT-223-3_TabPin2"), ("SOT-223", 1.0))
        self.assertEqual(spec.package_watts("Package_TO_SOT_SMD:SOT-23-5"), ("SOT-23", 0.35))


class ExampleSpecTests(unittest.TestCase):
    def test_examples_are_valid_json_with_required_keys(self):
        root = os.path.join(os.path.dirname(__file__), "..", "examples")
        for name in os.listdir(root):
            if name.endswith(".json"):
                with open(os.path.join(root, name)) as f:
                    d = json.load(f)
                for key in ("name", "requirements", "components", "nets"):
                    self.assertIn(key, d, "%s is missing %s" % (name, key))


if __name__ == "__main__":
    unittest.main()
