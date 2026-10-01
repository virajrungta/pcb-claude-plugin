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
                for key in ("name", "requirements"):
                    self.assertIn(key, d, "%s is missing %s" % (name, key))
                self.assertTrue(d.get("components") or d.get("blocks"), "%s has no parts" % name)


class FinePitchTests(unittest.TestCase):
    FP = """(footprint "T:QFN" (attr smd)
      (pad "1" smd roundrect (at -0.4 0) (size 0.2 0.8) (layers "F.Cu"))
      (pad "2" smd roundrect (at 0 0) (size 0.2 0.8) (layers "F.Cu"))
      (pad "3" smd roundrect (at 0.4 0) (size 0.2 0.8) (layers "F.Cu"))
      (pad "4" smd roundrect (at 3 -0.4 90) (size 0.2 0.8) (layers "F.Cu"))
      (pad "5" smd roundrect (at 3 0 90) (size 0.2 0.8) (layers "F.Cu"))
      (pad "6" smd rect (at 6 0) (size 1 1) (layers "F.Cu"))
      (pad "6" smd rect (at 6 0) (size 1 1) (layers "F.Cu")))"""

    def fp(self):
        from kipcb import fplib, sexp
        return fplib.Footprint("T:QFN", "", sexp.loads(self.FP))

    def test_track_limits_from_pitch(self):
        lims = self.fp().track_limits(0.2)
        # 0.4 mm pitch, 0.2 mm pads, 0.2 clearance + 0.01 margin: 2 * (0.4 - 0.1 - 0.21) = 0.18
        self.assertAlmostEqual(lims["2"], 0.18, places=3)
        self.assertAlmostEqual(lims["4"], 0.18, places=3)     # rotated pads use their real width
        self.assertNotIn("6", lims)                            # stacked same-number pads ignored
        self.assertAlmostEqual(self.fp().track_limits(0.15)["2"], 0.28, places=3)
        self.assertAlmostEqual(self.fp().pitch(), 0.4, places=3)

    def test_dsn_boundary_inset(self):
        import tempfile
        from kipcb import route
        with tempfile.NamedTemporaryFile("w", suffix=".dsn", delete=False) as f:
            f.write("(pcb x (structure (boundary (path pcb 0  0 0  10000 0  10000 -10000  0 -10000  0 0)) ))")
        route._inset_boundary(f.name, 300)
        text = open(f.name).read()
        os.remove(f.name)
        self.assertIn("300.0 -300.0", text)
        self.assertIn("9700.0 -9700.0", text)


class PreflightTests(unittest.TestCase):
    def test_routability_learns_from_history(self):
        from kipcb import learn
        rows = [{"kind": "route_final", "features": {"layers": 2, "conn_density": d}, "unconnected": u}
                for d, u in ((5.0, 0), (5.5, 0), (5.2, 3), (12.0, 9))]
        orig = learn.history
        try:
            learn.history = lambda kind=None, include_tainted=False: rows
            r = learn.routability(2, 5.1)
            self.assertEqual((r["ok"], r["n"]), (2, 3))
            self.assertEqual(learn.routability(2, 12.5)["ok"], 0)
            self.assertIsNone(learn.routability(4, 5.0))
        finally:
            learn.history = orig

    def test_report_blocks_on_preflight_failures(self):
        import tempfile
        from kipcb import report, paths
        with tempfile.TemporaryDirectory() as pdir:
            rep = paths.reports(pdir)
            os.makedirs(rep, exist_ok=True)
            with open(os.path.join(rep, "build.json"), "w") as f:
                json.dump({"width": 10, "height": 10}, f)
            with open(os.path.join(rep, "preflight.json"), "w") as f:
                json.dump({"items": [{"check": "pad reach", "level": "FAIL", "message": "x", "fix": "y"}]}, f)
            r = report.collect(pdir, "t")
            self.assertTrue(any(b.startswith("preflight pad reach") for b in r["blockers"]))
            self.assertIn("not routed: fix the preflight problems first", r["blockers"])


class EstimateTests(unittest.TestCase):
    def test_heuristics_and_learning(self):
        from kipcb import estimate, learn
        simple = {"parts": 10, "pads": 40, "connections": 20, "layers": 2, "finest_pitch": None, "fine_parts": []}
        hard = dict(simple, parts=44, pads=200, finest_pitch=0.4, fine_parts=["U3 (RP2040)"])
        self.assertLess(estimate._heuristic(simple)["route"][1], 60)
        self.assertGreaterEqual(estimate._heuristic(hard)["route"][0], 300)
        rows = [{"kind": "run_timings", "features": dict(hard), "timings": {"route": t, "build": 50}}
                for t in (400, 440, 460)]
        orig = learn.history
        try:
            learn.history = lambda kind=None, include_tainted=False: rows
            got = estimate._learned(hard)
            self.assertEqual(got["_n"], 3)
            self.assertAlmostEqual(got["route"][0], 440 * 0.8)
            self.assertEqual(estimate._learned(simple), {})
        finally:
            learn.history = orig
        self.assertEqual(estimate.span(300, 480), "5-8 min")
        self.assertEqual(estimate.fmt(6), "6s")


