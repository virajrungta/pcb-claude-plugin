# Manufacturing notes

## Default rules (safe for JLCPCB, PCBWay, OSH Park standard 2-layer)

| rule | kipcb default | typical fab minimum |
|---|---|---|
| track width | 0.25 mm | 0.127 mm (5 mil); 0.09 mm on some 4-layer services |
| clearance | 0.2 mm | 0.127 mm |
| via drill / diameter | 0.3 / 0.6 mm | 0.2–0.3 / 0.45–0.6 mm |
| copper to board edge | 0.5 mm | 0.3 mm |
| min hole | 0.3 mm (lower if a footprint needs it) | 0.2 mm |
| board thickness | 1.6 mm | 0.4–2.0 mm |

Tightening rules is possible but reduces yield and may raise the price. Check
the current capability page of the chosen fab before going below the defaults.

## Outputs from `kipcb fab`

`<project>/fab/`:
- `<name>-gerbers.zip`: copper, mask, paste, silk, outline, drill files and
  drill map, referenced to the board's bottom-left corner. Upload this to the PCB order page.
- `<name>-bom.csv`: columns `Comment, Designator, Footprint, LCSC Part #, MPN, Qty`.
  Matches JLCPCB's assembly BOM format.
- `<name>-cpl.csv`: `Designator, Mid X, Mid Y, Layer, Rotation` (JLCPCB CPL format).
- `gerbers/`: the unzipped files, for inspection or other fabs.

## JLCPCB assembly (PCBA)

- Put the LCSC part number in each component's `lcsc` field. **Basic** parts
  (common passives, AMS1117, etc.) avoid the per-part setup fee; **extended**
  parts cost a few dollars each. Use basic parts where you reasonably can.
- Don't invent LCSC numbers. If you can't verify one, leave it blank and tell
  the user to pick it in JLCPCB's BOM matching step.
- Check rotations in JLCPCB's placement preview: some footprints (SOT-23,
  some ICs, polarized parts) differ from KiCad's zero orientation.
- Through-hole connectors and headers are often cheaper to hand-solder, or
  mark them `dnp`.

## Ordering checklist to give the user

1. Upload the Gerber zip and confirm the board outline, size and layer count in the viewer.
2. Choose surface finish (HASL lead-free is fine; ENIG for fine-pitch/QFN parts), 1.6 mm, 1 oz copper.
3. For assembly: upload the BOM and CPL, review part matches and rotations.
4. Order a few extra boards; the first revision is for bring-up.

## Bring-up advice worth passing on

Power the board from a current-limited supply first. Check each rail
before plugging in USB/programmers, then program and test one subsystem at a time.
