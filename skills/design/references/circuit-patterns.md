# Circuit patterns and values

Proven starting points. **The IC's datasheet overrides anything here.** When a
datasheet gives a reference schematic, copy it.

## Power entry

**USB-C as a 5 V sink (no PD)**
- CC1 and CC2 each get their **own** 5.1 kΩ to GND. Never share one resistor
  between both CC pins: the host then won't supply 5 V.
- VBUS: 10 µF bulk near the connector. Add a PTC fuse (e.g. 500 mA–1.5 A hold) or
  load switch if downstream faults are possible.
- Connect SHIELD to GND directly (simple boards), or through 1 MΩ ∥ 4.7 nF (EMC-sensitive boards).
- With USB data: ESD array (USBLC6-2SC6 or similar) right next to the connector.
  D+ and D− from both rows (A6/B6, A7/B7) are tied together. Optional series
  resistors (22–27 Ω) only if the MCU datasheet calls for them.
- Use `Connector:USB_C_Receptacle_USB2.0_16P` + `Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12` (JLC C165948) for data, or the 6P power-only variant.

**Barrel jack / external DC**: reverse-polarity protection, either a series
Schottky (simple; drops ~0.3–0.5 V) or a P-MOSFET ideal diode (gate to GND via
10–100 kΩ, 12 V zener gate clamp above 12 V input). Add a TVS (e.g. SMAJ series,
standoff just above the max input) on inputs from cables.

**Battery (1S Li-ion/LiPo)**: charger IC (TP4056 for simple builds, MCP73831 for
small ones; set the charge current with the PROG resistor per the datasheet),
plus a protection IC (DW01A + FS8205A dual FET) unless the cell is already
protected. Size the regulator for the 3.0–4.2 V range: an LDO with low dropout,
or a buck-boost for full-capacity 3.3 V.

## Regulators

**LDO** (e.g. AMS1117-3.3, AP2112K-3.3, XC6206, MCP1700, TLV75533)
- Input and output caps per the datasheet. AMS1117 needs ≥10 µF on the input
  and ~22 µF tantalum/ceramic (check stability ESR) on the output; AP2112K is
  fine with 1 µF ceramic on each side.
- Dissipation P = (Vin − Vout) × I. SOT-23 handles ~0.3–0.4 W, SOT-223 ~1 W with
  copper. 5 V→3.3 V at 500 mA = 0.85 W: use SOT-223, or a buck converter.
- AMS1117 dropout is ~1.1 V: it can't make 3.3 V from a 3.7 V battery. Use a
  low-dropout part (AP2112, TLV755, ME6211).

**Buck converter**: follow the datasheet's layout exactly (input cap tight
across VIN–GND, short switch node, inductor next to the SW pin, feedback divider
away from the switch node). The autorouter does not know these rules. Place all
buck parts with `near` hints, and tell the user this section needs manual
layout review. Feedback divider: R_top = R_bottom × (Vout/Vref − 1); use
10–100 kΩ values.

## Decoupling

- Every IC power pin gets 100 nF (0402/0603) within ~1–2 mm, via `place.near: "Ux.PIN"`.
- Plus bulk per rail: 10 µF near each regulator output and each big load (MCU,
  radio module, LED driver).
- Radio modules (ESP32, nRF, RP2040 with flash): 10 µF + 100 nF at the module's
  3V3 pin; ESP32 modules need ≥22 µF total on 3V3 because of TX current bursts (≈350 mA peaks).
- Op-amps and ADCs: 100 nF per supply pin. Analog rails can use a ferrite bead + 10 µF.

## Microcontrollers

**ESP32-C3/S3 modules (WROOM/MINI)**
- EN: 10 kΩ pull-up to 3V3 + 1 µF to GND (power-on reset delay). Reset button EN→GND.
- Boot strap: C3 = IO9 (low at reset = download mode), S3 = IO0. 10 kΩ pull-up + BOOT button to GND.
- Native USB: C3/S3 use IO18 = D−, IO19 = D+ (C3). Check the module datasheet
  for your part. No external USB-UART is needed.
- Keep the antenna end at a board edge, with no copper, parts or traces under or
  around the antenna area. The module footprint's keep-out zone enforces this
  for pours. Place the module with `edge` so the antenna faces out.

**RP2040**: needs external QSPI flash (W25Q16/W25Q128), a 12 MHz crystal (with
load caps; see below), 1 kΩ series on the crystal output per the hardware
design guide, and 27 Ω series on USB D+/D−. 1.1 V core rail from the internal
regulator with 1 µF on VREG_VOUT. Follow the "Hardware design with RP2040"
minimal design.

