"""Command line interface for kipcb."""

import argparse
import os
import re
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


def _queries(words):
    """'esp32 c3 | ams1117 | usb c' -> three queries (one call, one round trip)."""
    return [q.strip() for q in " ".join(words).split("|") if q.strip()]


def cmd_sym_search(a):
    from . import symlib
    libs = kienv.lib_table("sym")
    queries = _queries(a.query)
    limit = a.limit or (8 if len(queries) == 1 else 5)
    rc = 0
    for q in queries:
        res = symlib.search(libs, q, limit)
        if len(queries) > 1:
            print("## " + q)
        if not res:
            print("  no symbols match")
            rc = 1
        for e in res:
            fp = (" fp=" + e["fp"].split(":")[-1]) if e["fp"] else ""
            print("%s:%s [%sp]%s | %s" % (e["lib"], e["name"], e["pins"], fp, e["desc"][:80]))
    return rc


TYPE_ABBR = {"input": "in", "output": "out", "bidirectional": "io", "tri_state": "3s", "passive": "",
             "power_in": "pwr", "power_out": "PWR_OUT", "open_collector": "oc", "open_emitter": "oe",
             "unspecified": "?", "free": "", "no_connect": "nc"}


def cmd_sym_info(a):
    """Compact pin listing: 'number=name/type' grouped per unit; several symbols per call."""
    from . import symlib
    from .spec import strip_markup
    from .fplib import _natkey
    libs = kienv.lib_table("sym")
    rc = 0
    for sid in a.id:
        try:
            s = symlib.load(libs, sid)
        except KeyError as e:
            print("%s: %s" % (sid, e.args[0]))
            rc = 1
            continue
        filters = s.props.get("ki_fp_filters", "-").split()
        fp = s.props.get("Footprint") or ("filters " + " ".join(filters[:3]) + (" ..." if len(filters) > 3 else ""))
        print("%s | fp %s | %s" % (s.lib_id, fp, s.props.get("Description", "")[:90]))
        if a.verbose and s.props.get("Datasheet", "~") != "~":
            print("  datasheet " + s.props["Datasheet"])
        units = sorted({p["unit"] for p in s.pins})
        for u in units:
            pins = sorted([p for p in s.pins if p["unit"] == u], key=lambda p: _natkey(p["number"]))
            cells = []
            for p in pins:
                name = strip_markup(p["name"])
                t = TYPE_ABBR.get(p["type"], p["type"])
                cells.append("%s%s%s%s" % (p["number"], ("=" + name) if name not in ("", "~") else "",
                                           ("/" + t) if t else "", "*" if p["hidden"] else ""))
            label = "  " if len(units) == 1 else "  unit %s: " % ("common" if u == 0 else u)
            print(label + " ".join(cells))
    print("(types: pwr=power in, io=bidirectional, in/out; *=hidden pin; no suffix=passive)")
    return rc


def cmd_fp_search(a):
    from . import fplib
    libs = kienv.lib_table("fp")
    queries = _queries(a.query)
    limit = a.limit or (8 if len(queries) == 1 else 5)
    rc = 0
    for q in queries:
        res = fplib.search(libs, q, limit)
        if len(queries) > 1:
            print("## " + q)
        if not res:
            print("  no footprints match")
            rc = 1
        for e in res:
            print("%s:%s | %s" % (e["lib"], e["name"], e["desc"].split(",")[0][:70]))
    return rc


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
    for line in getattr(d, "block_summary", []):
        print("block " + line)
    for line in getattr(d, "support", []):
        print("SUPPORT: " + line)
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


def cmd_run(a):
    from . import run
    return run.run(a.spec, a.out, do_fab=not a.no_fab, force=a.force, passes=a.passes, timeout=a.timeout,
                   until=a.until, resume=a.resume)


def cmd_report(a):
    from . import report
    pdir, name = _project_paths(a.target)
    print(report.text(report.write(pdir, name)))
    return 0


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


def cmd_estimate(a):
    """Approximate time per pipeline step, to tell the user before a run."""
    from . import estimate
    from .spec import Design
    d = Design(a.spec)
    for line in estimate.lines(d, fab=not a.no_fab):
        print(line)
    from . import progress
    est, _, f = estimate.estimate(d)
    why = ("%s: fine-pitch pins" % " and ".join(f["fine_parts"])) if est["route"][1] > 120 and f["fine_parts"] else ""
    progress.start(os.path.join(d.dir, d.name), d.name,
                   {k: "~" + estimate.fmt((v[0] + v[1]) / 2) for k, v in est.items()}, why, keep=True)
    print("progress: " + progress.render_line(progress.load(os.path.join(d.dir, d.name))))
    return 0


