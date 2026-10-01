# Changelog

All notable changes to the PCB Design plugin. Versions are released weekly as
`V<major>.<minor>` (V1.0, V1.1, …). Add notes under **Unreleased** as you work;
`scripts/release.sh <version>` moves them under the new version, tags the
release and publishes it on GitHub.

## Unreleased

_Nothing yet._

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
