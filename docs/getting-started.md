# Getting started

This guide takes you from nothing installed to a board ready to order.

## 1. Install the tools

| Tool | Why | How |
|---|---|---|
| **Claude Code** | Runs the plugin | [claude.com/claude-code](https://claude.com/claude-code): the desktop app, terminal CLI or IDE extension |
| **KiCad 9** | Schematics, boards, checks, exports | [kicad.org/download](https://www.kicad.org/download/). The default install location is fine |
| **Java 21+** | Runs the Freerouting autorouter | macOS: `brew install --cask temurin`. Linux: your package manager (`openjdk-21-jre`) |

macOS and Linux are supported. On Linux, install KiCad from the official PPA or
Flatpak so that `python3 -c "import pcbnew"` works.

## 2. Install the plugin

In Claude Code, run:

```text
/plugin install pcb --marketplace virajrungta/pcb-claude-plugin
```

Confirm the marketplace and choose **Install for you**. A setup form asks for
your usual manufacturer, how boards get built (assembled or hand-soldered),
your default layer count, and whether kipcb may learn from your boards
(locally). Change these later with `/plugin configure pcb@pcb-claude-plugin`.

Turn on auto-update so weekly versions arrive automatically: `/plugin` →
**Marketplaces** → **pcb-claude-plugin** → **Enable auto-update**.

Start a new session. The plugin welcomes you and checks your toolchain.

## 3. Check the toolchain

```bash
kipcb doctor
```

You should see KiCad, `pcbnew` and Java reported as OK. If Freerouting
shows as not installed, run:

```bash
kipcb setup-router
```

It downloads the Freerouting autorouter (~65 MB) from its official GitHub
releases into `~/.cache/kipcb`. (When you use `/pcb:design`, Claude asks
before downloading it.)

## 4. Design a board

In Claude Code, from the folder where you want the project:

```
/pcb:design a USB-C powered ESP32-C3 board with an RGB status LED, reset and boot buttons, and a 4-pin I2C header
```

What happens next:

1. **Questions.** Claude asks a few quick questions: board size, power source
   and current, which connectors go on which edges, assembly vs hand
   soldering, and whether anything is noise-sensitive. Answer what you know;
   sensible defaults cover the rest.
2. **Architecture and parts.** Claude proposes a block diagram and power tree,
   then picks real parts from KiCad's libraries, checking every pin and following
   each chip's datasheet reference circuit.
3. **Spec and validation.** The circuit is written as a JSON design spec in
   `hardware/<name>.json` and validated. Every pin must be connected or
   explicitly marked unused.
4. **Build and inspect.** The schematic and a placed board are generated,
   checked, and rendered. Claude looks at the renders and adjusts placement.
5. **Route.** The board is autorouted, ground planes are poured, and DRC plus
   the noise checks run until clean.
6. **Manufacturing files.** Gerbers, BOM and pick-and-place files land in
   `hardware/<name>/fab/`.

Claude finishes with the run report: whether the board is ready to order, **where
the project is**, board size and layers, which checks passed, the
manufacturing files, and anything that deserves a human look, plus the
assumptions it made. The same summary is saved in `reports/REPORT.md` inside the
project, and `kipcb report <project>` prints it again any time.

```
hardware/<name>/
  <name>.kicad_pro / .kicad_sch / .kicad_pcb   open in KiCad
  fab/        Gerber zip, BOM, pick-and-place: send these to the manufacturer
  previews/   board and schematic images, printable PDFs
  reports/    REPORT.md and the check results
```

## 5. Open it in KiCad

```bash
open hardware/<name>/<name>.kicad_pro
```

(`xdg-open` on Linux.) It's a normal KiCad project: edit the schematic, nudge
parts, or hand-route critical nets.

> **Spec vs hand edits.** Claude edits the JSON spec and rebuilds, and a
> rebuild regenerates the KiCad files. Iterate with Claude first, then switch
> to KiCad for final polish. After that, `/pcb:review` and `/pcb:fab` work
> directly on your edited files.

## 6. Order boards

```
/pcb:fab hardware/<name>
```

Upload `fab/<name>-gerbers.zip` to your fab (JLCPCB, PCBWay, OSH Park…). For
assembly, also upload `fab/<name>-bom.csv` and `fab/<name>-cpl.csv`, and check
part rotations in the fab's preview. Claude never places orders for you.

## Try the examples first

```bash
git clone https://github.com/virajrungta/pcb-claude-plugin
```

```bash
cd pcb-claude-plugin && bin/kipcb run examples/usb_blinker.json
```

Then open `examples/usb_blinker/usb_blinker.kicad_pro` in KiCad.
