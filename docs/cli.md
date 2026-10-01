# `kipcb` command reference

`kipcb` is on your PATH whenever the plugin is enabled (it lives in `bin/`).
It runs under KiCad's bundled Python so it can use `pcbnew`. Claude runs
these commands for you, but they also work on their own.

## Setup

| Command | What it does |
|---|---|
| `kipcb doctor` | Checks KiCad, `pcbnew`, Java and Freerouting, plus the knowledge base and learning status |
| `kipcb setup-router [--version X]` | Downloads Freerouting into `~/.cache/kipcb` (picks the newest version your Java can run) |

## Parts

| Command | What it does |
|---|---|
| `kipcb sym-search <words>` | Searches symbol libraries; shows pin count and default footprint |
| `kipcb sym-info Lib:Name` | Lists every pin (number, name, electrical type, unit) |
| `kipcb fp-search <words>` | Searches footprint libraries |
| `kipcb fp-info Lib:Name` | Shows a footprint's pads and courtyard size |
| `kipcb ref <part>` | Shows how open-source designs wire a part (needs the knowledge base) |

## Design

| Command | What it does |
|---|---|
| `kipcb check spec.json` | Validates the spec: symbols, footprints, pins, nets, unused pins |
| `kipcb build spec.json [-o DIR] [--no-render]` | Generates the schematic and placed board, runs ERC, the placement DRC and placement noise checks, and renders previews |
| `kipcb route <project> [--attempts N] [--timeout S] [--no-pour]` | Autoroutes, pours ground, adds stitching vias, then runs DRC and noise checks |
| `kipcb fab <project> [--fab jlcpcb\|generic] [--force]` | Exports Gerbers and drill zip, BOM and CPL. Refuses while DRC has errors |

`<project>` can be the project folder, the `.kicad_pro` / `.kicad_pcb`, or the spec `.json`.

## Checks and inspection

| Command | What it does |
|---|---|
| `kipcb erc <project>` | Schematic electrical rules check |
| `kipcb drc <project>` | Board design rules check, including schematic parity |
| `kipcb noise <project> [-q]` | Noise / signal-integrity checks (`-q` shows problems only) |
| `kipcb netlist <project>` | Components and nets with pin functions, for review |
| `kipcb render <project> [--what all\|sch\|pcb\|3d]` | Schematic PDF/PNG, board PDF and 3D PNG renders into `out/` |

## Learning

| Command | What it does |
|---|---|
| `kipcb learn` | What has been learned: strategy success rates, hard footprints, sizing |
| `kipcb learn reset` | Forget all local experience |

## Output layout

```
my_board.json              design spec (source of truth)
my_board/
  my_board.kicad_pro/.kicad_sch/.kicad_pcb
  out/    renders, ERC/DRC/noise reports, placement.json, router logs
  fab/    <name>-gerbers.zip, <name>-bom.csv, <name>-cpl.csv, gerbers/
```

## Environment variables

| Variable | Purpose |
|---|---|
| `KIPCB_PYTHON` | Python with `pcbnew` (if KiCad isn't in the default location) |
| `KIPCB_KICAD_CLI` | Path to `kicad-cli` |
| `KIPCB_FREEROUTING_JAR` | Use a specific Freerouting jar |
| `KIPCB_KNOWLEDGE` | Path to a `knowledge.json` (default `~/.local/share/kipcb/knowledge.json`) |
| `KIPCB_EXPERIENCE` | Path to the experience log |
| `KIPCB_LEARN=0` | Disable learning |
| `KIPCB_CACHE` | Library index and Freerouting cache (default `~/.cache/kipcb`) |
