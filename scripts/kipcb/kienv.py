"""Locate KiCad, its CLI, and the symbol/footprint library tables."""

import glob
import json
import os
import platform
import re
import shutil
import subprocess

from . import sexp

KICAD_MAJOR = "9.0"


WINDOWS = platform.system() == "Windows"


def _mac_app():
    for p in ["/Applications/KiCad/KiCad.app", os.path.expanduser("~/Applications/KiCad/KiCad.app")]:
        if os.path.isdir(p):
            return p
    return None


def _win_root():
    """KiCad's install folder on Windows (e.g. C:\\Program Files\\KiCad\\9.0), or None."""
    if not WINDOWS:
        return None
    env = os.environ.get("KIPCB_KICAD_ROOT")
    cands = [env] if env else []
    bases = [os.environ[v] for v in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
    if os.environ.get("LOCALAPPDATA"):                       # per-user install
        bases.append(os.path.join(os.environ["LOCALAPPDATA"], "Programs"))
    for base in bases:                                       # 9.0, 9.1 ...: newest 9.x first
        cands += sorted(glob.glob(os.path.join(base, "KiCad", "9.*")), reverse=True)
    for c in cands:
        if c and os.path.exists(os.path.join(c, "bin", "kicad-cli.exe")):
            return c
    return None


def kicad_cli():
    env = os.environ.get("KIPCB_KICAD_CLI")
    if env:
        return env
    app = _mac_app()
    if app and os.path.exists(os.path.join(app, "Contents/MacOS/kicad-cli")):
        return os.path.join(app, "Contents/MacOS/kicad-cli")
    root = _win_root()
    if root:
        return os.path.join(root, "bin", "kicad-cli.exe")
    found = shutil.which("kicad-cli")
    if found:
        return found
    raise RuntimeError("kicad-cli not found. Install KiCad 9 (https://www.kicad.org/download/) "
                       "or set KIPCB_KICAD_CLI.")


def shared_support():
    """Directory holding the stock symbols/ and footprints/ folders."""
    env = os.environ.get("KICAD9_SYMBOL_DIR")
    if env:
        return os.path.dirname(env.rstrip("/"))
    app = _mac_app()
    if app:
        return os.path.join(app, "Contents/SharedSupport")
    root = _win_root()
    if root and os.path.isdir(os.path.join(root, "share", "kicad", "symbols")):
        return os.path.join(root, "share", "kicad")
    for p in ["/usr/share/kicad", "/usr/local/share/kicad"]:
        if os.path.isdir(os.path.join(p, "symbols")):
            return p
    return None


def config_dir():
    sysname = platform.system()
    if sysname == "Darwin":
        base = os.path.expanduser("~/Library/Preferences/kicad")
    elif sysname == "Windows":
        base = os.path.join(os.environ.get("APPDATA", ""), "kicad")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "kicad")
    return os.path.join(base, KICAD_MAJOR)


def env_vars(project_dir=None):
    ss = shared_support() or ""
    v = {
        "KICAD9_SYMBOL_DIR": os.path.join(ss, "symbols"),
        "KICAD9_FOOTPRINT_DIR": os.path.join(ss, "footprints"),
        "KICAD9_3DMODEL_DIR": os.path.join(ss, "3dmodels"),
        "KICAD9_TEMPLATE_DIR": os.path.join(ss, "template"),
        "KICAD_USER_TEMPLATE_DIR": os.path.join(config_dir(), "template"),
        "KICAD9_3RD_PARTY": os.path.expanduser("~/Documents/KiCad/9.0/3rdparty"),
    }
    try:
        with open(os.path.join(config_dir(), "kicad_common.json")) as f:
            user = (json.load(f).get("environment") or {}).get("vars") or {}
            v.update(user)
    except Exception:
        pass
    if project_dir:
        v["KIPRJMOD"] = project_dir
    v.update({k: os.environ[k] for k in v if k in os.environ})
    return v


def expand(uri, variables):
    return re.sub(r"\$\{([^}]+)\}", lambda m: variables.get(m.group(1), m.group(0)), uri)


def _read_table(path, variables):
    libs = {}
    if not path or not os.path.exists(path):
        return libs
    with open(path) as f:
        tree = sexp.loads(f.read())
    for lib in sexp.find_all(tree, "lib"):
        name = sexp.value(lib, "name")
        typ = sexp.value(lib, "type", "KiCad")
        uri = sexp.value(lib, "uri")
        disabled = sexp.find(lib, "disabled") is not None
        if name and uri and not disabled and str(typ) == "KiCad":
            libs[str(name)] = expand(str(uri), variables)
    return libs


def _stock_tables(kind):
    ss = shared_support()
    if not ss:
        return {}
    libs = {}
    if kind == "sym":
        for p in glob.glob(os.path.join(ss, "symbols", "*.kicad_sym")):
            libs[os.path.basename(p)[:-len(".kicad_sym")]] = p
    else:
        for p in glob.glob(os.path.join(ss, "footprints", "*.pretty")):
            libs[os.path.basename(p)[:-len(".pretty")]] = p
    return libs