def cmd_progress(a):
    """Show the design progress checklist (also: start one, status-bar line, status-bar setup)."""
    from . import progress
    if a.start:
        name = re.sub(r"[^A-Za-z0-9_\-]+", "_", a.start).strip("_") or "board"
        pdir = os.path.abspath(os.path.join(a.dir, name))
        progress.start(pdir, name, {})
        print("progress: " + progress.render_line(progress.load(pdir)))
        return 0
    if a.statusline:
        print(progress.statusline())
        return 0
    if a.install_statusline:
        print(progress.install_statusline())
        return 0
    if a.uninstall_statusline:
        print(progress.uninstall_statusline())
        return 0
    state = progress.load()
    print(progress.render(state) if state else "no design in progress")
    return 0


def cmd_preflight(a):
    """Routability checks on a built (placed) board, from its spec."""
    from . import preflight
    from .spec import Design
    d = Design(a.spec)
    pdir = os.path.join(d.dir, d.name)
    if not os.path.exists(os.path.join(pdir, d.name + ".kicad_pcb")):
        print("not built yet: kipcb build %s" % a.spec)
        return 1
    fails = preflight.run(pdir, d.name, d)
    print("preflight: %s" % ("%d problem(s) to fix before routing" % fails if fails else "ok, ready to route"))
    return 1 if fails else 0


def cmd_netlist(a):
    from . import sexp
    pdir, name = _project_paths(a.target)
    from . import paths
    out = paths.work(pdir)
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
    from . import learn, progress
    s = learn.settings()
    print("manufacturer:  %s" % s.get("fab_house", "JLCPCB (default)"))
    print("build method:  %s" % s.get("build_method", "Assembled by the manufacturer (default)"))
    print("layers:        %s" % s.get("layers", "2 (default)"))
    print("learning:      %s" % ("on" if learn.enabled() else "off"))
    print("status bar:    %s" % ("PCB progress" if progress.statusline_installed() else "not set"))
    print("change with:   /plugin configure pcb@pcb-claude-plugin")
    return 0


def cmd_lcsc(a):
    """C-numbers are looked up; anything else is a search ('kipcb lcsc -s "SHT31 | 10uF 0805"')."""
    from . import lcsc
    words = " ".join(a.codes)
    if a.search or not all(re.match(r"^C?\d+$", w, re.I) for w in a.codes):
        rc = 0
        queries = _queries(a.codes)
        for q in queries:
            rows = lcsc.search(q, limit=a.limit or 6, basic_only=a.basic)
            if len(queries) > 1:
                print("## " + q)
            if rows is None:
                print("  offline: can't reach JLCPCB's parts search")
                return 1
            if not rows:
                print("  no parts match")
                rc = 1
            for r in rows:
                print(lcsc.describe(r))
        return rc
    res = lcsc.lookup(words.split(), refresh=a.refresh)
    for code in res:
        print(lcsc.describe(res[code]))
    return 0 if all(r.get("found") for r in res.values()) else 1


def cmd_parts(a):
    from . import blocks
    q = " ".join(a.query).lower()
    rows = []
    for name, p in sorted(blocks.builtin_parts().items()):
        text = "%s %s %s %s" % (name, p.get("symbol", ""), p.get("footprint", ""), p.get("kind", ""))
        if q and q not in text.lower():
            continue
        lc = p.get("lcsc")
        lcs = (" lcsc " + lc) if isinstance(lc, str) else (" lcsc for " + ",".join(lc)) if lc else ""
        fp = p.get("footprint", "(symbol default)").split(":")[-1]
        rows.append("%s: %s | %s%s%s" % (name, p["symbol"], fp, lcs, (" | " + p["pins"]) if p.get("pins") else ""))
    mem = sorted(blocks.remembered().values(), key=lambda e: -e.get("uses", 0))
    mine = [e for e in mem if not q or q in __import__('json').dumps(e).lower()][:15]
    for line in rows:
        print(line)
    if mine:
        print("## remembered from your boards (use \"part\": \"<value>\")")
        for e in mine:
            print("%s: %s | %s%s | used %dx" % (e["value"], e["symbol"], e["footprint"].split(":")[-1],
                                               (" lcsc " + e["lcsc"]) if e.get("lcsc") else "", e.get("uses", 1)))
    print('(use: {"ref": "R1", "part": "R0603", "value": "10k"}; LCSC numbers: confirm in JLCPCB\'s BOM preview)')
    return 0




