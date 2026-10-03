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
    """Rasterise the first page of a PDF with sips (macOS) or pdftoppm, when available."""
    if not os.path.exists(pdf) or os.environ.get("KIPCB_FORCE_SVG_RASTER"):
        return None
    if platform.system() == "Darwin" and shutil.which("sips"):
        cmd = ["sips", "-s", "format", "png", "-Z", str(width), pdf, "--out", png]
    elif shutil.which("pdftoppm"):
        cmd = ["pdftoppm", "-png", "-singlefile", "-scale-to", str(width), pdf, png[:-4]]
    else:
        return None
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    return png if os.path.exists(png) else None


_WX_RASTER = r"""
import sys, wx, wx.svg
svg, png, width = sys.argv[1], sys.argv[2], int(sys.argv[3])
app = wx.App(False)
img = wx.svg.SVGimage.CreateFromFile(svg)
w = width
h = int(round(img.height * w / float(img.width)))
bmp = wx.Bitmap(w, h, 32)
dc = wx.MemoryDC(bmp)
dc.SetBackground(wx.WHITE_BRUSH)
dc.Clear()
gc = wx.GraphicsContext.Create(dc)
img.RenderToGC(gc, scale=w / float(img.width))
del gc
dc.SelectObject(wx.NullBitmap)
sys.exit(0 if bmp.ConvertToImage().SaveFile(png, wx.BITMAP_TYPE_PNG) else 1)
"""


def _svg_to_png(svg, png, width=SCHEMATIC_PX):
    """Rasterise an SVG with wxPython's renderer, which KiCad bundles on every platform (how
    previews are made on Windows, where there's no sips or pdftoppm). Runs in its own process:
    wx must own the main thread, and previews are rendered in worker threads."""
    import sys
    try:
        p = subprocess.run([sys.executable, "-c", _WX_RASTER, svg, png, str(int(width))],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if not os.path.exists(png):
        from . import ui
        ui.say("schematic preview: rasterising the SVG failed:\n" + (p.stdout or "")[-800:])
        return None
    return png


def _browser():
    """A Chromium-based browser for headless SVG screenshots (Edge ships with Windows 10/11)."""
    env = os.environ.get("KIPCB_BROWSER")
    if env and os.path.exists(env):
        return env
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    cands = []
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def _svg_to_png_browser(svg, png, width=SCHEMATIC_PX):
    """Screenshot the SVG in a headless browser (Windows: Edge), sized to the drawing."""
    import re
    import tempfile
    exe = _browser()
    if not exe:
        return None
    with open(svg, encoding="utf-8", errors="replace") as f:
        head = f.read(4000)
    m = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', head)
    ratio = float(m.group(2)) / float(m.group(1)) if m else 0.707
    w = int(width)
    h = max(100, int(round(w * ratio)))
    tmp = tempfile.mkdtemp(prefix="kipcb_svg_")
    html = os.path.join(tmp, "page.html")
    with open(html, "w", encoding="utf-8") as f:
        f.write('<html><body style="margin:0;background:#fff">'
                '<img src="file:///%s" style="width:%dpx;height:%dpx;display:block"></body></html>'
                % (os.path.abspath(svg).replace("\\", "/"), w, h))
    url = "file:///" + html.replace("\\", "/").lstrip("/")
    try:
        subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        "--allow-file-access-from-files", "--user-data-dir=" + os.path.join(tmp, "profile"),
                        "--window-size=%d,%d" % (w, h), "--screenshot=" + os.path.abspath(png), url],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pass
    shutil.rmtree(tmp, ignore_errors=True)
    return png if os.path.exists(png) and os.path.getsize(png) > 0 else None


def _render_3d(pcb, png, side, size):
    kienv.run_cli(["pcb", "render", "--side", side, "--quality", "basic", "--width", size[0],
                   "--height", size[1], "--zoom", "1", "-o", png, pcb], check=False)
    return png if os.path.exists(png) else None


def _schematic(sch, out):
    pdf = os.path.join(out, "schematic.pdf")
    kienv.run_cli(["sch", "export", "pdf", "--no-background-color", "-o", pdf, sch], check=False)
    png = _pdf_to_png(pdf, os.path.join(out, "schematic.png"))
    if png is None:            # no PDF rasteriser (Windows): KiCad's SVG export + wx
        tmp = os.path.join(out, ".svg")
        os.makedirs(tmp, exist_ok=True)
        kienv.run_cli(["sch", "export", "svg", "--exclude-drawing-sheet", "--no-background-color",
                       "-o", tmp, sch], check=False)
        svgs = sorted(f for f in os.listdir(tmp) if f.endswith(".svg"))
        if svgs:
            svg = os.path.join(tmp, svgs[0])
            png = _svg_to_png(svg, os.path.join(out, "schematic.png"))
            if png is None:      # KiCad's Windows wx has no SVG renderer: use Edge / Chrome
                png = _svg_to_png_browser(svg, os.path.join(out, "schematic.png"))
        shutil.rmtree(tmp, ignore_errors=True)
    return [p for p in (pdf if os.path.exists(pdf) else None, png) if p]


def _board_pdf(pcb, out):
    pdf = os.path.join(out, "board.pdf")
    if os.path.isdir(pdf):
        shutil.rmtree(pdf, ignore_errors=True)
    kienv.run_cli(["pcb", "export", "pdf", "--mode-multipage", "--include-border-title",
                   "--layers", "F.Cu,F.Silkscreen,Edge.Cuts,B.Cu,B.Silkscreen", "-o", pdf, pcb], check=False)
    if os.path.isdir(pdf):
        # kicad-cli on Windows treats -o as a folder in multi-page mode: keep the file it wrote
        inner = sorted(f for f in os.listdir(pdf) if f.lower().endswith(".pdf"))
        tmp = pdf + ".tmp"
        if inner:
            shutil.move(os.path.join(pdf, inner[0]), tmp)
        shutil.rmtree(pdf, ignore_errors=True)
        if os.path.exists(tmp):
            os.replace(tmp, pdf)
    return pdf if os.path.isfile(pdf) else None


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
