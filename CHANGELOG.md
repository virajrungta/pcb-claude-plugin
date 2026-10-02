# Changelog

All notable changes to the PCB Design plugin. Versions are released weekly as
`V<major>.<minor>` (V1.0, V1.1, …). Add notes under **Unreleased** as you work;
`scripts/release.sh <version>` moves them under the new version, tags the
release and publishes it on GitHub.

## Unreleased

_Nothing yet._

## V2.0 - 2026-10-02

**The PCB Design plugin now runs on Windows.** Describe a board in Claude Code
on Windows 10/11 and get the same result as on a Mac: a checked KiCad 9
project, a routed board and manufacturing files ready to order. It's the same
plugin and the same commands on both; only the one-time install of KiCad and
Java differs.

### Install on Windows

In PowerShell:

```powershell
winget install Git.Git
winget install KiCad.KiCad
winget install EclipseAdoptium.Temurin.21.JDK
```

Then, as on macOS:

```bash
claude plugin marketplace add virajrungta/pcb-claude-plugin
claude plugin install pcb@pcb-claude-plugin
```

Already using the plugin on macOS? Update as usual
(`claude plugin marketplace update pcb-claude-plugin`, then
`claude plugin update pcb@pcb-claude-plugin`); nothing changes for you.

### What's new

- **Windows 10/11 support.** kipcb finds KiCad 9 automatically (in
  `C:\Program Files\KiCad\9.x` or a per-user install) and runs on the Python
  that ships with KiCad, so there's no separate Python to install. Claude Code
  on Windows runs the plugin through Git Bash, which it already requires.
- **Previews on every platform.** Schematic previews no longer depend on
  macOS's `sips`. On Windows they're rendered from KiCad's SVG export by
  Microsoft Edge, which ships with Windows (headless; Chrome works too).
- **Windows-native locations.** Settings, learning and caches live in
  `%LOCALAPPDATA%\kipcb`, and install hints, the "open in KiCad" command
  (`start "" board.kicad_pro`) and the docs match the platform. The README
  has a side-by-side macOS / Windows setup and a "where files live" table.
- **Sturdier on any machine.**
  - Unicode output works in Windows consoles.
  - Scripts are pinned to LF line endings, so a Windows checkout can't break
    them.
  - When KiCad's Python can't be found, kipcb now says what it tried and why it
    failed.
- **Better placement on a fresh install.**
  - Chips with capacitors or resistors pinned to their pins now keep other
    parts at a distance, so those helpers fit right at the pin. Before, a new
    user (no learning history yet) could get a regulator pressed against the
    USB connector with its input capacitor 7 mm away, and preflight then
    stopped the run.
  - kipcb also sets up KiCad's global library tables if KiCad was never
    opened, and retries or times out kicad-cli calls that crash or hang.
- **Tested on Windows on every change.** A GitHub Actions job installs KiCad 9
  and Java on Windows, runs the test suite and designs the example boards end
  to end, requiring each to come out ready to order.

### Notes

- **Requirements:** KiCad 9, Java 21+, and on Windows Git for Windows. KiCad 10
  isn't supported yet.
- **Fine-pitch chips:** the RP2040 and STM32 blocks stay marked *advanced* on
  2 layers, on every platform.

## V1.11 - 2026-10-01

- **Status bar progress is built in**: a new install option, *Show design
  progress in the status bar* (on by default), makes the plugin set up Claude
  Code's status bar by itself at session start. There's nothing to run. It never
  replaces a status line you already have, and turning the option off removes
  it again. The "ask once" step in the design flow is gone.

## V1.10 - 2026-10-01

- **Readable progress**: Claude now reports progress in its own messages after
  each step (**PCB progress** ■■■■■□□□□ 5/9 · ▶ Routing (~1 min)). This
  replaces the dim "PostToolUse:Bash says" notice, which is how Claude Code
  shows any plugin hook message and couldn't be styled. The design commands
  print a ready-made `progress:` line for Claude to relay.
