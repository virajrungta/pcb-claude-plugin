"""Command line interface for kipcb."""

import argparse
import os
import sys

from . import __version__, kienv


def _project_paths(target):
    """Resolve a project dir, .kicad_pro, .kicad_pcb or spec .json to (dir, name)."""
    target = os.path.abspath(target)
    if os.path.isdir(target):
        pros = [f for f in os.listdir(target) if f.endswith(".kicad_pro")]
        if len(pros) != 1:
            raise SystemExit("expected exactly one .kicad_pro in %s" % target)
        return target, pros[0][:-len(".kicad_pro")]
    base, ext = os.path.splitext(target)
    if ext == ".json":
        from .spec import Design
        d = Design(target)
        return os.path.join(d.dir, d.name), d.name
    return os.path.dirname(target), os.path.basename(base)


def cmd_doctor(a):
    import subprocess
    ok = True
    print("kipcb %s" % __version__)
    try:
        cli = kienv.kicad_cli()
        v = kienv.run_cli(["version"]).stdout.strip()
        print("kicad-cli      OK  %s (%s)" % (v, cli))
    except Exception as e:
        ok = False
        print("kicad-cli      MISSING  %s" % e)
    try:
        import pcbnew
        print("pcbnew         OK  %s (python %s)" % (pcbnew.Version(), sys.version.split()[0]))
    except Exception as e:
        ok = False
        print("pcbnew         MISSING  %s" % e)
    syms = kienv.lib_table("sym")
    fps = kienv.lib_table("fp")
    print("symbol libs    %d" % len(syms))
    print("footprint libs %d" % len(fps))
    from . import route
    java = route.java_version()
    print("java           %s" % (("OK  version %d" % java) if java else "MISSING (needed for autorouting)"))
    jar = route.find_jar()
    print("freerouting    %s" % (("OK  " + jar) if jar else "not installed (run: kipcb setup-router)"))
    from . import knowledge, learn
    kb = knowledge.load()
    print("knowledge      %s" % (("%d parts from %d open-source projects" % (len(kb["parts"]), kb.get("projects", 0)))
                                 if kb else "not installed (optional; see the pcb-knowledge repo)"))
    print("experience     %d routed boards learned from (kipcb learn)" % len(learn.history("route_final")))
    return 0 if ok else 1


def cmd_sym_search(a):
    from . import symlib
    libs = kienv.lib_table("sym")
    res = symlib.search(libs, " ".join(a.query), a.limit)
    if not res:
        print("no symbols match %r" % " ".join(a.query))
        return 1
    for e in res:
        fp = (" fp=" + e["fp"]) if e["fp"] else ""
        print("%s:%s  [%s pins]%s\n    %s" % (e["lib"], e["name"], e["pins"], fp, e["desc"][:140]))
    return 0


def cmd_sym_info(a):
    from . import symlib
    from .spec import strip_markup
    libs = kienv.lib_table("sym")
    s = symlib.load(libs, a.id)
    print(s.lib_id)
    for k in ("Description", "Footprint", "ki_fp_filters", "Datasheet", "ki_keywords"):
        if s.props.get(k):
            print("  %-13s %s" % (k, s.props[k]))
    if len(s.units) > 1:
        print("  units         %s" % ", ".join(str(u) for u in sorted(s.units)))
    print("  pins (number  name  type  unit):")
    from .fplib import _natkey
    for p in sorted(s.pins, key=lambda p: _natkey(p["number"])):
        print("    %-5s %-22s %-14s %s%s" % (p["number"], strip_markup(p["name"]), p["type"], p["unit"],
                                           "  hidden" if p["hidden"] else ""))
    return 0


def cmd_fp_search(a):
    from . import fplib
    libs = kienv.lib_table("fp")
    res = fplib.search(libs, " ".join(a.query), a.limit)
    if not res:
        print("no footprints match %r" % " ".join(a.query))
        return 1
    for e in res:
        print("%s:%s\n    %s" % (e["lib"], e["name"], e["desc"][:140]))
    return 0


def cmd_fp_info(a):
    from . import fplib
    libs = kienv.lib_table("fp")
    f = fplib.load(libs, a.id)
    x0, y0, x1, y1 = f.courtyard
    print(f.fp_id)
    print("  %s" % f.descr)
    print("  type %s, courtyard %.2f x %.2f mm" % ("SMD" if f.smd else "THT/other", x1 - x0, y1 - y0))
    print("  pads: %s" % ", ".join(f.pad_numbers()))
    return 0


