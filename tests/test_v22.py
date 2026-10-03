"""V2.2: board sizing learned from real routed boards (nearest neighbours)."""

import random
import unittest

from kipcb import placestats, sizemodel


def _row(layers, pads, parts, conns, court, fine, board):
    return {"layers": layers, "pads": pads, "parts": parts, "conns": conns, "court_area": court,
            "fine": fine, "board_area": board}


class DesignFeatureTests(unittest.TestCase):
    def test_counts_connections_area_and_fine_pitch(self):
        qfn = (5.0, 5.0, [(i * 0.5, 0.0, "N%d" % (i % 3)) for i in range(8)])     # 0.5 mm pitch
        cap = (2.0, 1.0, [(-0.5, 0.0, "N0"), (0.5, 0.0, "GND")])
        f = placestats.design_features([qfn, cap])
        self.assertEqual(f["pads"], 10)
        self.assertEqual(f["parts"], 2)
        self.assertEqual(f["conns"], 6)               # N0: 4 pads, N1: 3, N2: 2, GND: 1 -> 3+2+1
        self.assertEqual(f["court_area"], 27.0)
        self.assertEqual(f["fine"], 0.8)

    def test_two_pad_parts_are_never_fine_pitch(self):
        self.assertFalse(placestats.fine_pitch([(0, 0), (0.4, 0)]))
        self.assertTrue(placestats.fine_pitch([(0, 0), (0.4, 0), (0.8, 0)]))


class SizingModelTests(unittest.TestCase):
    def setUp(self):
        rnd = random.Random(1)
        self.rows = []
        # two kinds of real board: sparse connector boards (20% full) and dense
        # fine-pitch boards (50% full)
        for _ in range(40):
            pads = rnd.randint(30, 60)
            self.rows.append(_row(2, pads, pads // 2, pads // 2, 400.0, 0.0, 2000.0))
            pads = rnd.randint(150, 250)
            self.rows.append(_row(2, pads, pads // 4, pads, 800.0, 0.6, 1600.0))

    def test_similar_boards_decide_the_fill(self):
        sparse = {"pads": 45, "parts": 22, "conns": 22, "court_area": 400.0, "fine": 0.0}
        dense = {"pads": 200, "parts": 50, "conns": 200, "court_area": 800.0, "fine": 0.6}
        self.assertAlmostEqual(sizemodel.fill(sparse, 2, rows=self.rows)["p50"], 0.2, places=2)
        self.assertAlmostEqual(sizemodel.fill(dense, 2, rows=self.rows)["p50"], 0.5, places=2)

    def test_too_few_boards_says_nothing(self):
        f = {"pads": 45, "parts": 22, "conns": 22, "court_area": 400.0, "fine": 0.0}
        self.assertIsNone(sizemodel.fill(f, 2, rows=self.rows[:5]))


class KnowledgePathTests(unittest.TestCase):
    def test_newest_copy_wins(self):
        import json
        import os
        import tempfile
        from kipcb import knowledge
        with tempfile.TemporaryDirectory() as d:
            bundled, local_dir = os.path.join(d, "bundled.json"), os.path.join(d, "data")
            os.makedirs(local_dir)
            local = os.path.join(local_dir, "knowledge.json")
            env = {k: os.environ.pop(k) for k in ("KIPCB_KNOWLEDGE", "XDG_DATA_HOME") if k in os.environ}
            old_bundled = knowledge.BUNDLED
            try:
                os.environ["XDG_DATA_HOME"] = d
                os.rename(local_dir, os.path.join(d, "kipcb"))
                local = os.path.join(d, "kipcb", "knowledge.json")
                knowledge.BUNDLED = bundled
                for path, built in ((bundled, "2026-10-03T10:00:00"), (local, "2026-10-02")):
                    with open(path, "w") as f:
                        json.dump({"version": 1, "built": built}, f, indent=1)
                self.assertEqual(knowledge.kb_path(), bundled)      # plugin update is newer
                with open(local, "w") as f:
                    json.dump({"version": 1, "built": "2026-10-04T09:00:00"}, f, indent=1)
                self.assertEqual(knowledge.kb_path(), local)        # fresh pkb build is newer
            finally:
                knowledge.BUNDLED = old_bundled
                os.environ.pop("XDG_DATA_HOME", None)
                os.environ.update(env)


if __name__ == "__main__":
    unittest.main()