- **Status bar, one click**: the first time you design a board, Claude offers to
  pin live progress in Claude Code's status bar. `kipcb progress
  --install-statusline` sets it up and only touches `~/.claude/settings.json`
  if no status line is configured. It keeps a backup.

## V1.9 - 2026-10-01

- **Cleaner progress display**: the progress line is now a short bar,
  `PCB · board  ■■■■■□□□□ 5/9  ▶ Routing ~1 min` (or `✘ Preflight: 2 problems
  to fix`), shown only when progress changes instead of after every command.
- **Status bar option**: the same bar can live in Claude Code's status line
  and update in place. Add
  `"statusLine": {"type": "command", "command": "~/.local/share/kipcb/statusline.sh"}`
  to `~/.claude/settings.json`. It's empty when no design is in progress.
  `kipcb progress --statusline` prints it.

## V1.8 - 2026-10-01

- **Progress checklist from the start**: it now appears as soon as you've
  answered the requirements questions (`kipcb progress --start <name>`), not
  only once the design file exists, and fills in the step times when the
  estimate is ready.

## V1.7 - 2026-10-01

- **Progress checklist that actually shows up**: V1.6 asked Claude to use
  Claude Code's task-list tool, which many Claude Code versions don't have, so
  no checklist appeared. Now the plugin shows it itself after every
  `kipcb estimate` / `kipcb run` step, and it ticks forward as the design
  progresses:
  `PCB progress · board  ✔ Requirements  ✔ Components  ✔ Schematic  ✔ Placement 3s  ▶ Preflight ~1s  ☐ Routing ~6s  ☐ DRC  ☐ Files  ☐ Hand-off`.
  A failed step shows ✘ with the reason. It's drawn by a plugin hook from the
  pipeline's own progress file, so it can't be skipped and costs no tokens.
  `kipcb progress` prints the full checklist.
- The design skill starts the checklist right after the requirements
  questions, before any design work.

## V1.6 - 2026-10-01

- **Progress checklist**: after the requirements questions, Claude shows a
  checklist in Claude Code's task list and ticks it off as the work happens:
  Requirements → Components & circuit → Schematic → Placement → Preflight
  checks → Routing → DRC & noise checks → Manufacturing files → Review &
  hand-off. Long steps show their expected time and the reason. Backed by
  `kipcb run --until build|preflight|route` and `--resume`; each stage ends
  with a `checkpoint:` line.
- **Routing is several times faster on normal boards**: the ESP32-C3
  examples' route step (routing, pour, DRC, previews) takes 7-30 s instead of
  32-99 s, and the whole pipeline about 40 s. Two Freerouting processes running at once were slowing each
  other down and leaving connections unrouted. Attempts now run one at a time,
  each capped at 20 s, stop at the first complete route, and give up early
  when a round brings no improvement.
- **Fan-out for fine-pitch chips**: before routing, kipcb bridges neighbouring
  same-net pins and adds a locked escape stub from every used pin, so the
  router doesn't have to thread between 0.4 mm-pitch pads. The RP2040 test
  board now gets within about 9 connections on 2 layers (from 15-26). It stays
  marked advanced.
- **Better use of the board**:
  - Parts spread over spare room instead of packing into one corner. Parts
    that must sit at a pin (decoupling, crystal caps) stay close.
  - Auto-size no longer shrinks a board denser than most real boards that
    routed at that layer count.
  - Preflight warns when one area is crowded while the rest of the board is
    empty.
- **Learning fixes**: failures from before V1.6 no longer make boards bigger
  or add footprint margins. They were caused by bugs that are now fixed, and
  had been inflating board sizes.
- Fixed: a completion pass that ran out of time crashed the route step
  instead of keeping the best result.

## V1.5 - 2026-10-01

- **Preflight before routing**: a new step between build and route
  (`kipcb preflight`, automatic in `kipcb run`) checks in under a second
  whether the placed board can route cleanly:
  - every pad is reachable at its net's track width given the pin pitch;
  - each net has exactly one netclass;
  - track, clearance, via, drill and edge sizes are within the manufacturer's
    standard limits;
  - no pads too near the edge, no overlapping parts, no parts off the board;
  - decoupling and crystal parts are close to their pins;
  - fine-pitch chips have room to fan their tracks out;
  - routing density compared with real routed boards and your own history.

  Failures stop the run before the slow routing step and print the fix.
- **Fine-pitch chips route**: nets on 0.4-0.5 mm pitch parts (RP2040's
  QFN-56, STM32's LQFP-48, ...) automatically get the track width and
  clearance real boards use at that pitch (0.2 / 0.15 mm at 0.4 mm). Before,
  0.25 mm tracks physically couldn't reach those pins. Fine-pitch chips also
  get extra room around them for escape routing.
- **Fixes**:
  - A net could end up in two netclasses, which KiCad merged with the wrong
    width.
  - Rebuilds kept the previous build's netclass assignments.
  - The autorouter didn't respect the copper-to-edge clearance.
  - Board-wide minimum clearance and track width could be stricter than a
    netclass, which turned that class's legitimate tracks into DRC errors.
  - Stale routing and manufacturing results survived a rebuild.
- **Much bigger parts catalog**:
  - JLCPCB part numbers for 265 resistor and capacitor values (0402-1206,
    from JLCPCB's basic library, no extra assembly fee). Capacitors use the
    highest voltage rating stocked, and `kipcb check` warns when a capacitor
    sits on a rail too close to its rating.
  - About 50 new named parts with verified part numbers: transistors and
    MOSFETs, diodes, TVS, polyfuse, regulators, crystals, ferrite beads,
    inductor, op-amps, logic, interface chips, sensors, MCUs, microSD socket,
    relay, barrel jack, buzzer.
  - LED colours pick the part (`"part": "LED0805", "value": "green"`).
- **28 new blocks (43 total)**:
  - MCUs and USB: `rp2040_minimal`, `stm32f103c8_core`, `esp32_wroom32e`,
    `ch340c_usb_uart`, `esp_autoprogram`.
  - Power: `buck_ap63203_3v3`, `ldo_ams1117_5v0`, `ldo_xc6206_3v3`,
    `lipo_charger_tp4056`, `input_protection_5v`, `reverse_polarity_pfet`,
    `dc_jack_input`.
  - Drivers: `drv8833_motor`, `relay_5v`, `buzzer_driver`.
  - Sensors and peripherals: `bme280_i2c`, `sht31_i2c`, `mpu6050_i2c`,
    `ina219_current`, `ads1115_adc`, `ds3231_rtc`, `micro_sd_spi`.
  - Buses and headers: `rs485_transceiver`, `can_sn65hvd230`,
    `level_shifter_bss138`, `swd_header`, `uart_header`, `oled_i2c_header`.
  - Values come from datasheets and real open-source boards.
  - `rp2040_minimal` and `stm32f103c8_core` are marked advanced: on 2 layers
    autorouting may leave a few connections near the chip (4 layers or a quick
    hand-route finishes them). Automatic fan-out for such chips is planned.
- **Block options**: `"omit": ["R1"]` drops a part from a block. Bus ports
  (USB, SWD, reset) join a same-named net automatically. `kipcb blocks i2c`
  filters the list, and `kipcb blocks a b` shows several blocks.
- **Live JLCPCB data**: `kipcb lcsc C25804` shows stock, price and
  basic/extended for a part, and `kipcb lcsc -s "SHT31 | 10uF 0805"` searches
  JLCPCB's library. Manufacturing export warns about out-of-stock BOM parts.
- **Learning**:
  - The knowledge base now includes design rules mined from 417 real boards:
    track widths and clearances at each pin pitch, and the routing density
    real 2- and 4-layer boards reach.
  - Preflight and routing results feed a local routability estimate.
- **Time checkpoints**: before a run, Claude tells you the approximate time
  of each step (check, build, preflight, route, files) and why a step is
  long. For example, boards with 0.4 mm-pitch chips need 5-8 minutes of
  autorouting. `kipcb run` prints the same plan, and `kipcb estimate` shows it
  on its own. Estimates switch to your own measured timings once similar boards
  have run.
- **README**: installation and updating are now step-by-step terminal
  commands, including the update commands.
- **Says what's possible before designing**: after the requirements
  questions, Claude sorts the features into automatic, advanced (may need
  4 layers or a little hand routing, e.g. the RP2040) and not supported (BGA,
  custom RF, high-speed links, mains), and offers easier alternatives before
  any work starts. Blocks carry a support level, `kipcb blocks` marks advanced
  ones, `kipcb check` prints `SUPPORT:` lines, and `kipcb guide capabilities`
  has the full list.
- `usb_blinker.json` now uses an in-stock ATtiny85.

## V1.4 - 2026-09-30

- **Prebuilt circuit blocks**: 15 verified sub-circuits that drop into a design
  with one line and need no part research: `usb_c_power`, `usb_c_data` (with
  ESD), `ldo_ams1117_3v3`, `ldo_ap2112_3v3`, `lipo_charger_mcp73831`,
  `esp32_c3_wroom02`, `esp32_s3_wroom1`, `atmega328p_16mhz`, `ws2812b_led`,
  `led_indicator`, `button`, `i2c_pullups`, `qwiic_connector`,
  `lowside_switch`, `voltage_divider`. Ports join your nets by name or through
  `connect`; spare MCU pins become no-connects automatically; part numbers are
  assigned to fit. `kipcb blocks` lists them.
- **Prebuilt part names**: `{"part": "R0603", "value": "10k"}` fills in symbol,
  footprint and, for common values, the JLCPCB part number. Covers passives,
  LEDs, buttons, crystals, headers of any size, USB-C connectors, regulators,
  the charger, ESD, MCUs and modules, the WS2812, LM358 and AO3400A.
  `kipcb parts` lists them.
- **Remembers your parts**: chips and connectors from every board that reaches
  "ready to order" are saved locally and usable by name next time.
- **New example** `c3_blocks.json`: the ESP32-C3 sensor board in 1,253
  characters instead of 4,309, with no library searches, and 7 parts without
  JLCPCB numbers instead of 17.
- Every block and part is tested against KiCad's libraries
  (`tests/test_blocks_kicad.py`).

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