def cmd_blocks(a):
    """No args: list all. Block names: details for each. Other words: filter the list."""
    from . import blocks
    cat = blocks.builtin_blocks()
    names = [n for n in a.name if n in cat]
    words = [w.lower() for w in a.name if w not in cat]
    for name in names:
        b = cat[name]
        ports = [n[1:] for n in b["nets"] if n.startswith("@")]
        opt = set(b.get("optional", []))
        req = [p for p in ports if p not in opt]
        print("%s: %s%s" % (name, b["title"], "  [advanced]" if b.get("support") == "advanced" else ""))
        print("  " + b["description"])
        if b.get("support_note"):
            print("  support: advanced -- " + b["support_note"])
        defaults = b.get("defaults", {})
        print("  ports: " + " ".join("%s%s" % (p, ("=" + defaults[p]) if p in defaults else "") for p in req))
        if opt:
            print("  optional ports (no-connect if unused): " + " ".join(p for p in ports if p in opt))
        if b.get("autojoin"):
            print("  joins a same-named net automatically: " + " ".join(b["autojoin"]))
        if b.get("params"):
            print("  params: " + " ".join("%s=%s" % kv for kv in b["params"].items()))
        print("  parts: " + ", ".join("%s %s %s" % (c["ref"], c.get("part", ""), c.get("value", "")) for c in b["components"]))
        print('  use: {"use": "%s", "connect": {%s}}  ("omit": ["R1"] drops a part)' % (
            name, ", ".join('"%s": "..."' % p for p in req[:3])))
    if names and not words:
        return 0
    shown = 0
    for name, b in cat.items():
        text = (name + " " + b["title"] + " " + b["description"]).lower()
        if words and not all(w in text for w in words):
            continue
        ports = [n[1:] for n in b["nets"] if n.startswith("@") and n[1:] not in set(b.get("optional", []))]
        print("%s: %s%s | ports %s%s" % (name, b["title"], " [advanced]" if b.get("support") == "advanced" else "",
                                         " ".join(ports), " +IOs" if b.get("optional") else ""))
        shown += 1
    if words and not shown:
        print("no block matches %r" % " ".join(words))
        return 1
    print("(`kipcb blocks <name> [<name>...]` for ports, params and parts)")
    return 0


def cmd_guide(a):
    from . import guide
    return guide.show(" ".join(a.topic))


