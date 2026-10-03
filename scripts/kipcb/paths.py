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


# ------------------------------------------------------------------ safe file writes

def atomic_write(path, text):
    """Write a file so a crash or a full disk can never leave it half-written: write a
    temporary file next to it, then swap it in. Used for files people or other tools own
    (Claude Code's settings.json, a spec rewritten by `kipcb fmt`) and long-lived state."""
    import tempfile
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".kipcb-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def save_json(path, data, indent=1):
    import json
    atomic_write(path, json.dumps(data, indent=indent) + "\n")


def load_json(path, default=None):
    import json
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def project_spec(pdir, name):
    """The raw design spec a project was built from: the path the build recorded in
    reports/build.json, else <name>.json next to or inside the project folder. {} if none."""
    built = load_json(os.path.join(pdir, "reports", "build.json"), {}) or {}
    cands = [built.get("spec")] if built.get("spec") else []
    cands += [os.path.join(os.path.dirname(pdir), name + ".json"), os.path.join(pdir, name + ".json")]
    for cand in cands:
        if cand and os.path.exists(cand):
            return load_json(cand, {}) or {}
    return {}
