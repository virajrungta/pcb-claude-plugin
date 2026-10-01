# PCB Design plugin for Claude Code

Describe a board in plain English and get a manufacturable KiCad project:
requirements → part selection → schematic → placement → autorouting →
ERC/DRC → Gerbers, BOM and pick-and-place files.

```
/pcb:design a USB-C powered ESP32-C3 board with a WS2812 LED, reset/boot buttons and a 4-pin sensor header
```

Claude acts as the electrical engineer (choosing parts, following datasheet
reference circuits, reviewing the design), and the bundled `kipcb` tool does
the KiCad work. Everything is generated from a small JSON design spec, so
changes are made in the spec and rebuilt.

## What you get

| Command | What it does |
|---|---|
| `/pcb:design [idea]` | Full idea-to-PCB workflow |
| `/pcb:review [project]` | Review any KiCad project: ERC, DRC, netlist checklist, renders |
| `/pcb:fab [project]` | Gerbers + drill zip, JLCPCB-format BOM and CPL, ordering checklist |
| `pcb:circuit-reviewer` agent | Independent schematic review against datasheets |

Every build produces a normal KiCad 9 project (`.kicad_pro`, `.kicad_sch`,
`.kicad_pcb`) that you can open and edit in KiCad.

## Requirements

- [KiCad 9](https://www.kicad.org/download/). `kipcb` uses KiCad's bundled
  Python (`pcbnew`) and `kicad-cli`.
- Java 21+ for autorouting with [Freerouting](https://github.com/freerouting/freerouting)
  (Java 25+ for the newest version). Run `kipcb setup-router` once to download it into `~/.cache/kipcb`.
- macOS or Linux.

## Install

```bash
claude plugin marketplace add virajrungta/pcb-claude-plugin
```

```bash
claude plugin install pcb@pcb-claude-plugin
```

Or try it for one session without installing:

```bash
claude --plugin-dir /path/to/pcb-claude-plugin
```

Then check the toolchain:

```bash
kipcb doctor
```

## The `kipcb` tool

Claude runs these for you; they're also usable directly (`bin/kipcb`).

```
kipcb doctor                      check KiCad, Java, Freerouting
kipcb sym-search <words>          find schematic symbols      kipcb sym-info Lib:Name
kipcb fp-search <words>           find footprints             kipcb fp-info Lib:Name
kipcb check spec.json             validate a design spec
kipcb build spec.json             schematic + placed board + ERC + previews
kipcb route <project>             Freerouting + ground pour + stitching vias + DRC
kipcb noise <project>             basic noise / signal-integrity checks
kipcb ref <part>                  how open-source designs wire a part
kipcb learn [reset]               what kipcb has learned from past runs
kipcb erc|drc|netlist|render <project>
kipcb fab <project>               Gerbers/drill zip, BOM, CPL
kipcb setup-router                download Freerouting
```

Try the examples:

```bash
bin/kipcb build examples/usb_blinker.json
```

```bash
bin/kipcb route examples/usb_blinker
```

```bash
bin/kipcb fab examples/usb_blinker
```

## How it works

1. **Spec**: a JSON file lists components (KiCad symbol + footprint + value
   + LCSC number), nets as `REF.PIN` lists, and optional placement hints. See
   [spec-format.md](skills/design/references/spec-format.md).
2. **Validation**: every pin is resolved against the real KiCad library
   symbol, symbol pins are checked against footprint pads, and every unused pin
   must be explicitly marked no-connect.
3. **Schematic**: symbols are laid out by functional group, each pin wired
   to a net label or power symbol, with PWR_FLAGs where ERC needs them.
4. **Board**: footprints, nets and net classes, an outline with rounded
   corners and optional mounting holes, then connectivity-driven placement
   (connectors flush to edges facing out, decoupling caps pulled to their pins).
5. **Routing**: Specctra DSN → Freerouting → SES import, with automatic retry
   at narrower power widths, healing of near-miss track ends, GND pours on both
   layers, and stitching vias.
6. **Checks and outputs**: KiCad ERC/DRC with schematic parity, noise
   checks (decoupling distance, ground plane integrity, crosstalk, crystals,
   switch-node loops, differential pairs), 3D renders for visual review, and fab
   files referenced to the board corner.

## Learning

kipcb improves with use. Everything stays on your machine.

- **From your own runs** (`kipcb learn`): every build and routing attempt is
  logged to `~/.local/share/kipcb/experience.jsonl`. Routing strategies are
  ranked by how they did on past boards of similar density (a contextual
  bandit with exploration). Auto-sized boards use the tightest area per pad
  that has reliably routed before. Footprints that keep causing unrouted
  connections get extra clearance. `kipcb learn reset` forgets everything,
  and `KIPCB_LEARN=0` turns logging off.
- **From open-source designs** (`kipcb ref <part>`): the companion
  [pcb-knowledge](https://github.com/virajrungta/pcb-knowledge) repo mines openly licensed KiCad projects
  from GitHub into statistics: how real designs wire each IC's pins, plus
  layout distributions that seed the sizing prior. Claude consults it during
  part selection and review.

## Limits

Autorouted boards are functional, not optimal. Switching-regulator layout,
RF, controlled-impedance and high-speed signals (USB 480 Mb/s, Ethernet, DDR)
need human review or hand routing in KiCad. The plugin flags these instead of
pretending otherwise. Always review a design before ordering.

## License

MIT