def cmd_fmt(a):
    from . import fmt
    for path in a.spec:
        before, after = fmt.fmt_file(path)
        print("%s: %d -> %d characters" % (path, before, after))
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

    p = sp.add_parser("sym-search", help="search symbol libraries ('a | b | c' runs several searches)")
    p.add_argument("query", nargs="+"); p.add_argument("-n", "--limit", type=int, default=None)
    p.set_defaults(fn=cmd_sym_search)
    p = sp.add_parser("sym-info", help="pins, units and default footprint of one or more symbols")
    p.add_argument("id", nargs="+"); p.add_argument("-v", "--verbose", action="store_true", help="include datasheet URL")
    p.set_defaults(fn=cmd_sym_info)
    p = sp.add_parser("fp-search", help="search footprint libraries ('a | b' runs several searches)")
    p.add_argument("query", nargs="+"); p.add_argument("-n", "--limit", type=int, default=None)
    p.set_defaults(fn=cmd_fp_search)
    p = sp.add_parser("fp-info", help="show a footprint's pads and size")
    p.add_argument("id"); p.set_defaults(fn=cmd_fp_info)

    p = sp.add_parser("run", help="spec -> checked, routed board + manufacturing files + final report")
    p.add_argument("spec"); p.add_argument("-o", "--out", help="output dir (default: <spec dir>/<name>)")
    p.add_argument("--no-fab", action="store_true", help="stop after routing")
    p.add_argument("--force", action="store_true", help="rebuild even if the spec is unchanged")
    p.add_argument("--passes", type=int, default=100); p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--until", choices=["build", "preflight", "route"],
                   help="stop after this stage (each stage ends with a 'checkpoint:' line)")
    p.add_argument("--resume", action="store_true", help="continue a staged run after its last finished stage")
    p.set_defaults(fn=cmd_run)
    p = sp.add_parser("report", help="print the summary of a project (also in reports/REPORT.md)")
    p.add_argument("target"); p.set_defaults(fn=cmd_report)
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
    p.add_argument("target"); p.add_argument("--what", default="build",
                                             choices=["build", "review", "full", "all", "sch", "pcb", "3d"])
    p.set_defaults(fn=cmd_render)
    p = sp.add_parser("noise", help="basic noise / signal-integrity checks on the board")
    p.add_argument("target"); p.add_argument("-q", "--quiet", action="store_true", help="only show problems")
    p.set_defaults(fn=cmd_noise)
    p = sp.add_parser("netlist", help="print components and nets of a schematic (for review)")
    p.add_argument("target"); p.set_defaults(fn=cmd_netlist)
    p = sp.add_parser("ref", help="how open-source designs wire a part (needs the knowledge base)")
    p.add_argument("part", nargs="+"); p.set_defaults(fn=cmd_ref)
    p = sp.add_parser("progress", help="show the current design's progress checklist")
    p.add_argument("--uninstall-statusline", action="store_true",
                   help="remove kipcb's status line from ~/.claude/settings.json (only if it's kipcb's)")
    p.add_argument("--install-statusline", action="store_true",
                   help="show design progress in Claude Code's status bar (edits ~/.claude/settings.json; "
                        "only when no status line is set yet)")
    p.add_argument("--statusline", action="store_true",
                   help="one line for Claude Code's status bar (empty when no design is in progress)")
    p.add_argument("--start", metavar="NAME", help="start a checklist for a new board right after the requirements")
    p.add_argument("--dir", default="hardware", help="folder the spec will go in (default: hardware)")
    p.set_defaults(fn=cmd_progress)
    p = sp.add_parser("estimate", help="approximate time for each step of `kipcb run` on this spec, "
                      "with the reason when a step is long")
    p.add_argument("spec"); p.add_argument("--no-fab", action="store_true"); p.set_defaults(fn=cmd_estimate)
    p = sp.add_parser("preflight", help="seconds-long checks that a placed board can route cleanly "
                      "(pad reach, fab limits, edge, placement, density)")
    p.add_argument("spec"); p.set_defaults(fn=cmd_preflight)
    p = sp.add_parser("lcsc", help="look up LCSC/JLCPCB numbers, or search JLCPCB's parts ('a | b'): "
                      "package, basic/extended, stock, price")
    p.add_argument("codes", nargs="+"); p.add_argument("--refresh", action="store_true")
    p.add_argument("-s", "--search", action="store_true", help="treat the words as a search")
    p.add_argument("--basic", action="store_true", help="search basic parts only (no extra setup fee)")
    p.add_argument("--limit", type=int)
    p.set_defaults(fn=cmd_lcsc)
    p = sp.add_parser("parts", help="prebuilt parts usable as {\"part\": \"R0603\"} (plus parts remembered from your boards)")
    p.add_argument("query", nargs="*"); p.set_defaults(fn=cmd_parts)
    p = sp.add_parser("blocks", help="prebuilt circuit blocks (USB-C, regulators, MCUs, LEDs...); names for details, other words filter")
    p.add_argument("name", nargs="*"); p.set_defaults(fn=cmd_blocks)
    p = sp.add_parser("guide", help="print the reference section on a topic (no topic: list sections)")
    p.add_argument("topic", nargs="*"); p.set_defaults(fn=cmd_guide)
    p = sp.add_parser("fmt", help="rewrite specs in the compact one-line-per-part layout")
    p.add_argument("spec", nargs="+"); p.set_defaults(fn=cmd_fmt)
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
    from .spec import SpecError
    try:
        return a.fn(a) or 0
    except (OSError, KeyError, RuntimeError, SpecError) as e:
        if os.environ.get("KIPCB_DEBUG"):
            raise
        if isinstance(e, OSError):             # missing file, permission... : say which
            msg = "%s%s" % (e.strerror or e, (": " + e.filename) if e.filename else "")
        elif isinstance(e, KeyError):          # a bare KeyError only names the key
            msg = "unknown name %r (set KIPCB_DEBUG=1 for the traceback)" % (e.args[0] if e.args else "")
        else:
            msg = e.args[0] if e.args else str(e)
        print("kipcb: error: %s" % msg, file=sys.stderr)
        return 1