**STM32**: 100 nF per VDD pin + 4.7 µF bulk. VDDA via ferrite bead + 1 µF + 10 nF.
BOOT0 via 10 kΩ to GND. NRST: 100 nF to GND + button. SWD header (SWDIO, SWCLK,
NRST, 3V3, GND).

**AVR (ATtiny/ATmega)**: 100 nF on VCC, 10 kΩ RESET pull-up, 6-pin ISP header
(`Connector:AVR-ISP-6`) for programming.

**Crystals**: C_load each = 2 × (CL − C_stray), with C_stray ≈ 3–5 pF. For CL = 12 pF,
use 15–18 pF caps. Keep the crystal and caps within a few mm of the MCU pins,
using `near` hints.

## Digital interfaces

- **I²C**: one pull-up pair per bus (not per device) to the bus voltage. 4.7 kΩ
  at 100 kHz, 2.2 kΩ at 400 kHz or long/heavily loaded buses. Check each device's address.
- **SPI**: optional 33 Ω series resistors on SCK and MOSI for long traces. Pull CS lines up.
- **UART**: cross TX↔RX. Add 1 kΩ series resistors if either side can be unpowered.
- **Open-drain / interrupt lines**: pull-ups (10 kΩ) to the right rail.
- **Level shifting**: 3.3 V MCU to 5 V logic: a BSS138 + 2× 10 kΩ per bidirectional
  line (I²C), or a 74LVC/74AHCT buffer for push-pull.

## Outputs and loads

- **LED**: R = (Vsupply − Vf) / I. Red/yellow/green Vf ≈ 2.0 V, blue/white ≈ 3.0 V;
  2–5 mA is plenty for indicators (e.g. 3.3 V green at 2 mA → 680 Ω–1 kΩ).
- **WS2812B / SK6812**: 100 nF per LED at VDD. 300–470 Ω series on the first DIN.
  At 5 V from a 3.3 V MCU, level-shift DIN (74AHCT1G125) or run the LEDs at ~3.7–4 V.
  Budget ~60 mA per LED at full white.
- **Low-side N-MOSFET switch** (motors, solenoids, LED strips): logic-level FET
  (AO3400 small loads, e.g. IRLZ44N / SI2302 per current), 100 Ω gate
  resistor, 100 kΩ gate pull-down, and a flyback diode (1N5819/SS34) across
  inductive loads.
- **Relays**: NPN or N-FET driver + flyback diode. 5 V relay coils draw 70–90 mA.
- **Motor drivers**: follow the driver datasheet (e.g. DRV8833 needs bulk
  capacitance at VM and caps on VINT/VCP). Wide tracks for motor current: add a
  `net_class` with a 1.0 mm+ track.

## Inputs and analog

- **Buttons**: to GND with the MCU's internal pull-up, or an external 10 kΩ pull-up.
  An optional 100 nF gives hardware debounce.
- **Voltage divider to ADC**: keep the source impedance within the ADC spec
  (often <10 kΩ, or add 100 nF at the ADC pin). Battery sense: 100 k/100 k plus a
  P-FET switch or high values to limit drain.
- **Op-amp**: check the input/output range for the supply (LM358 output doesn't
  reach the top rail; use a rail-to-rail part like MCP6002 at 3.3 V). Voltage
  follower: output → inverting input. Tie unused units as followers with the
  + input to mid-rail or ground (per datasheet); don't leave inputs floating.
- **Thermistor NTC**: divider with a fixed resistor equal to R25 (10 kΩ for a 10 k NTC).

## Protection and robustness

- ESD/TVS on every external connector pin that leaves the enclosure.
- Series resistors (100–1 kΩ) on MCU pins wired straight to connectors.
- Test points (`Connector:TestPoint`) on rails, GND and key signals help bring-up.
- Mounting holes: tie to GND or leave isolated (the default `MountingHole` footprints are isolated).
- A power LED on each rail is cheap bring-up insurance.

## Connectors (stock KiCad libraries)

- Pin headers: `Connector_Generic:Conn_01xNN` + `Connector_PinHeader_2.54mm:PinHeader_1xNN_P2.54mm_Vertical`.
- JST PH / XH / SH: `Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical` etc. (`kipcb fp-search JST PH 2`).
- Qwiic/STEMMA QT (I²C): JST SH 4-pin, pin order GND, 3V3, SDA, SCL.
- Screw terminals: `TerminalBlock_Phoenix:*` or `TerminalBlock:*`.
