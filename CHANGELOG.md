# Changelog

All notable changes to the PCB Design plugin. Versions are released weekly as
`V<major>.<minor>` (V1.0, V1.1, …). Add notes under **Unreleased** as you work;
`scripts/release.sh <version>` moves them under the new version, tags the
release and publishes it on GitHub.

## Unreleased

- **Faster failures**: `kipcb route` refuses in about a second when a part sits
  outside the board, instead of spending minutes on routing attempts that can
  never succeed.
- **Parts no longer get stranded**: if a part doesn't fit at the chosen spacing
  (e.g. `"roomy"` on a small board), placement retries with tighter spacing
  before giving up, and says so in the build output.

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
