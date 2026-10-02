"""CI diagnostic: replay kipcb's build step by step in one process and run kicad-cli ERC after
each step, to find which step makes kicad-cli crash when started from kipcb (Windows)."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath("scripts"))
from kipcb import kienv, schgen, pcbgen, build as buildmod   # noqa: E402
from kipcb.spec import Design                                 # noqa: E402

spec = sys.argv[1]
d = Design(spec)
pdir = os.path.join(tempfile.mkdtemp(), d.name)
os.makedirs(pdir)
sch = os.path.join(pdir, d.name + ".kicad_sch")
pcb = os.path.join(pdir, d.name + ".kicad_pcb")


def erc(label):
    rpt = os.path.join(pdir, "erc_%s.json" % label)
    p = kienv.run_cli(["sch", "erc", "--format", "json", "--severity-all", "-o", rpt, sch], check=False)
    print("%-28s exit %s, report %s" % (label, p.returncode, "yes" if os.path.exists(rpt) else "NO"))


buildmod._write_lib_tables(d, pdir)
sb = schgen.write(d, sch)
erc("after schematic")
import pcbnew                                                 # noqa: E402
erc("after import pcbnew")
info = pcbgen.build(d, sb, pcb, None)
erc("after board build+save")
print("files:", sorted(os.listdir(pdir)))
