"""Render PNG previews so the design can be inspected visually."""

import os
import platform
import shutil
import subprocess

from . import kienv


def _pdf_to_png(pdf, png, width=2400):
    """Rasterise the first page of a PDF. Uses sips (macOS) or pdftoppm."""
    if platform.system() == "Darwin" and shutil.which("sips"):
        subprocess.run(["sips", "-s", "format", "png", "-Z", str(width), pdf, "--out", png],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-png", "-singlefile", "-scale-to", str(width), pdf, png[:-4]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return png if os.path.exists(png) else None


def render(pdir, name, what="all"):
    out = os.path.join(pdir, "out")
    os.makedirs(out, exist_ok=True)
    sch = os.path.join(pdir, name + ".kicad_sch")
    pcb = os.path.join(pdir, name + ".kicad_pcb")
    made = []
    if what in ("all", "sch") and os.path.exists(sch):
        pdf = os.path.join(out, "schematic.pdf")
        kienv.run_cli(["sch", "export", "pdf", "--no-background-color", "-o", pdf, sch], check=False)
        png = _pdf_to_png(pdf, os.path.join(out, "schematic.png"))
        made += [p for p in (pdf, png) if p]
    if what in ("all", "pcb") and os.path.exists(pcb):
        # printable 2D plot (one page per side) for humans; Claude reads the PNG renders below
        pdf = os.path.join(out, "pcb_layout.pdf")
        kienv.run_cli(["pcb", "export", "pdf", "--mode-multipage", "--include-border-title",
                       "--layers", "F.Cu,F.Silkscreen,Edge.Cuts,B.Cu,B.Silkscreen",
                       "-o", pdf, pcb], check=False)
        if os.path.exists(pdf):
            made.append(pdf)
    if what in ("all", "3d", "pcb") and os.path.exists(pcb):
        for side in ("top", "bottom"):
            png = os.path.join(out, "pcb_3d_%s.png" % side)
            kienv.run_cli(["pcb", "render", "--side", side, "--quality", "basic", "--width", "1600",
                           "--height", "1200", "--zoom", "1", "-o", png, pcb], check=False)
            if not os.path.exists(png):
                kienv.run_cli(["pcb", "render", "--side", side, "-o", png, pcb], check=False)
            if os.path.exists(png):
                made.append(png)
    return made
