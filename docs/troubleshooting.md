# Troubleshooting

**`kipcb: could not find a Python with KiCad's pcbnew module`**
Install KiCad 9. If it's installed somewhere unusual, point `KIPCB_PYTHON` at a
Python that can `import pcbnew` (on macOS:
`/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3`).

**`kipcb` is not found in Claude Code**
The plugin's `bin/` folder is only on PATH while the plugin is enabled. Start a
new session after installing. Outside Claude Code, run `bin/kipcb` from the repo.

**Freerouting: Java too old / not found**
Freerouting 2.1 needs Java 21+, and newer versions need Java 25. Run
`java -version`, install a newer JRE (`brew install --cask temurin`), then
`kipcb setup-router` again.

**Routing leaves connections unrouted**
kipcb already retries with different strategies and runs a completion pass.
If connections still fail:
- give the board more room (larger `width`/`height`, or `"spacing": "roomy"`)
- spread crowded groups, or use `place` hints so ICs face their partners
- move to 4 layers for fine-pitch parts or dense boards
- run `kipcb route <project> --attempts 6` to try more strategies

Each run is remembered, so later boards start with strategies that worked.

**DRC errors after routing**
Run `kipcb drc <project>` for details. Clearance or edge errors usually mean a
part sits too close to the edge or to another part; adjust placement and
rebuild. Silkscreen warnings are cosmetic.

**Noise check FAILs**
See the table in
[layout-guidelines.md](../skills/design/references/layout-guidelines.md). The usual fix
is a `near` hint on the exact supply pin for its decoupling cap, or more
board room so routes stay on the top layer.

**The schematic or board changed after I edited it in KiCad**
`kipcb build` regenerates the KiCad files from the spec, which overwrites hand
edits. Iterate through the spec first, then polish in KiCad at the end.

**A part isn't in KiCad's libraries**
See "Custom parts" in [spec-format.md](../skills/design/references/spec-format.md)
(stock equivalents, `easyeda2kicad`, or a hand-made footprint).

**`kipcb ref` says no knowledge base is installed**
It's optional. Build one with the
[pcb-knowledge](https://github.com/virajrungta/pcb-knowledge) project
(`pkb fetch`, `pkb extract`, `pkb build`, `pkb install`).

**Something else**
Run `kipcb doctor` and include its output when you
[open an issue](https://github.com/virajrungta/pcb-claude-plugin/issues).