def cmd_check(a):
    from .spec import Design, SpecError
    try:
        d = Design(a.spec)
    except SpecError as e:
        print("ERROR: %s" % e)
        return 1
    d.validate()
    print(d.summary())
    for n in getattr(d, "notes", []):
        print("NOTE: " + n)
    for w in d.warnings:
        print("WARNING: " + w)
    for e in d.errors:
        print("ERROR: " + e)
    if d.errors:
        print("\n%d error(s), %d warning(s). Fix errors before building." % (len(d.errors), len(d.warnings)))
        return 1
    print("\nOK: %d warning(s)." % len(d.warnings))
    return 0


def cmd_build(a):
    from . import build
    return build.build(a.spec, a.out, render=not a.no_render)


def cmd_place(a):
    from . import build
    return build.replace(a.spec, a.out, render=not a.no_render)


def cmd_route(a):
    from . import route
    pdir, name = _project_paths(a.target)
    return route.route(pdir, name, passes=a.passes, timeout=a.timeout, pour=not a.no_pour,
                       attempts_max=a.attempts)


def cmd_setup_router(a):
    from . import route
    return route.setup(a.version)


def cmd_erc(a):
    from . import checks
    pdir, name = _project_paths(a.target)
    return checks.erc(pdir, name)


def cmd_drc(a):
    from . import checks
    pdir, name = _project_paths(a.target)
    return checks.drc(pdir, name)


def cmd_render(a):
    from . import render
    pdir, name = _project_paths(a.target)
    for p in render.render(pdir, name, a.what):
        print(p)
    return 0


def cmd_noise(a):
    from . import noise
    pdir, name = _project_paths(a.target)
    return noise.run(pdir, name, quiet=a.quiet)


def cmd_netlist(a):
    from . import sexp
    pdir, name = _project_paths(a.target)
    out = os.path.join(pdir, "out")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name + ".net")
    kienv.run_cli(["sch", "export", "netlist", "--format", "kicadsexpr", "-o", path,
                   os.path.join(pdir, name + ".kicad_sch")])
    with open(path, encoding="utf-8") as f:
        tree = sexp.loads(f.read())
    comps = sexp.find(tree, "components") or []
    print("COMPONENTS")
    for c in sexp.find_all(comps, "comp"):
        ref = sexp.value(c, "ref"); val = sexp.value(c, "value", "")
        fp = sexp.value(c, "footprint", "")
        ls = sexp.find(c, "libsource")
        lib = "%s:%s" % (sexp.value(ls, "lib", ""), sexp.value(ls, "part", "")) if ls else ""
        props = {str(p_[1][1]): str(p_[2][1]) for p_ in sexp.find_all(c, "property") if len(p_) > 2}
        extra = " ".join("%s=%s" % (k, v) for k, v in props.items() if k in ("LCSC", "MPN", "dnp", "DNP"))
        print("  %-6s %-22s %-40s %s %s" % (ref, val, lib, fp, extra))
    print("NETS")
    nets = sexp.find(tree, "nets") or []
    for n in sexp.find_all(nets, "net"):
        nodes = []
        for nd in sexp.find_all(n, "node"):
            fn = sexp.value(nd, "pinfunction", "")
            nodes.append("%s.%s%s" % (sexp.value(nd, "ref"), sexp.value(nd, "pin"),
                                      "(%s)" % fn if fn else ""))
        print("  %s: %s" % (sexp.value(n, "name"), " ".join(nodes)))
    return 0


def cmd_ref(a):
    from . import knowledge
    return knowledge.print_part(" ".join(a.part))


def cmd_settings(a):
    from . import learn
    s = learn.settings()
    print("manufacturer:  %s" % s.get("fab_house", "JLCPCB (default)"))
    print("build method:  %s" % s.get("build_method", "Assembled by the manufacturer (default)"))
    print("layers:        %s" % s.get("layers", "2 (default)"))
    print("learning:      %s" % ("on" if learn.enabled() else "off"))
    print("change with:   /plugin configure pcb@pcb-claude-plugin")
    return 0


def cmd_learn(a):
    from . import learn
    return learn.reset() if a.action == "reset" else learn.status()


