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
| `kipcb sym-search "a \| b \| c"` | Several searches in one call (8 results for one query, 5 each for several) |
| `kipcb sym-info A B C` | Compact pins of several symbols in one call (`-v` adds the datasheet URL) |
| `kipcb guide [topic]` | Prints one section of the design references (USB-C, LDO, decoupling, placement hints…); no topic lists them |
| `kipcb fmt spec.json` | Rewrites a spec in the compact one-line-per-part layout |
| `kipcb blocks [names or words]` | Prebuilt circuit blocks (43). Block names show ports, optional ports, parameters and parts; other words filter the list (`kipcb blocks i2c`) |
| `kipcb parts [query]` | Prebuilt part names for `"part": "…"` (symbol, footprint, JLCPCB part number), plus parts remembered from your boards |
| `kipcb lcsc C25804 …` | Live JLCPCB data for part numbers: part, package, basic/extended, stock, price |
| `kipcb lcsc -s "SHT31 \| 10uF 0805" [--basic]` | Searches JLCPCB's parts library, in-stock and basic parts first |
| `kipcb ref <part>` | Shows how open-source designs wire a part (uses the knowledge base shipped with the plugin) |

## Design

| Command | What it does |
|---|---|
| `kipcb run spec.json [--no-fab] [--force]` | **The whole pipeline**: check → build → preflight → route → manufacturing files → report, with one progress line per step. An unchanged spec returns the last report instantly |
| `kipcb estimate spec.json [--no-fab]` | Approximate time for each step of `kipcb run` on this design, with the reason when a step is long (e.g. fine-pitch chips); learns from your own run times |
| `kipcb preflight spec.json` | Checks in under a second whether a built board can route cleanly: pad reach at each pin pitch, one netclass per net, manufacturer limits, copper near the edge, overlaps, decoupling and crystal placement, escape room around fine-pitch chips, routing density. `run` stops before routing if any of these fail |
| `kipcb check spec.json` | Validates the spec: symbols, footprints, pins, nets, unused pins, logic levels, and the power budget / regulator heat |
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
| `kipcb render <project> [--what build\|review\|full]` | Previews into `previews/`: `review.png` (board, top), `schematic.png/.pdf`; `full` adds `bottom.png` and `board.pdf` |
| `kipcb report <project>` | Prints the summary (status, project location, board, checks, files, what to check); also in `reports/REPORT.md` |

## Learning

| Command | What it does |
|---|---|
| `kipcb settings` | Your defaults from the install dialog |
| `kipcb learn` | What has been learned: strategy success rates, hard footprints, sizing, and how many runs were ignored |
| `kipcb learn reset` | Forget all local experience |

## Output layout

```
my_board.json              design spec (source of truth)
my_board/
  my_board.kicad_pro/.kicad_sch/.kicad_pcb    the KiCad project
  fab/        <name>-gerbers.zip, <name>-bom.csv, <name>-cpl.csv, gerbers/
  previews/   review.png, schematic.png/.pdf, bottom.png, board.pdf
  reports/    REPORT.md, erc/drc/noise/placement/build/route/fab .json
  .kipcb/     router working files and logs (safe to delete)
```

## Environment variables

| Variable | Purpose |
|---|---|
| `KIPCB_PYTHON` | Python with `pcbnew` (if KiCad isn't in the default location) |
| `KIPCB_KICAD_CLI` | Path to `kicad-cli` |
| `KIPCB_FREEROUTING_JAR` | Use a specific Freerouting jar |
| `KIPCB_KNOWLEDGE` | Path to a `knowledge.json` (default: `~/.local/share/kipcb/knowledge.json` if you built one, else the copy in the plugin's `data/`) |
| `KIPCB_EXPERIENCE` | Path to the experience log |
| `KIPCB_LEARN=0` | Disable learning |
| `KIPCB_CACHE` | Library index and Freerouting cache (default `~/.cache/kipcb`) |
