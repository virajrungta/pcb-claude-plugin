"""Where kipcb puts things inside a project folder.

    <project>/
      <name>.kicad_pro/.kicad_sch/.kicad_pcb   the KiCad project
      fab/        files to send to the manufacturer
      previews/   images and PDFs to look at
      reports/    REPORT.md plus ERC / DRC / noise / placement results
      .kipcb/     working files (router input/output, logs); safe to delete
"""

import os


def _mk(*parts):
    d = os.path.join(*parts)
    os.makedirs(d, exist_ok=True)
    return d


def reports(pdir):
    return _mk(pdir, "reports")


def previews(pdir):
    return _mk(pdir, "previews")


def work(pdir):
    return _mk(pdir, ".kipcb")


def fab(pdir):
    return _mk(pdir, "fab")
