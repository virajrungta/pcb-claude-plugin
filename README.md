<div align="center">

<img src="docs/images/c3_sensor_angle.png" alt="A 3D render of an ESP32-C3 board designed, placed and routed by the plugin" width="640">

# PCB Design for Claude Code

**Describe a circuit board in plain English. Get a manufacturable KiCad project.**

Requirements → parts → schematic → placement → autorouting → checks → Gerbers, BOM and pick-and-place,
<br>with Claude as your electrical engineer.

<a href="https://github.com/virajrungta/pcb-claude-plugin/releases"><img src="https://img.shields.io/github/v/release/virajrungta/pcb-claude-plugin?label=release&color=2ea44f" alt="Latest release"></a>
<a href="https://github.com/virajrungta/pcb-claude-plugin/actions/workflows/ci.yml"><img src="https://github.com/virajrungta/pcb-claude-plugin/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<img src="https://img.shields.io/badge/KiCad-9-314cb0" alt="KiCad 9">
<img src="https://img.shields.io/badge/Claude%20Code-plugin-d97757" alt="Claude Code plugin">
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license"></a>

<a href="docs/getting-started.md"><b>Getting started</b></a> ·
<a href="docs/how-it-works.md"><b>How it works</b></a> ·
<a href="docs/cli.md"><b>CLI reference</b></a> ·
<a href="docs/troubleshooting.md"><b>Troubleshooting</b></a> ·
<a href="CHANGELOG.md"><b>Changelog</b></a>

</div>

---

```text
/pcb:design a USB-C powered ESP32-C3 board with an RGB status LED, reset and boot buttons, and a 4-pin sensor header
```