class V16PlacementTests(unittest.TestCase):
    def test_spread_fills_roomy_board_but_not_pinned_parts(self):
        from kipcb import pcbgen, place

        class D:
            board = {}
            rules = {"edge_clearance": 0.5}
        big = place.Part("U1", (-3, -3, 3, 3), [], {}, 32, 0.6)
        cap = place.Part("C1", (-1, -0.5, 1, 0.5), [], {"near": "U1.1"}, 2, 0.5)
        btn = place.Part("SW1", (-3, -2, 3, 2), [], {}, 2, 0.5)
        res = pcbgen._spread(D(), [big, cap, btn], 60, 40)
        self.assertIsNotNone(res)
        self.assertGreater(big.spread, btn.spread)          # fine-pitch-sized chips get the most room
        self.assertEqual(cap.spread, 0.0)                   # parts pinned near a pin stay close
        pl = place.Placer(60, 40, [big, cap, btn], {})
        self.assertAlmostEqual(pl._pair_gap(big, btn), big.margin + btn.margin + big.spread + btn.spread)
        self.assertAlmostEqual(pl._pair_gap(cap, btn), cap.margin + btn.margin)
        D.board = {"spacing": "compact"}
        self.assertIsNone(pcbgen._spread(D(), [big, btn], 60, 40))

    def test_old_generation_failures_are_ignored(self):
        from kipcb import learn
        rows = [{"kind": "route_final", "auto_size": True, "unconnected": 5, "gen": 1,
                 "features": {"layers": 2, "pads": 100, "area_mm2": 2000}},
                {"kind": "route_final", "auto_size": True, "unconnected": 0, "gen": 1,
                 "features": {"layers": 2, "pads": 100, "area_mm2": 3000}}]
        orig = learn.history
        try:
            learn.history = lambda kind=None, include_tainted=False: rows
            ok, bad = learn._sizing_samples(2, 100)
            self.assertEqual((len(ok), len(bad)), (1, 0))
            rows[0]["gen"] = learn.ROUTER_GEN
            self.assertEqual(len(learn._sizing_samples(2, 100)[1]), 1)
        finally:
            learn.history = orig


class ProgressTests(unittest.TestCase):
    def test_checklist_ticks_through_stages(self):
        import tempfile
        from kipcb import progress
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as pdir:
            old = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = home
            try:
                progress.start(pdir, "t", {"build": "~5s", "route": "~6 min"}, "U1 (RP2040): fine-pitch pins")
                st = {i["name"]: i["status"] for i in progress.load()["items"]}
                self.assertEqual((st["Requirements"], st["Components & circuit"], st["Routing"]), ("done", "active", "todo"))
                progress.stage(pdir, "t", "build", True, 4)
                progress.stage(pdir, "t", "preflight", False, 0, "2 problem(s) to fix")
                items = {i["name"]: i for i in progress.load()["items"]}
                self.assertEqual(items["Placement"]["status"], "done")
                self.assertEqual(items["Placement"]["took"], "4s")
                self.assertEqual(items["Preflight checks"]["status"], "failed")
                line = progress.render_line(progress.load())
                self.assertIn("■■■■✘□□□□ 4/9", line)
                self.assertIn("✘ Preflight: 2 problem(s) to fix", line)
                self.assertNotIn("\n", line)
                cmd = json.dumps({"tool_input": {"command": "cd x && kipcb run t.json --resume"}})
                self.assertIn("PCB · t", json.loads(progress.hook(cmd))["systemMessage"])
                self.assertIsNone(progress.hook(cmd))          # unchanged: not shown again
                self.assertEqual(progress.statusline(), line)
                self.assertIsNone(progress.hook(json.dumps({"tool_input": {"command": "ls"}})))
            finally:
                if old is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = old


if __name__ == "__main__":
    unittest.main()


