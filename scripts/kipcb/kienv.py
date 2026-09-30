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


def _mac_app():
    for p in ["/Applications/KiCad/KiCad.app", os.path.expanduser("~/Applications/KiCad/KiCad.app")]:
        if os.path.isdir(p):
            return p
    return None


def kicad_cli():
    env = os.environ.get("KIPCB_KICAD_CLI")
    if env:
        return env
    app = _mac_app()
    if app and os.path.exists(os.path.join(app, "Contents/MacOS/kicad-cli")):
        return os.path.join(app, "Contents/MacOS/kicad-cli")
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
    d = os.environ.get("KIPCB_CACHE") or os.path.join(os.path.expanduser("~/.cache"), "kipcb")
    os.makedirs(d, exist_ok=True)
    return d


def run_cli(args, check=True, cwd=None):
    cmd = [kicad_cli()] + list(args)
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, cwd=cwd)
    if check and p.returncode != 0:
        raise RuntimeError("kicad-cli %s failed (%d):\n%s" % (" ".join(args[:3]), p.returncode, p.stdout))
    return p
