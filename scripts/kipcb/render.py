"""Render previews into <project>/previews/.

Images are sized for review, not print: every image Claude looks at costs
tokens, so the per-iteration preview is a single ~1100 px top view.

  what="build"   schematic.pdf/.png + review.png        (after a build)
  what="review"  review.png                             (after routing)
  what="full"    everything, incl. bottom view and a printable board PDF
"""

import os
import platform
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import kienv

REVIEW_SIZE = ("1100", "800")
SCHEMATIC_PX = 1400


def _pdf_to_png(pdf, png, width=SCHEMATIC_PX):
    """Rasterise the first page of a PDF. Uses sips (macOS) or pdftoppm."""
    if not os.path.exists(pdf):
        return None
    if platform.system() == "Darwin" and shutil.which("sips"):
        subprocess.run(["sips", "-s", "format", "png", "-Z", str(width), pdf, "--out", png],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-png", "-singlefile", "-scale-to", str(width), pdf, png[:-4]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return png if os.path.exists(png) else None


def _render_3d(pcb, png, side, size):
    kienv.run_cli(["pcb", "render", "--side", side, "--quality", "basic", "--width", size[0],
                   "--height", size[1], "--zoom", "1", "-o", png, pcb], check=False)
    return png if os.path.exists(png) else None


def _schematic(sch, out):
    pdf = os.path.join(out, "schematic.pdf")
    kienv.run_cli(["sch", "export", "pdf", "--no-background-color", "-o", pdf, sch], check=False)
    png = _pdf_to_png(pdf, os.path.join(out, "schematic.png"))
    return [p for p in (pdf if os.path.exists(pdf) else None, png) if p]


def _board_pdf(pcb, out):
    pdf = os.path.join(out, "board.pdf")
    kienv.run_cli(["pcb", "export", "pdf", "--mode-multipage", "--include-border-title",
                   "--layers", "F.Cu,F.Silkscreen,Edge.Cuts,B.Cu,B.Silkscreen", "-o", pdf, pcb], check=False)
    return pdf if os.path.exists(pdf) else None


def render(pdir, name, what="build"):
    from . import paths
    out = paths.previews(pdir)
    # names from older versions
    what = {"all": "full", "sch": "build", "pcb": "review", "3d": "review"}.get(what, what)
    sch = os.path.join(pdir, name + ".kicad_sch")
    pcb = os.path.join(pdir, name + ".kicad_pcb")
    jobs = []
    if what in ("build", "full") and os.path.exists(sch):
        jobs.append(lambda: _schematic(sch, out))
    if os.path.exists(pcb):
        jobs.append(lambda: [_render_3d(pcb, os.path.join(out, "review.png"), "top", REVIEW_SIZE)])
        if what == "full":
            jobs.append(lambda: [_render_3d(pcb, os.path.join(out, "bottom.png"), "bottom", REVIEW_SIZE)])
            jobs.append(lambda: [_board_pdf(pcb, out)])
    # kicad-cli renders are independent processes: run them side by side
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda job: job(), jobs))
    return [p for group in results for p in group if p]
