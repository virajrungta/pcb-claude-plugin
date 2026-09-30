# Layout guidelines for kipcb boards

## How placement works

`kipcb build` places parts in this order:
1. Parts with fixed `x/y`.
2. Parts with `edge` hints, biggest first. They sit flush to the edge with the mating side facing out.
3. Everything else, in connectivity order. Each part goes where its pads are
   closest to already-placed pads on the same nets; `near` adds a strong pull.
   Large nets (GND, rails) count less.
4. Two refinement passes re-place each part knowing where all the others went.

Placement keeps parts apart according to `board.spacing` (normal: 1.0 mm
between passives, ~1.6 mm around ICs). It uses each footprint's real courtyard
shape (e.g. a module's wide antenna section doesn't block the pins beside it),
keeps parts `edge_clearance` from the outline, and keeps them clear of mounting holes. Parts that don't fit are
parked beside the board and reported: enlarge the board, or pin them.

## Good placement, and the hints that produce it

| goal | hint |
|---|---|
| Connectors on edges, facing out | `{"edge": "left"}` (+ `"at"` to choose the spot) |
| Decoupling cap at its pin | `{"near": "U1.VDD"}` (by pin name or number) |
| Crystal + load caps at the MCU | crystal `near` the XIN pin, caps `near` the crystal |
| ESD array next to the USB connector | `{"near": "J1"}` |
| CC resistors next to the USB-C | `{"near": "J1.A5"}` / `{"near": "J1.B5"}` |
| Regulator near power entry, caps around it | regulator `near` the connector; caps `near` its VIN / VOUT pins |
| Antenna module at an edge | `{"edge": "top"}` on the module; keep other parts away from the antenna end |
| Buttons/LEDs reachable/visible | `edge` or fixed positions |
| Things that must line up with an enclosure | fixed `x/y/rot` |

Workflow: build → Read `out/pcb_3d_top.png` → adjust hints → rebuild. Copy
good positions from `out/placement.json` into `place` to freeze them while
you move others.

Board size: start with the auto size or a generous guess, then shrink once
placement looks right. Very tight boards fail to route; ~40–60% component
coverage routes comfortably on 2 layers.

## Routing (Freerouting)

- `kipcb route` routes all nets, including GND at signal width, then pours GND
  on both layers and adds stitching vias. The pour carries the ground current.
- Power nets use `power_track` (0.5 mm default ≈ 1 A+ on 1 oz outer copper).
  If wide tracks can't reach fine-pitch pads, the router retries automatically at signal width.
- Track width vs current (1 oz, 10 °C rise, outer layer): 0.25 mm ≈ 0.6 A,
  0.5 mm ≈ 1.2 A, 1.0 mm ≈ 2 A, 2.0 mm ≈ 3.5 A. Add a `net_class` for heavy nets.
- If routing leaves unconnected nets: spread congested areas, rotate ICs so
  pins face their partners, reduce `via_diameter`/`track` toward fab limits, or go to 4 layers.

**Use 4 layers** for: fine-pitch QFN/BGA with many signals, more than about 60
routed nets on a small board, RF or high-speed signals that need a solid
reference plane, or boards where the 2-layer route looks like spaghetti.

## Noise: what `kipcb noise` checks

| check | pass criteria | typical fix |
|---|---|---|
| decoupling | each IC supply pin has a cap to ground ≤ 2.5 mm pad-to-pad (fail > 5 mm or none) | `near` on the exact pin; one small cap per pin; bulk cap `near` the small one |
| ground plane | pours ≥ 80% of board on bottom, not split into islands | more room, fewer bottom routes, 4 layers |
| bottom-layer routing | < 15 mm of signal on the bottom of 2-layer boards | spread parts, rotate ICs so pins face partners |
| stitching | ≥ 0.4 ground vias per cm² | automatic; larger boards may need `kipcb route` again after changes |
| noisy nets (clocks, SW nodes, PWM, USB) | short (< 60 mm), ≥ 1 mm from the board edge, ≤ 2 vias | place source and load together, keep off the edges |
| crosstalk | sensitive nets (ADC, SENSE, FB, op-amp inputs) not run > 2 mm within 0.5 mm of noisy nets | `away_from`, separate groups, ground between |
| crystal | crystal ≤ 6 mm from the MCU, load caps ≤ 4 mm, no other signals routed under it | `near` hints, keep the crystal area clear |
| switcher loop | inductor ≤ 4 mm from the SW/LX pin | `near` on the SW pin |
| diff pairs (USB D+/D−, *_P/*_N) | length mismatch ≤ 2 mm, equal via count | keep the connector and MCU close and aligned |
| power width | supply tracks ≥ 0.4 mm | more room around fine-pitch pins, a `net_class`, or widen by hand |

Net roles are guessed from net and pin names. Override them with `net_roles` in the spec.

## What the autorouter doesn't handle (flag these to the user)

- **Switching regulators**: loop area, switch-node size and feedback routing
  matter. Place tightly with `near` and ask for manual review or hand routing.
- **Differential pairs** (USB, Ethernet, LVDS): Freerouting doesn't
  length-match or impedance-control. USB 2.0 full speed (12 Mb/s) is forgiving
  when kept short and parallel. High-speed USB 480 Mb/s and faster needs manual routing.
- **RF / antennas**: keep-out areas and 50 Ω feed lines need manual work;
  modules with integrated antennas are the easy path.
- **High current / thermal**: check copper widths and thermal relief on
  power parts. Add copper pours or wider net classes.
- **Analog precision**: route away from switching nodes and digital clocks.

## Silkscreen

DRC silk warnings (overlap, clipped by mask) are cosmetic. Clean them up in
KiCad for a tidy board if the user cares; they don't affect function.
