# Layout guidelines for kipcb boards

## How placement works

`kipcb build` places parts in this order:
1. Parts with fixed `x/y`.
2. Parts with `edge` hints, biggest first. They sit flush to the edge with the mating side facing out.
3. Everything else, in connectivity order. Each part goes where its pads are
   closest to already-placed pads on the same nets; `near` adds a strong pull.
   Large nets (GND, rails) count less.
4. Two refinement passes re-place each part knowing where all the others went.

Placement keeps courtyards 0.3 mm apart, keeps parts `edge_clearance` from the
outline, and keeps them clear of mounting holes. Parts that don't fit are
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