class V13Tests(unittest.TestCase):
    def test_fmt_keeps_meaning_and_shrinks(self):
        from kipcb import fmt
        import collections
        raw = collections.OrderedDict([
            ("name", "x"), ("power_nets", ["GND", "+3V3"]),
            ("components", [{"ref": "R1", "symbol": "Device:R", "value": "10k"}]),
            ("nets", {"A": ["R1.1", "J1.1"], "GND": ["R1.2", "J1.2"]}),
            ("no_connect", ["U1.5"])])
        text = fmt.format_spec(raw)
        back = json.loads(text)
        self.assertEqual(back["nets"]["A"].split(), ["R1.1", "J1.1"])
        self.assertEqual(back["power_nets"], "GND +3V3")
        self.assertEqual(back["no_connect"], "U1.5")
        self.assertEqual(back["components"], raw["components"])
        self.assertLess(len(text), len(json.dumps(raw, indent=2)))

    def test_guide_finds_single_sections(self):
        from kipcb import guide
        import io, contextlib
        for topic, must in (("usb-c", "5.1 k"), ("i2c", "pull-up"), ("crystal", "load")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(guide.show(topic), 0)
            self.assertIn(must, buf.getvalue())
            self.assertLess(len(buf.getvalue()), 3000)

    def test_report_status_and_location(self):
        from kipcb import report
        with tempfile.TemporaryDirectory() as pdir:
            rep = os.path.join(pdir, "reports")
            os.makedirs(rep)
            dump = lambda n, d: json.dump(d, open(os.path.join(rep, n), "w"))
            dump("build.json", {"title": "T", "width": 30, "height": 20, "layers": 2, "parts": 5, "nets": 4,
                                "placement_failed": [], "notes": [], "spec_warnings": [], "spec_notes": []})
            r = report.collect(pdir, "t")
            self.assertEqual(report._status(r), "BUILT, NOT ROUTED")
            dump("route.json", {"unconnected": 0, "vias": 2, "chosen": "x"})
            dump("drc.json", {"violations": [], "unconnected_items": [], "schematic_parity": []})
            dump("noise.json", [{"check": "decoupling", "level": "PASS", "message": "ok"}])
            dump("fab.json", {"files": ["fab/t-gerbers.zip"], "missing_lcsc": [], "parity_issues": 0})
            r = report.write(pdir, "t", {"build": 3, "route": 5})
            out = report.text(r)
            self.assertTrue(out.startswith("READY TO ORDER"))
            self.assertIn(os.path.join(pdir, "t.kicad_pro"), out)
            self.assertTrue(os.path.exists(os.path.join(rep, "REPORT.md")))


class BlockExpansionTests(unittest.TestCase):
    def test_part_shorthand_fills_symbol_footprint_and_lcsc(self):
        from kipcb import blocks
        c = blocks.expand_part({"ref": "R1", "part": "r0603", "value": "10K"})
        self.assertEqual(c["symbol"], "Device:R")
        self.assertEqual(c["footprint"], "Resistor_SMD:R_0603_1608Metric")
        self.assertEqual(c["lcsc"], "C25804")
        self.assertNotIn("lcsc", blocks.expand_part({"ref": "R2", "part": "R0603", "value": "36.5k"}))
        h = blocks.expand_part({"ref": "J1", "part": "HEADER_1x06"})
        self.assertEqual(h["symbol"], "Connector_Generic:Conn_01x06")
        self.assertIn("PinHeader_1x06", h["footprint"])
        with self.assertRaises(blocks.BlockError):
            blocks.expand_part({"ref": "X1", "part": "NOPE"})

    def test_catalog_value_matching(self):
        from kipcb import blocks
        part = lambda name, value: blocks.expand_part({"ref": "X1", "part": name, "value": value})
        self.assertEqual(part("R0603", "0")["lcsc"], part("R0603", "0R")["lcsc"])       # zero ohm
        self.assertEqual(part("C0603", "100nF")["lcsc"], part("C0603", "0.1uF")["lcsc"])
        self.assertEqual(part("R0402", "4k7")["lcsc"], part("R0402", "4.7k")["lcsc"])
        self.assertEqual(part("LED0805", "Green LED")["lcsc"], part("LED0805", "green")["lcsc"])
        self.assertIn("lcsc", part("R1206", "0.1"))                                     # 100 mohm shunt
        c = part("C1206", "22uF")
        self.assertEqual(c["voltage_rating"], 25)
        self.assertTrue(c["footprint_checked"])
        self.assertNotIn("footprint_checked", blocks.expand_part(
            {"ref": "C1", "part": "C0603", "value": "1uF", "footprint": "Capacitor_SMD:C_0805_2012Metric"}))

    def test_catalog_lcsc_numbers_are_well_formed(self):
        import re
        from kipcb import blocks
        for name, e in blocks.builtin_parts().items():
            codes = [e["lcsc"]] if isinstance(e.get("lcsc"), str) else list((e.get("lcsc") or {}).values())
            for code in codes:
                self.assertRegex(code, r"^C\d+$", name)
        for name, b in blocks.builtin_blocks().items():
            for c in b["components"]:
                if "part" in c:
                    self.assertIsNotNone(blocks.lookup_part(c["part"]), "%s: %s" % (name, c["part"]))

    def test_block_omit_drops_parts_and_their_pins(self):
        from kipcb import blocks
        raw = {"name": "t", "blocks": [{"use": "can_sn65hvd230", "omit": ["R1"]}]}
        spec, _ = blocks.expand(raw)
        self.assertEqual([c["ref"] for c in spec["components"] if c["ref"].startswith("R")], [])
        self.assertEqual(spec["nets"]["CANH"], ["U1.CANH"])
        with self.assertRaises(blocks.BlockError):
            blocks.expand({"blocks": [{"use": "can_sn65hvd230", "omit": ["R9"]}]})

    def test_block_internal_power_net_is_registered(self):
        from kipcb import blocks
        spec, _ = blocks.expand({"name": "t", "blocks": [{"use": "drv8833_motor"}]})
        self.assertIn("drv8833_motor_VINT", spec["power_nets"])

    def test_capacitor_voltage_rating_warning(self):
        from kipcb.spec import Design
        d = Design.__new__(Design)
        d.raw = {}
        d.warnings, d.notes = [], []

        class P:
            pass
        c = P()
        c.ref, c.value, c.d = "C1", "47uF", {"voltage_rating": 6.3}
        c.sym = P(); c.sym.pins = [{"number": "1", "type": "passive"}, {"number": "2", "type": "passive"}]
        d.components = [c]
        d.nets = {"+12V": [("C1", "1")], "GND": [("C1", "2")]}
        d.pin_net = {("C1", "1"): "+12V", ("C1", "2"): "GND"}
        d.power_nets = {"+12V", "GND"}
        d.by_ref = {"C1": c}
        d.electrical_checks()
        self.assertTrue(any("rated 6.3V" in w for w in d.warnings), d.warnings)

    def test_bom_stock_check_is_silent_offline(self):
        from kipcb import lcsc
        orig = lcsc.lookup
        try:
            lcsc.lookup = lambda codes, refresh=False: {c: {"code": c, "found": False, "error": "network"} for c in codes}
            self.assertEqual(lcsc.check_bom(["C1", "C2"]), [])
            lcsc.lookup = lambda codes, refresh=False: {"C1": {"code": "C1", "found": True, "stock": 0, "mpn": "X"},
                                                        "C2": {"code": "C2", "found": False}}
            msgs = dict(lcsc.check_bom(["C1", "C2"]))
            self.assertIn("out of stock", msgs["C1"])
            self.assertIn("not a", msgs["C2"])
        finally:
            lcsc.lookup = orig

    def test_blocks_renumber_connect_and_handle_optional_ports(self):
        from kipcb import blocks
        raw = {"name": "t", "power_nets": "GND",
               "components": [{"ref": "R1", "part": "R0603", "value": "1k"}],
               "nets": {"GND": "R1.2"},
               "blocks": [{"use": "led_indicator", "connect": {"IN": "STATUS"}, "params": {"color": "Red"}},
                          {"use": "atmega328p_16mhz", "connect": {"+5V": "VBUS", "PD2": "BTN"}}]}
        spec, summary = blocks.expand(raw)
        refs = [c["ref"] for c in spec["components"]]
        self.assertEqual(len(refs), len(set(refs)))              # no clashes with the explicit R1
        led = [c for c in spec["components"] if c.get("group") == "led_indicator"]
        self.assertEqual({c["ref"] for c in led}, {"R2", "D1"})
        self.assertIn("Red", [c.get("value") for c in led])        # params substituted
        self.assertIn("R2.1", spec["nets"]["STATUS"])
        self.assertIn("D1.K", spec["nets"]["GND"])                  # default: port name -> same net
        self.assertIn("R1.2", spec["nets"]["GND"])                  # merged with the spec's own net
        self.assertIn("VBUS", spec["power_nets"])                   # power port registered
        u = [c["ref"] for c in spec["components"] if c.get("value") == "ATmega328P-AU"][0]
        self.assertIn("%s.PD2" % u, spec["nets"]["BTN"])
        self.assertIn("%s.PB0" % u, spec["no_connect"])             # unused optional single pin
        self.assertIn("atmega328p_16mhz_RESET", spec["nets"])       # unused optional, but joins 2 parts
        self.assertNotIn("blocks", spec)
        with self.assertRaises(blocks.BlockError):
            blocks.expand({"blocks": [{"use": "led_indicator", "connect": {"NOPE": "X"}}]})
