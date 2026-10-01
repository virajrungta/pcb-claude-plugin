# What the pipeline can and can't do

Tell the user this after the requirements questions and before designing, so
expectations are set before any work starts. `kipcb check` also lists the
support level of every block in the spec.

## Automatic

Designed, placed, routed and checked with no manual work; typically ready to
order in one to three runs:

- Boards built from **verified blocks** (`kipcb blocks`; any block not marked
  advanced): USB-C power/data, USB-UART, linear regulators and the AP63203
  buck, LiPo chargers, input/reverse protection, ESP32/ESP32-C3/ESP32-S3
  modules, ATmega328P/ATtiny85, I2C/SPI sensors, microSD, RS-485, CAN,
  motor drivers, relays, LEDs, buttons, headers and connectors.
- Through-hole and SMD parts down to 0402 and 0.5 mm-pitch packages
  (SOIC, TSSOP, SOT, QFN/LQFP up to ~48 pins with few signals).
- 2-layer boards up to roughly 10 connections per cm² (most hobby and
  product boards); 4-layer boards for denser or noise-sensitive designs.
- Ground pours, stitching, noise checks, Gerbers, BOM and pick-and-place
  files for JLCPCB, PCBWay, OSH Park or any other manufacturer.

## Advanced: works, but may need help

The schematic, parts and placement are produced as usual, but autorouting
may leave a few connections near the chip. Say so up front and offer the
choices: 4 layers, a few minutes of hand routing in KiCad, or an
easier alternative.

- **0.4 mm-pitch chips with many signals**: the RP2040 (`rp2040_minimal`),
  and similar QFN-48+ microcontrollers. Easier alternative with the same job:
  an ESP32-S3 or ESP32-C3 module (Wi-Fi included), or an RP2040 module.
- **STM32 in LQFP-48** (`stm32f103c8_core`): usually routes on 2 layers with
  room around the chip; dense boards may need 4 layers.
- Very dense boards (preflight's density check warns): enlarge the board or
  use 4 layers.

## Not supported (needs a human PCB designer or a module)

- BGA packages, 0201 parts, HDI/blind vias, flex or rigid-flex boards.
- Impedance-controlled high-speed links: DDR memory, HDMI, PCIe, Ethernet
  PHY layout, MIPI, USB 3. USB 2.0 full/high speed is fine.
- Custom RF: antennas, matching networks, RF amplifiers. Use pre-certified
  modules (ESP32, nRF, LoRa modules) instead.
- Mains voltage (110/230 V AC) and high-voltage or high-current power
  stages: the pipeline can draw them but doesn't verify creepage, isolation
  or safety; a qualified person must review.
- More than 4 layers.

## How to tell the user

Keep it to two or three lines after the requirements are known:

> Everything here is automatic except the RP2040: its 0.4 mm pins may leave a
> few connections for 4 layers or a quick hand-route in KiCad. Want to keep it,
> or use an ESP32-S3 module, which routes automatically?

If everything is automatic, one line is enough ("All of this is standard;
expect a ready-to-order board in about N minutes"). Never promise a
not-supported item; offer the module or the human-review route instead.
