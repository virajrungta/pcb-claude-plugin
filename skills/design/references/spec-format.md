# Design spec format (`<name>.json`)

One JSON file describes the whole board. `kipcb check` validates it, and
`kipcb build` turns it into `<spec dir>/<name>/<name>.kicad_{pro,sch,pcb}`.

```json
{
  "name": "usb_blinker", "title": "USB-C ATtiny85 LED blinker", "revision": "A",
  "requirements": {"size": "30x22 mm", "power": "USB-C 5 V, < 50 mA", "build": "JLCPCB assembly", "noise": "none critical"},
  "board": {"width": 30, "height": 22, "layers": 2, "corner_radius": 1.5, "mounting_holes": {"size": "M3", "inset": 3.5}, "ground_pour": "GND"},
  "rules": {"track": 0.25, "power_track": 0.5, "clearance": 0.2},
  "power_nets": "GND VBUS",
  "components": [
    {"ref": "U1", "symbol": "MCU_Microchip_ATtiny:ATtiny85-20S", "value": "ATtiny85-20S", "lcsc": "C31540447", "current_ma": 10, "group": "mcu"},
    {"ref": "C2", "symbol": "Device:C", "footprint": "Capacitor_SMD:C_0603_1608Metric", "value": "100nF", "group": "mcu", "place": {"near": "U1.VCC"}},
    {"ref": "J1", "symbol": "Connector:USB_C_Receptacle_PowerOnly_6P", "footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal", "value": "USB-C", "place": {"edge": "left"}}
  ],
  "nets": {
    "VBUS": "J1.VBUS U1.VCC C2.1",
    "GND": "J1.GND J1.SHIELD U1.GND C2.2"
  },
  "no_connect": "U1.PB3 U1.PB4"
}
```

Write specs in this compact layout (one line per component, nets and
`power_nets`/`no_connect` as space-separated strings; JSON lists also work).
`kipcb fmt <spec>` rewrites any spec into it.

## Blocks

Prebuilt, verified sub-circuits. `kipcb blocks` lists them; `kipcb blocks <name>`
shows ports, optional ports, defaults and parameters.

```json
"blocks": [
  {"use": "usb_c_power", "params": {"edge": "bottom"}},
  {"use": "ldo_ap2112_3v3", "connect": {"VIN": "VBAT"}},
  {"use": "esp32_c3_wroom02", "connect": {"IO4": "SDA", "IO5": "SCL"}},
  {"use": "led_indicator", "name": "pwr_led", "connect": {"IN": "+3V3"}, "params": {"color": "Red"}}
]
```