def cmd_fab(a):
    from . import fab
    pdir, name = _project_paths(a.target)
    if a.fab is None:
        from . import learn
        a.fab = "jlcpcb" if learn.settings().get("fab_house", "JLCPCB") == "JLCPCB" else "generic"
    return fab.export(pdir, name, a.fab, a.force)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kipcb", description="Idea-to-PCB automation for KiCad 9")
    ap.add_argument("--version", action="version", version=__version__)
    sp = ap.add_subparsers(dest="cmd")

    p = sp.add_parser("doctor", help="check KiCad, Java and Freerouting installation")
    p.set_defaults(fn=cmd_doctor)

    p = sp.add_parser("sym-search", help="search symbol libraries")
    p.add_argument("query", nargs="+"); p.add_argument("-n", "--limit", type=int, default=25)
    p.set_defaults(fn=cmd_sym_search)
    p = sp.add_parser("sym-info", help="show a symbol's pins, units and default footprint")
    p.add_argument("id"); p.set_defaults(fn=cmd_sym_info)
    p = sp.add_parser("fp-search", help="search footprint libraries")
    p.add_argument("query", nargs="+"); p.add_argument("-n", "--limit", type=int, default=25)
    p.set_defaults(fn=cmd_fp_search)
    p = sp.add_parser("fp-info", help="show a footprint's pads and size")
    p.add_argument("id"); p.set_defaults(fn=cmd_fp_info)

    p = sp.add_parser("check", help="validate a design spec (JSON)")
    p.add_argument("spec"); p.set_defaults(fn=cmd_check)
    p = sp.add_parser("build", help="spec -> KiCad project (schematic + placed PCB), ERC, renders")
    p.add_argument("spec"); p.add_argument("-o", "--out", help="output dir (default: <spec dir>/<name>)")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(fn=cmd_build)
    p = sp.add_parser("place", help="re-run placement on an existing build from the spec (discards routing)")
    p.add_argument("spec"); p.add_argument("-o", "--out"); p.add_argument("--no-render", action="store_true")
    p.set_defaults(fn=cmd_place)

    p = sp.add_parser("route", help="autoroute with Freerouting, pour ground, run DRC")
    p.add_argument("target", help="project dir, .kicad_pcb or spec .json")
    p.add_argument("--passes", type=int, default=100)
    p.add_argument("--timeout", type=int, default=600, help="seconds")
    p.add_argument("--no-pour", action="store_true")
    p.add_argument("--attempts", type=int, default=4, help="router attempts; best result is kept")
    p.set_defaults(fn=cmd_route)
    p = sp.add_parser("setup-router", help="download the Freerouting autorouter jar")
    p.add_argument("--version", default=None); p.set_defaults(fn=cmd_setup_router)

    for name, fn, hlp in (("erc", cmd_erc, "run schematic ERC"), ("drc", cmd_drc, "run PCB DRC")):
        p = sp.add_parser(name, help=hlp); p.add_argument("target"); p.set_defaults(fn=fn)
    p = sp.add_parser("render", help="render PNG previews (schematic, 2D layers, 3D)")
    p.add_argument("target"); p.add_argument("--what", default="all", choices=["all", "sch", "pcb", "3d"])
    p.set_defaults(fn=cmd_render)
    p = sp.add_parser("noise", help="basic noise / signal-integrity checks on the board")
    p.add_argument("target"); p.add_argument("-q", "--quiet", action="store_true", help="only show problems")
    p.set_defaults(fn=cmd_noise)
    p = sp.add_parser("netlist", help="print components and nets of a schematic (for review)")
    p.add_argument("target"); p.set_defaults(fn=cmd_netlist)
    p = sp.add_parser("ref", help="how open-source designs wire a part (needs the knowledge base)")
    p.add_argument("part", nargs="+"); p.set_defaults(fn=cmd_ref)
    sp.add_parser("settings", help="show your defaults from the install dialog").set_defaults(fn=cmd_settings)
    p = sp.add_parser("learn", help="show or reset what kipcb has learned from past runs")
    p.add_argument("action", nargs="?", default="status", choices=["status", "reset"])
    p.set_defaults(fn=cmd_learn)
    p = sp.add_parser("fab", help="export Gerbers, drill, BOM, pick-and-place and a zip")
    p.add_argument("target"); p.add_argument("--fab", default=None, choices=["jlcpcb", "generic"],
                                             help="output format (default: your manufacturer setting)")
    p.add_argument("--force", action="store_true", help="export even with DRC errors")
    p.set_defaults(fn=cmd_fab)

    a = ap.parse_args(argv)
    if not getattr(a, "fn", None):
        ap.print_help()
        return 1
    try:
        return a.fn(a) or 0
    except (KeyError, RuntimeError) as e:
        print("kipcb: error: %s" % (e.args[0] if e.args else e), file=sys.stderr)
        return 1
