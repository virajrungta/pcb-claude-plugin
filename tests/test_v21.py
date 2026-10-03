"""V2.1: placement knowledge (away_from distances, R/C alignment), layout score, atomic files."""

import json
import os
import tempfile
import unittest

from kipcb import paths, place, placestats


def _rc(ref, net_a, net_b, hint=None):
    return place.Part(ref, (-1, -0.5, 1, 0.5), [("1", -0.5, 0, net_a), ("2", 0.5, 0, net_b)], hint or {}, 2, margin=0.4)


class AwayTests(unittest.TestCase):
    def test_hint_forms(self):
        self.assertEqual(place._away({"away_from": "L1"}), [("L1", 8.0)])
        self.assertEqual(place._away({"away_from": ["L1", "U2"], "min_dist": 5}), [("L1", 5.0), ("U2", 5.0)])
        self.assertEqual(sorted(place._away({"away_from": {"L1": 15, "U2": 10}})), [("L1", 15.0), ("U2", 10.0)])
        self.assertEqual(place._away({}), [])

    def test_sensor_keeps_its_distance_even_when_wired_to_the_heat_source(self):
        # the sensor's only connections are to U1, so its nearest spots are all too close:
        # the full-board search must still find a spot >= 15 mm away
        hot = place.Part("U1", (-3, -3, 3, 3), [("1", -2.5, 0, "SDA"), ("2", 2.5, 0, "SCL")], {}, 8, margin=0.5)
        sensor = place.Part("U2", (-1.5, -1.5, 1.5, 1.5), [("1", -1, 0, "SDA"), ("2", 1, 0, "SCL")],
                            {"away_from": {"U1": 15}}, 8, margin=0.5)
        hot.x, hot.y, hot.fixed = 10.0, 10.0, True
        pl = place.Placer(60, 40, [hot, sensor], {"SDA": 2, "SCL": 2})
        self.assertEqual(pl.run(), [])
        self.assertGreaterEqual(place._box_dist(sensor.box(), hot.box()), 15.0 - 1e-6)


class AlignTests(unittest.TestCase):
    def test_passives_turn_to_the_dominant_orientation(self):
        parts = [_rc("R%d" % i, "N%d" % i, "M%d" % i) for i in range(1, 6)]
        for i, p in enumerate(parts):
            p.x, p.y = 5.0 + 6 * i, 10.0
            p.rot = 90 if i == 0 else 0
        pl = place.Placer(40, 20, parts, {})
        self.assertEqual(pl.align_passives(), 1)
        self.assertEqual({p.rot % 180 for p in parts}, {0})

    def test_pinned_or_fixed_parts_stay(self):
        parts = [_rc("R%d" % i, "N", "M") for i in range(1, 6)]
        for i, p in enumerate(parts):
            p.x, p.y, p.rot = 5.0 + 6 * i, 10.0, 0
        parts[0].rot, parts[0].fixed = 90, True
        self.assertEqual(place.Placer(40, 20, parts, {}).align_passives(), 0)
        self.assertEqual(parts[0].rot, 90)


class ScoreTests(unittest.TestCase):
    KB = {"2": {k: {"n": 100, "p10": a, "p25": b, "p50": c, "p75": d, "p90": e} for k, (a, b, c, d, e) in {
        "density_peak": (0.5, 0.7, 0.9, 1.1, 1.3), "rc_orientation_share": (0.4, 0.55, 0.69, 0.85, 1.0),
        "connector_edge_mm": (0, 0.5, 1.2, 3, 6), "decoupling_mm": (0.5, 1.2, 2.3, 4, 7)}.items()}}

    def test_typical_layout_scores_high_and_a_bad_one_low(self):
        good = {"density_peak": 0.8, "rc_orientation_share": 0.9, "connector_edge": [0.5, 1], "decoupling": [1, 2]}
        bad = {"density_peak": 1.6, "rc_orientation_share": 0.3, "connector_edge": [9, 12], "decoupling": [9, 12]}
        g, notes = placestats.score(good, self.KB, 2)
        b, bad_notes = placestats.score(bad, self.KB, 2)
        self.assertEqual(g, 100)
        self.assertEqual(notes, [])
        self.assertLess(b, 30)
        self.assertEqual(len(bad_notes), 4)

    def test_no_knowledge_no_score(self):
        self.assertEqual(placestats.score({"density_peak": 1}, None), (None, []))


class FileTests(unittest.TestCase):
    def test_atomic_json_roundtrip_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "x.json")
            paths.save_json(p, {"a": [1, 2]})
            self.assertEqual(paths.load_json(p, None), {"a": [1, 2]})
            self.assertEqual([f for f in os.listdir(os.path.dirname(p))], ["x.json"])   # no temp files left
            self.assertEqual(paths.load_json(os.path.join(d, "missing.json"), {}), {})

    def test_project_spec_prefers_the_recorded_path(self):
        with tempfile.TemporaryDirectory() as d:
            pdir = os.path.join(d, "out", "board")
            elsewhere = os.path.join(d, "specs", "board.json")
            paths.save_json(elsewhere, {"name": "board", "board": {"ground_pour": "GND"}})
            paths.save_json(os.path.join(pdir, "reports", "build.json"), {"spec": elsewhere})
            self.assertEqual(paths.project_spec(pdir, "board")["board"]["ground_pour"], "GND")
            with open(os.path.join(pdir, "reports", "build.json"), "w") as f:
                json.dump({}, f)
            paths.save_json(os.path.join(d, "out", "board.json"), {"name": "guess"})
            self.assertEqual(paths.project_spec(pdir, "board")["name"], "guess")


if __name__ == "__main__":
    unittest.main()