- **Ports** join the spec net named in `connect`; otherwise the block's default
  (e.g. a regulator's `VIN` defaults to `VBUS`); otherwise the net with the
  port's own name (`GND`, `+3V3`, `USB_DP`…). Power ports are added to `power_nets`.
- **Optional ports** (spare MCU IOs, a WS2812's `DOUT`) that aren't connected
  become no-connects, so you don't list them. Bus ports a block marks
  `autojoin` (USB_DP/DN, SWDIO/SWCLK, NRST, EN/IO0, DTR/RTS) instead join a
  same-named net when another block or the spec has one.
- **`"omit": ["R1"]`** drops parts from a block by their ref inside the block
  (`kipcb blocks <name>` lists them), e.g. the 120 ohm terminator of an RS-485
  or CAN node in the middle of a bus.
- **Refs** are renumbered to avoid clashes with your own parts and each other;
  `kipcb check` prints the mapping. Nets inside a block are named `<block>_<net>`.
- Use the same block twice with different `name`s (e.g. two `led_indicator`s).

## Prebuilt parts

`"part": "<name>"` fills `symbol`, `footprint`, a default `value` and
`current_ma`, and a JLCPCB/LCSC number (265 R/C values; LED colours like
`"value": "green"`; ~80 named parts) (e.g. `R0603` + `10k` →
C25804). `kipcb parts` lists them: `R0402/R0603/R0805`, `C0402/C0603/C0805/C1206`,
`LED0603/LED0805`, `SCHOTTKY_SOD123`, `BUTTON`, `CRYSTAL_3225`,
`HEADER_1x04`-style headers (any count), `JST_SH_4`, `USB-C-6P/16P`,
`AMS1117-3.3`, `AP2112K-3.3`, `MCP73831`, `USBLC6-2SC6`, ESP32-C3/S3 modules,
`ATMEGA328P-AU`, `ATTINY85-SOIC`, `WS2812B-2020`, `LM358-SOIC`, `AO3400A`. Parts from
your own ready-to-order boards are remembered and usable by their value.
Fields you set yourself (e.g. `footprint`) win over the catalog.

## Top level

| key | required | meaning |
|---|---|---|
| `name` | yes | Project/file name: letters, digits, `_`, `-` |
| `title`, `revision`, `author` | no | Title block |
| `requirements` | recommended | Free-form record of what the user asked for (size, power, current, build, noise). Tools ignore it; the reviewer agent checks the design against it |
| `board` | no | See below. Omit width/height to auto-size from the parts |
| `net_roles` | no | Override the noise checker's guess: `{"SENSE_IN": "sensitive", "MOTOR_PWM": "noisy", "LED_DIN": "quiet"}` |
| `rails` | no | Voltages of supply nets whose names don't say it, e.g. `{"VCC": 3.3, "VMOT": 12}`. `+3V3`, `+5V`, `3V3`, `+1V8`, `12V`, `VBUS` and `VBAT` are recognised automatically |
| `rules` | no | Design rules in mm; defaults shown above (JLCPCB-safe) |
| `power_nets` | recommended | Supply/ground nets. They get the wider `power_track` (ground is routed thin and poured instead), power symbols in the schematic, and PWR_FLAGs when nothing on the board drives them (e.g. a connector input) |
| `net_classes` | no | Extra classes: `name`, `track`, `clearance`, `via_diameter`, `via_drill`, `nets` |
| `libraries` | no | Custom `.kicad_sym` / `.pretty` libraries, paths relative to the spec |
| `components` | yes | List of parts (below) |
| `nets` | yes | Map of net name → list of pins |
| `no_connect` | if needed | Pins deliberately left unconnected |

## `board`

- `width`, `height` (mm). Omit both to auto-size: kipcb picks a starting size from
  the parts and what has routed before, then shrinks the board while every
  part still fits at full spacing.
- `auto_shrink`: `false` keeps the auto-sized board at its starting size. Use this for
  noise-sensitive boards, where the extra room keeps routes on the top layer.
- `layers`: 2 or 4. With 4, inner layers are available to the router. Pour planes are still added on the outer layers only.
- `corner_radius` (mm, default 1).
- `mounting_holes`: `"M3"`, or `{"size": "M2|M2.5|M3|M4", "inset": 3.5}`
  (four corners), or `{"size": "M3", "positions": [[x, y], ...]}`. Parts are kept clear of them.
- `ground_pour`: net to pour on both copper layers (default: GND if present). `null` disables it.
- `spacing`: `"compact"`, `"normal"` (default) or `"roomy"`. Sets the clearance
  between parts; ICs get extra room to fan out their traces. Use roomy for
  hand soldering or noise-sensitive boards, and compact only when size is critical.

Coordinates are mm from the board's **top-left** corner, with X right and Y down.

## Components

| key | meaning |
|---|---|
| `ref` | Reference designator: `R1`, `C3`, `U2`, `J1`, `SW1`, `D4`, `Q1`, `Y1`, `L1`, `FB1`, `TP1` |
| `symbol` | `Library:Symbol` from KiCad's libraries (check with `kipcb sym-info`) |
| `footprint` | `Library:Footprint`. Optional when the symbol has a default footprint (the `sym-search` output shows `fp=`); required for generic symbols like `Device:R` |
| `value` | Shown on the schematic and BOM (`10k`, `100nF`, `AMS1117-3.3`) |
| `lcsc`, `mpn`, `manufacturer` | Sourcing fields, carried into the BOM (`LCSC Part #` for JLCPCB) |
| `fields` | Extra symbol fields `{"Tolerance": "1%"}` |
| `dnp` | `true` = do not populate |
| `current_ma` | Typical/peak current this part draws from its supply (mA). Set it on the main loads (MCU or radio module, LEDs, motors, sensors with heaters). `kipcb check` then adds up each rail and checks linear regulator heat against its package |
| `group` | Schematic grouping (parts sharing a group sit together): `power`, `mcu`, `usb`… |
| `place` | Placement hints (below) |

### Placement hints (`place`)

- `{"edge": "left|right|top|bottom"}`: flush against that board edge. For
  connectors the mating side is turned to face outward automatically. Add
  `"at": 12` for the position along the edge (mm) and `"rot"` to force a rotation.
- `{"near": "U1"}` or `{"near": "U1.VDD"}`: pull strongly toward a part or a
  specific pin. Use this for decoupling caps, crystals, pull-ups and ESD parts.
- `{"x": 10, "y": 5, "rot": 90}`: fixed position of the footprint origin (mm), rotation CCW in degrees.
- `{"rot": 0}`: constrain rotation only.
- `{"away_from": ["L1", "U5"], "min_dist": 8}`: keep at least `min_dist` mm
  (edge to edge) from those parts. Use it to separate noisy parts (switching
  regulators, inductors, clocks, motor drivers) from sensitive ones (analog
  front-ends, ADC inputs, antennas). Can be combined with `near`.
- `{"side": "bottom"}`: with fixed x/y, put the part on the back.

Anything without hints is placed by connectivity, with the biggest parts first.
Decoupling caps (a `near` hint on a supply pin) are placed before other helpers,
so they win the spot next to the pin.

## Pins in `nets` / `no_connect`

`REF.PIN`, where PIN is either
- the pin **number** (`U1.8`, `J1.A5`), which is always unambiguous; or
- the pin **name** (`U1.VCC`, `J1.CC1`). Overbar markup is ignored
  (`~{RESET}` matches `RESET`). For multi-function names like `RESET/PB5`, any
  part (`PB5`) matches. **A name matches every pin with that name**:
  `U2.GND` connects all GND pins, and `J1.VBUS` connects all VBUS pins,
  including hidden stacked ones.

Every symbol pin must appear in exactly one net or in `no_connect`
(`kipcb check` errors otherwise). Pins whose type is `no_connect` are exempt.

Net names: no spaces or braces. Use `+3V3`, `+5V`, `VBUS`, `GND` for power so
the standard KiCad power symbols are used. Signal nets: `SDA`, `USB_DP`, `LED_R`.

## Power budget

Give the main loads a `current_ma` (MCU or radio module at its peak, LEDs,
motors, heaters). `kipcb check` then adds up each rail (rail voltages come from
names like `+3V3`, `5V`, `VBUS`, or the `rails` field) and works out each linear
regulator's heat, (Vin − Vout) × I, against what its package can dissipate:
SOT-23 about 0.35 W, SOT-89 0.6 W, SOT-223 1 W, TO-252/DPAK 1.5 W. Over the
limit: a bigger package, a lower input voltage, or a buck converter.

## Multi-unit parts

Dual op-amps, logic gates and similar get one schematic symbol per unit
(U4A, U4B, plus a power unit). Wire them by pin number (`U4.1`, `U4.8`).
`kipcb sym-info` lists each pin's unit.

## Custom parts (not in KiCad's libraries)

1. Prefer an equivalent stock part (same package and pinout) if one exists.
2. Otherwise fetch the part from LCSC/EasyEDA with the open-source
   `easyeda2kicad` tool. Ask the user before installing it:
   `pip install easyeda2kicad`, then
   `easyeda2kicad --full --lcsc_id=C2040 --output ./lib/easyeda`
   This creates `./lib/easyeda.kicad_sym` and `./lib/easyeda.pretty`. Add
   `"libraries": {"symbols": {"easyeda": "./lib/easyeda.kicad_sym"}, "footprints": {"easyeda": "./lib/easyeda.pretty"}}`
   and reference `easyeda:<SymbolName>`. Check pins with `kipcb sym-info`, and
   check pads with `kipcb fp-info` against the datasheet: third-party footprints
   are not guaranteed correct.
3. As a last resort, write a `.kicad_sym` / `.kicad_mod` by hand from the
   datasheet's package drawing, and tell the user it needs verification.