def lib_table(kind, project_dir=None, extra=None):
    """Map nickname -> path for 'sym' or 'fp' libraries.

    Order of precedence (later wins): stock libs, global user table,
    project table, extra libs passed from the design spec.
    """
    variables = env_vars(project_dir)
    fname = "sym-lib-table" if kind == "sym" else "fp-lib-table"
    libs = _stock_tables(kind)
    libs.update(_read_table(os.path.join(config_dir(), fname), variables))
    if project_dir:
        libs.update(_read_table(os.path.join(project_dir, fname), variables))
    for nick, path in (extra or {}).items():
        libs[nick] = os.path.abspath(expand(path, variables))
    return {k: v for k, v in libs.items() if os.path.exists(v)}


def cache_dir():
    """Downloads and indexes. macOS/Linux: ~/.cache/kipcb; Windows: %LOCALAPPDATA%\\kipcb\\cache."""
    d = os.environ.get("KIPCB_CACHE")
    if not d:
        if WINDOWS and os.environ.get("LOCALAPPDATA"):
            d = os.path.join(os.environ["LOCALAPPDATA"], "kipcb", "cache")
        else:
            d = os.path.join(os.path.expanduser("~/.cache"), "kipcb")
    os.makedirs(d, exist_ok=True)
    return d


def data_dir():
    """Settings, learning and progress. macOS/Linux: ~/.local/share/kipcb (or $XDG_DATA_HOME);
    Windows: %LOCALAPPDATA%\\kipcb."""
    if os.environ.get("XDG_DATA_HOME"):
        d = os.path.join(os.environ["XDG_DATA_HOME"], "kipcb")
    elif WINDOWS and os.environ.get("LOCALAPPDATA"):
        d = os.path.join(os.environ["LOCALAPPDATA"], "kipcb")
    else:
        d = os.path.join(os.path.expanduser("~/.local/share"), "kipcb")
    os.makedirs(d, exist_ok=True)
    return d


def cli_env():
    """Environment for kicad-cli: without our PYTHON* settings. kicad-cli embeds its own
    Python, and on Windows it crashes on startup when it inherits them from kipcb."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}


_SKIP_DIRS = {"fab", "previews", "reports", ".kipcb", "__pycache__"}


def _mirror_inputs(args):
    """Windows: kipcb's own process keeps the KiCad project open (pcbnew's settings manager
    holds it), and kicad-cli crashes (0xC0000005) opening a project another process holds.
    Point kicad-cli at a temporary copy of the project instead; outputs still go where asked."""
    import tempfile
    out = list(args)
    tmp = None
    for i, a in enumerate(out):
        if not (isinstance(a, str) and a.endswith((".kicad_sch", ".kicad_pcb")) and os.path.isfile(a)):
            continue
        src_dir = os.path.dirname(os.path.abspath(a))
        if tmp is None:
            tmp = tempfile.mkdtemp(prefix="kipcb_cli_")
            for entry in os.listdir(src_dir):
                src = os.path.join(src_dir, entry)
                if entry in _SKIP_DIRS:
                    continue
                if os.path.isdir(src):
                    shutil.copytree(src, os.path.join(tmp, entry))
                else:
                    shutil.copy2(src, tmp)
        out[i] = os.path.join(tmp, os.path.basename(a))
    return out, tmp


_CONFIG_READY = False


def ensure_user_config():
    """KiCad creates its global symbol/footprint library tables the first time the KiCad app
    is opened. On a fresh install that never was (e.g. `winget install KiCad.KiCad`),
    kicad-cli on Windows crashes on schematic commands. Do what KiCad's first run does:
    copy its template tables into the user config folder, only if they're missing."""
    global _CONFIG_READY
    if _CONFIG_READY:
        return
    _CONFIG_READY = True
    ss = shared_support()
    if not ss:
        return
    cfg = config_dir()
    for name in ("sym-lib-table", "fp-lib-table"):
        dest = os.path.join(cfg, name)
        src = os.path.join(ss, "template", name)
        if not os.path.exists(dest) and os.path.exists(src):
            try:
                os.makedirs(cfg, exist_ok=True)
                shutil.copyfile(src, dest)
            except OSError:
                pass


_CRASH = (3221225477, -1073741819)          # 0xC0000005 access violation, as unsigned / signed
CLI_TIMEOUT = int(os.environ.get("KIPCB_CLI_TIMEOUT", "300"))


def run_cli(args, check=True, cwd=None):
    ensure_user_config()
    tmp = None
    if WINDOWS and not os.environ.get("KIPCB_CLI_NO_MIRROR"):
        args, tmp = _mirror_inputs(args)
    cmd = [kicad_cli()] + list(args)
    try:
        for attempt in (1, 2):
            try:
                p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, encoding="utf-8", errors="replace",
                                   cwd=cwd, env=cli_env(), timeout=CLI_TIMEOUT)
            except subprocess.TimeoutExpired as e:  # a stuck kicad-cli must not freeze the run
                out = e.output if isinstance(e.output, str) else ""
                p = subprocess.CompletedProcess(cmd, -9, out + "\nkicad-cli timed out after %ds" % CLI_TIMEOUT)
            if p.returncode not in _CRASH and p.returncode != -9:
                break                           # a crash or hang is retried once
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if check and p.returncode != 0:
        raise RuntimeError("kicad-cli %s failed (%d):\n%s" % (" ".join(args[:3]), p.returncode, p.stdout))
    return p
