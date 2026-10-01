"""Every prebuilt block must validate against the real KiCad libraries.

Needs KiCad's symbol/footprint libraries, so it is skipped where they aren't
installed (e.g. CI). Run locally before each release:
    PYTHONPATH=scripts python3 -m unittest tests.test_blocks_kicad -v
"""

import json
import os
import tempfile
import unittest

from kipcb import blocks, kienv

HAVE_KICAD = bool(kienv.shared_support()) and os.path.isdir(os.path.join(kienv.shared_support() or "", "symbols"))


@unittest.skipUnless(HAVE_KICAD, "KiCad libraries not installed")
class BlocksValidate(unittest.TestCase):
    def _check(self, name, wire_optional):
        from kipcb.spec import Design
        b = blocks.builtin_blocks()[name]
        ports = [n[1:] for n in b["nets"] if n.startswith("@")]
        optional = set(b.get("optional", []))
        connect = {p: ("N_" + p.replace("+", "P")) for p in ports if wire_optional or p not in optional}
        spec = {"name": "t_" + name, "board": {"width": 60, "height": 50},
                "blocks": [{"use": name, "connect": connect}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.json")
            with open(path, "w") as f:
                json.dump(spec, f)
            design = Design(path)
            design.validate()
        return design

    def test_every_block_is_clean(self):
        for name in sorted(blocks.builtin_blocks()):
            for wire_optional in (True, False):
                with self.subTest(block=name, optional_ports_wired=wire_optional):
                    d = self._check(name, wire_optional)
                    self.assertEqual(d.errors, [], "\n".join(d.errors))

    def test_parts_catalog_resolves(self):
        from kipcb import fplib, symlib
        syms, fps = kienv.lib_table("sym"), kienv.lib_table("fp")
        for name in list(blocks.builtin_parts()) + ["HEADER_1x04", "HEADER_2x03"]:
            if "NN" in name:
                continue
            with self.subTest(part=name):
                e = blocks.lookup_part(name)
                sym = symlib.load(syms, e["symbol"])
                fplib.load(fps, e.get("footprint") or sym.footprint)
