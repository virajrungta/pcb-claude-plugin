"""V2.4: schematic inspection against how real designs wire each pin."""

import unittest

from kipcb import schinspect, wiring


def _rec(with_input_cap=True, out_cap="22uF"):
    comps = [{"ref": "U1", "value": "AMS1117-3.3", "lib": "Regulator_Linear", "part": "AMS1117-3.3"},
             {"ref": "C2", "value": out_cap, "lib": "Device", "part": "C"}]
    nets = [{"name": "GND", "nodes": [["U1", "1", "GND", "power_in"], ["C2", "2", "", "passive"]]},
            {"name": "+3V3", "nodes": [["U1", "2", "VO", "power_out"], ["C2", "1", "", "passive"]]},
            {"name": "VBUS", "nodes": [["U1", "3", "VI", "power_in"]]}]
    if with_input_cap:
        comps.append({"ref": "C1", "value": "10uF", "lib": "Device", "part": "C"})
        nets[0]["nodes"].append(["C1", "2", "", "passive"])
        nets[2]["nodes"].append(["C1", "1", "", "passive"])
    return {"components": comps, "nets": nets}


KB = {"parts": {"AMS1117-3.3": {
    "designs": 17, "lib": "Regulator_Linear:AMS1117-3.3",
    "pins": {"VI": [["C 10uF -> GND", 9]], "VO": [["C 22uF -> GND", 12]], "GND": [["tied to GND", 17]]},
    "shapes": {"VI": [["C -> GND", 15], ["tied to supply", 14]], "VO": [["C -> GND", 15]],
               "GND": [["tied to GND", 17]]}}}, "aliases": {}}


class ShapeTests(unittest.TestCase):
    def test_shapes_drop_values_and_rail_names(self):
        self.assertEqual(wiring.shape("C 100nF -> GND"), "C -> GND")
        self.assertEqual(wiring.shape("R 10k -> +3V3"), "R -> supply")
        self.assertEqual(wiring.shape("R 10k -> own pin EN"), "R -> own pin EN")
        self.assertEqual(wiring.shape("tied to +3.3V"), "tied to supply")
        self.assertIsNone(wiring.shape("to connector"))


class SchematicInspectTests(unittest.TestCase):
    def test_good_schematic_is_clean(self):
        found = schinspect.inspect(_rec(), KB)
        self.assertEqual(found[0][0], "ok")
        self.assertEqual([f for f in found[1:] if f[0] == "warn"], [])

    def test_missing_input_cap_is_flagged(self):
        warns = [t for lvl, t in schinspect.inspect(_rec(with_input_cap=False), KB) if lvl == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("pin VI has no capacitor to GND; 15 of 17", warns[0])

    def test_unusual_value_is_only_a_note(self):
        found = schinspect.inspect(_rec(out_cap="1uF"), KB)
        notes = [t for lvl, t in found if lvl == "info"]
        self.assertTrue(any("pin VO: 1uF here; real designs mostly use 22uF" in t for t in notes))
        self.assertFalse(any(lvl == "warn" for lvl, _ in found))

    def test_unknown_parts_are_skipped_not_guessed(self):
        rec = _rec()
        rec["components"][0].update(value="AMS1117-5.0", part="AMS1117-5.0")
        found = schinspect.inspect(rec, KB)
        self.assertEqual(len(found), 1)
        self.assertIn("none of the chips", found[0][1])


if __name__ == "__main__":
    unittest.main()