Claude asks a few questions (size, power, connectors, how you'll build it, anything noise-sensitive),
picks real parts, follows each chip's datasheet, validates every pin, builds the schematic and board
in KiCad, looks at its own renders to fix the layout, routes it, runs electrical and noise checks,
and hands you files a fab house can build.

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/c3_sensor_top.png" alt="ESP32-C3 sensor board, top view" width="100%"><br>
      <sub><b>ESP32-C3 sensor node</b>: USB-C, LDO, ESD, buttons, RGB LED, dual op-amp.<br>
      0 DRC errors · antenna keep-out respected · decoupling ≤ 2 mm from every supply pin</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/images/c3_sensor_schematic.png" alt="Generated schematic for the ESP32-C3 sensor node" width="100%"><br>
      <sub><b>Generated schematic</b>: grouped by function, labelled nets, standard<br>
      power symbols, ERC-clean, editable in KiCad</sub>
    </td>
  </tr>
</table>

## What you get

<table>
  <tr>
    <th align="left">Command</th>
    <th align="left">What it does</th>
  </tr>
  <tr>
    <td><code>/pcb:design [idea]</code></td>
    <td>The full workflow: requirements, part selection, schematic, placement, routing, checks and manufacturing files</td>
  </tr>
  <tr>
    <td><code>/pcb:review [project]</code></td>
    <td>Reviews <i>any</i> KiCad project: ERC, DRC, noise checks, a netlist checklist and renders, as a prioritized list of fixes</td>
  </tr>
  <tr>
    <td><code>/pcb:fab [project]</code></td>
    <td>Gerbers and drill zip, JLCPCB-format BOM and pick-and-place file, plus an ordering checklist</td>
  </tr>
  <tr>
    <td><code>pcb:circuit-reviewer</code></td>
    <td>A subagent that gives an independent second opinion on the circuit against datasheets</td>
  </tr>
  <tr>
    <td><code>kipcb</code></td>
    <td>The engine behind it all; also usable on its own (<a href="docs/cli.md">reference</a>)</td>
  </tr>
</table>

Every design is a normal **KiCad 9 project** (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`) that you can open and edit at any point.

## Highlights

- **Asks before it designs**: board size, power budget, connector edges, build method and noise sensitivity are pinned down first, not guessed.
- **Prebuilt, verified building blocks**: USB-C power or data, 3.3 V regulators, a LiPo charger, ESP32-C3/S3 and ATmega328P cores, LEDs, buttons, I²C, Qwiic, a MOSFET load switch and more, each one line in a design. No part research, pin lookups or datasheet reading for standard circuitry, and the parts you use are remembered for next time.
- **Real parts, verified pins**: every symbol and footprint comes from KiCad's libraries; every pin is checked, and unused pins must be marked deliberately.
- **Placement that follows the rules of thumb**: connectors flush to edges facing out, decoupling caps first and right at their pins, room for ICs to fan out, noisy parts kept away from sensitive ones.
- **Routing that finishes**: Freerouting with several strategies, a completion pass, ground pours on both layers and stitching vias.
- **Electrical checks before layout**: 3.3 V / 5 V logic-level mismatches, rail current budgets, and regulators that would overheat.
- **Noise checks**: decoupling distance, ground-plane integrity, crosstalk, crystal and switching-regulator layout, differential pairs, supply track width.
- **Right-sized boards**: auto-sized boards shrink to fit their parts while keeping room to route.
- **Gets smarter with use**: routing strategy, board sizing and footprint clearances improve with every board you route, and `kipcb ref` shows how open-source designs wire each chip ([how](docs/how-it-works.md#learning)). A knowledge base built from hundreds of real boards ships with the plugin. Everything stays on your machine.

## Installation

### 1. Requirements

[Claude Code](https://claude.com/claude-code), [KiCad 9](https://www.kicad.org/download/) and Java 21+ (for the autorouter). On macOS with Homebrew:

```bash
brew install --cask kicad
```

```bash
brew install --cask temurin
```

### 2. Add the marketplace

```bash
claude plugin marketplace add virajrungta/pcb-claude-plugin
```

### 3. Install the plugin

```bash
claude plugin install pcb@pcb-claude-plugin
```

Or, inside Claude Code, run `/plugin install pcb --marketplace virajrungta/pcb-claude-plugin` instead of steps 2 and 3. That opens a short setup form (below).

### 4. Set your defaults (optional)

Inside Claude Code, open the settings form with `/plugin configure pcb@pcb-claude-plugin`. From a terminal, pass them when installing instead:

```bash
claude plugin install pcb@pcb-claude-plugin --config fab_house=JLCPCB --config layers=2
```

| Setting | Options | What it changes |
|---|---|---|
| Default PCB manufacturer | JLCPCB · PCBWay · OSH Park · Other | BOM / pick-and-place format and the design rules Claude aims for |
| How boards get built | Assembled by the manufacturer · Hand soldering | Part sizes (0402/0603 vs 0805+) and stocked-part preference |
| Default layer count | 2 · 4 | Starting layer count for new boards |
| Learn from my boards | on · off | Whether kipcb remembers what worked (stored only on your computer) |

### 5. Check the setup

Start a new Claude Code session. The plugin welcomes you and checks KiCad, Java and the autorouter, and only speaks up again if something is missing. You can also check by hand:

```bash
claude plugin list
```

```bash
kipcb doctor
```

### 6. Design your first board

```text
/pcb:design a 30 x 20 mm USB-C LED blinker with an ATtiny85 and an ISP header
```

<details>
<summary><b>Desktop app</b></summary>

In the **Code** tab, click **+** next to the prompt box → **Plugins** → **Add plugin**, add the
marketplace `virajrungta/pcb-claude-plugin`, select **PCB Design**, and choose a scope.

</details>

The [getting started guide](docs/getting-started.md) walks through a first board end to end.

### 7. Progress in your status bar (optional)

While a board is being designed, Claude Code shows a short progress line after
each step:

```text
PCB · esp32_lcd  ■■■■■□□□□ 5/9  ▶ Routing ~1 min
```

To keep it in Claude Code's status bar at the bottom of the screen instead (one
line, updating live), add this to `~/.claude/settings.json`:

```json
"statusLine": {"type": "command", "command": "~/.local/share/kipcb/statusline.sh"}
```

It's empty when no design is in progress, so it doesn't get in the way.

## Updating

A new version ships every week (see the [changelog](CHANGELOG.md) and [releases](https://github.com/virajrungta/pcb-claude-plugin/releases)). Third-party plugins don't update on their own unless you turn auto-update on, so update when a new version is out:

### 1. Refresh the marketplace

```bash
claude plugin marketplace update pcb-claude-plugin
```

### 2. Update the plugin

```bash
claude plugin update pcb@pcb-claude-plugin
```

### 3. Check the version

```bash
claude plugin list
```

Then restart Claude Code; the next session tells you which version you're on and links to what's new.

**Inside Claude Code:** `/plugin` → **Installed** → **PCB Design** → **Update now**.
<br>**Auto-update:** `/plugin` → **Marketplaces** → **pcb-claude-plugin** → **Enable auto-update**; new versions then download in the background.

### Uninstall

```bash
claude plugin uninstall pcb@pcb-claude-plugin
```

Your designs and local learning data stay on disk.

## Examples

<table>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/usb_blinker_angle.png" alt="USB-C ATtiny85 LED blinker" width="100%"><br>
      <sub><a href="examples/usb_blinker.json"><code>usb_blinker.json</code></a>: 30 × 22 mm, ATtiny85, USB-C power, ISP header.
      <a href="examples/c3_blocks.json"><code>c3_blocks.json</code></a> builds a 21-part ESP32-C3 board from four blocks in about 30 lines.</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/images/usb_blinker_top.png" alt="USB-C LED blinker, top view" width="100%"><br>
      <sub>Routed on the top layer so the bottom stays a solid ground plane</sub>
    </td>
  </tr>
</table>

Build one yourself from a clone. One command runs the whole pipeline (about 20 seconds) and ends with a report:

```bash
bin/kipcb run examples/c3_sensor.json
```

```text
READY TO ORDER: ESP32-C3 sensor node
Project   …/examples/c3_sensor/c3_sensor.kicad_pro
Board     58 x 43 mm, 2 layers, 20 parts, 14 nets
Checks    ERC ok, DRC ok, parity ok, noise 13 pass / 3 warn / 0 fail
Power     U1 (AMS1117-3.3) 5V->3.3V at 363 mA dissipates 0.62 W; SOT-223 handles about 1.0 W
Fab       fab/c3_sensor-gerbers.zip, fab/c3_sensor-bom.csv, fab/c3_sensor-cpl.csv
Previews  previews/review.png, previews/schematic.png
Time      check 0s, build 10s, route 7s, fab 2s (total 19s)
```

Every project folder has the same layout: the KiCad files, `fab/` (send these to the manufacturer), `previews/` (images and PDFs), `reports/` (`REPORT.md` and check results).

## Documentation

| Guide | For |
|---|---|
| [Getting started](docs/getting-started.md) | Installing, your first board, opening it in KiCad, ordering |
| [How it works](docs/how-it-works.md) | The pipeline, placement, routing, checks and learning |
| [CLI reference](docs/cli.md) | Every `kipcb` command, the output layout, environment variables |
| [Design spec format](skills/design/references/spec-format.md) | The JSON spec: parts, nets, placement hints, custom parts |
| [Circuit patterns](skills/design/references/circuit-patterns.md) | Proven sub-circuits and values Claude follows |
| [Layout and noise guidelines](skills/design/references/layout-guidelines.md) | Placement hints, routing and noise-check criteria |
| [Manufacturing](skills/design/references/manufacturing.md) | Fab rules, JLCPCB assembly and the ordering checklist |
| [Troubleshooting](docs/troubleshooting.md) | Common problems and fixes |

## Requirements

<table>
  <tr><td><b>Claude Code</b></td><td>Desktop app, CLI or IDE extension</td></tr>
  <tr><td><b>KiCad 9</b></td><td>Provides <code>kicad-cli</code>, <code>pcbnew</code> and the part libraries</td></tr>
  <tr><td><b>Java 21+</b></td><td>For the <a href="https://github.com/freerouting/freerouting">Freerouting</a> autorouter (downloaded with <code>kipcb setup-router</code>)</td></tr>
  <tr><td><b>OS</b></td><td>macOS or Linux</td></tr>
</table>

## Limits

Autorouted boards are functional, not optimal. Switching-regulator layout, RF, controlled-impedance and
high-speed signals (USB 480 Mb/s, Ethernet, DDR) need human review or hand routing in KiCad. The plugin
flags these rather than hiding them. **Always review a design before ordering boards.**

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, tests and release process.

## License

[MIT](LICENSE) © Viraj Rungta
