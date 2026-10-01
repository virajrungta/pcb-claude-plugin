# Changelog

All notable changes to the PCB Design plugin. Versions are released weekly as
`V<major>.<minor>` (V1.0, V1.1, …). Add notes under **Unreleased** as you work;
`scripts/release.sh <version>` moves them under the new version, tags the
release and publishes it on GitHub.

## Unreleased

_Nothing yet._

## V1.3 - 2026-09-30

- **One command for the whole pipeline**: `kipcb run <spec>` checks, builds,
  routes and exports manufacturing files, prints one line per step, and ends
  with a report: ready or not, **the project's location**, board size, checks,
  power budget, manufacturing files, previews, what to check, and timing. The
  report is saved as `reports/REPORT.md`, and `kipcb report` prints it again.
  An unchanged spec returns the last report instantly.
- **About 3x faster**: routing tries two strategies at once and stops the moment
  one finishes cleanly (ESP32-C3 example: 44 s → 7 s; full run about 20 s
  instead of over a minute). Previews render in parallel, and only what's needed.
- **Organized project folders**: `fab/` (send to the manufacturer),
  `previews/` (images and PDFs), `reports/` (report and check results), and a
  hidden `.kipcb/` for router working files.
- **Far fewer tokens per design**:
  - part search output is ~75% smaller, and `kipcb sym-search "a | b | c"`
    runs several searches in one call
  - `kipcb sym-info A B C` gives compact pins for several parts at once (~70% smaller)
  - `kipcb guide <topic>` prints one section of the design references
    (~50–300 tokens) instead of whole files (~2,000 each)
  - previews are sized for review: one ~1100 px board image per iteration,
    with the schematic image only when the circuit changes
  - a compact spec layout (nets as one-line strings, one line per part) is
    ~30% smaller; `kipcb fmt` converts existing specs
  - the design skill is ~40% shorter and tells Claude to batch lookups, run the
    whole pipeline in one call, edit specs instead of rewriting them, check
    `kipcb ref` before fetching datasheets, and use the reviewer agent only
    for complex boards
  - the reviewer agent is capped at three datasheet fetches and a short report

## V1.2 - 2026-09-30

- **Setup dialog at install**: installing from `/plugin` asks for your usual
  manufacturer, how boards get built (assembled or hand-soldered), your
  default layer count, and whether kipcb may learn from your boards. Claude
  uses these as defaults instead of asking every time, `kipcb fab` picks the
  matching output format, and `/plugin configure pcb@pcb-claude-plugin`
  changes them later.
- **Welcome and update notices**: the first session after installing shows a
  welcome with an example to try; after an update it tells you the new
  version and links to what's new. It also warns when KiCad, Java or the
  autorouter is missing, and otherwise stays quiet.
- **`kipcb settings`** shows your defaults.
- **Install guide**: one-line install (`/plugin install pcb --marketplace
  virajrungta/pcb-claude-plugin`), the setup dialog, desktop-app and terminal
  installs, auto-update, updating, changing settings and uninstalling are all
  documented in the README.

## V1.1 - 2026-09-30

- **Faster failures**: `kipcb route` refuses in about a second when a part sits
  outside the board, instead of spending minutes on routing attempts that can
  never succeed.
- **Parts no longer get stranded**: if a part doesn't fit at the chosen spacing
  (e.g. `"roomy"` on a small board), placement retries with tighter spacing
  before giving up, and says so in the build output.
- **Learning ignores broken runs**: routing attempts that followed a placement
  with a part off the board no longer count against good strategies or mark
  innocent footprints as "hard". Existing experience logs are repaired
  automatically. A failure at a size that has also routed fine no longer
  inflates future boards.
- **Knowledge base ships with the plugin**: `kipcb ref <part>` works out of the
  box (82 parts from 391 open-source projects). A locally built base still
  takes priority.
- **Logic-level check**: `kipcb check` flags signals shared by chips on
  different supply voltages (e.g. a 3.3 V MCU driving a 5 V shift register).
- **Power budget**: add `current_ma` to the main loads, and `kipcb check` adds
  up each rail and warns when a linear regulator would run too hot for its
  package. Rail voltages come from net names, or from a new `rails` field.
- **Auto-shrink**: auto-sized boards shrink until the parts no longer fit at
  full spacing (the ESP32-C3 example went from 76 x 55 to 57 x 42 mm), never
  below routing room or a size that failed before. `"auto_shrink": false` opts out.
- **Cleaner routing**: a small safety margin for the autorouter removes
  occasional micron-level clearance errors.

## V1.0 - 2026-09-30

- **Idea to PCB**: `/pcb:design` takes a plain-English idea through
  requirements, part selection, schematic, placement, autorouting, checks
  and manufacturing files, producing a normal KiCad 9 project.
- **Requirements first**: a short mandatory question round (size, power,
  I/O, build method, noise sensitivity) before any part is chosen.
- **Design review**: `/pcb:review` checks any KiCad project (ERC, DRC,
  netlist checklist, noise checks, renders); a `circuit-reviewer` agent gives an
  independent second opinion against datasheets.
- **Manufacturing**: `/pcb:fab` exports Gerbers and drill files, a
  JLCPCB-format BOM and pick-and-place file, plus an ordering checklist.
- **Placement**: connectors flush to edges facing out, decoupling caps
  placed first and pulled to their pins, exact courtyard shapes, spacing
  presets, `near` / `away_from` / `edge` hints.
- **Routing**: Freerouting with several strategies, a completion pass,
  ground pours with stitching vias, and fixes for stacked connector pads.
- **Noise checks** (`kipcb noise`): decoupling distance, ground-plane
  coverage, bottom-layer cuts, stitching, noisy and sensitive nets, crystals,
  switch-node loops, differential pairs, supply track width.
- **Learning**: routing strategy, board sizing and footprint clearances
  improve with every board you route (`kipcb learn`), and `kipcb ref`
  shows how open-source designs wire a part.
