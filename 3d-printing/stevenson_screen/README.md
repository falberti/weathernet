# Stevenson-screen-style radiation shield for SPS30 + BMP280 + HTU21D-F

Overall envelope **197 × 145 × 154 mm** (bracket included). Shield Ø 145.5 mm, height 154 mm.
Material **ASA**. Estimated mass ~233 g at 35% infill.

## Why it's built this way

Two stacked chambers separated by a solid partition:

- **PM chamber (top)** — the SPS30 sits vertically with its ports facing radially
  outward and **rotated so the two inlets sit above the outlet**: this is the
  "side mounting" called for by Sensirion's own mechanical guidelines, which
  prevents already-measured particles from falling back into the inlets under gravity.
  The sensor is up top on purpose: it dissipates ~0.3 W and Sensirion recommends
  keeping it away from or below heat sources — here the sensor itself is the heat
  source, so convection carries its heat up and out, away from the climate sensors.
- **Solid partition** — breaks the chimney effect between the two chambers.
- **T/RH chamber (bottom)** — 5 louver plates with a 60° skirt, open bottom,
  BMP280 and HTU21D-F on a panel suspended in the middle, ventilated on both faces.

The port-facing wall is **flush with the enclosure's opening**, with an EPDM
gasket around it: the openings are large and in direct contact with ambient air
(Sensirion's recommendation), and neither the inlet nor the outlet communicates
with the enclosure's interior.
Rain protection: a 45° drip edge, two vertical cheeks, and above all the
Ø 130 mm roof, which overhangs the wall by 22 mm with a skirt that comes down
to 1 mm above the opening's top edge.

## Printed parts list

| STL file | Qty | Envelope | Print orientation |
|---|---|---|---|
| `louver` | 5 | 146 × 146 × 16 | **upside down**, flat face on the bed |
| `plate_partition` | 1 | 146 × 146 × 16 | **upside down**, flat face on the bed |
| `pm_body` | 1 | 114 × 109 × 58 | upright, base on the bed |
| `pm_lid` | 1 | 146 × 146 × 14 | **upside down**, roof on the bed |
| `sensor_carrier` | 1 | 60 × 17 × 64 | on its side, panel on the bed |
| `mast_bracket` | 1 | 68 × 84 × 45 | upright, z=0 face on the bed |

In these orientations, unsupported overhangs are ≤ 4% of the area and are only
short bridges (opening's ceiling, louver slots, slots): **no supports needed**.
Requires a bed of at least 150 × 150 mm.

## Hardware

- 3 × **M4 stainless threaded rod, 175 mm** + 6 M4 nyloc nuts + 6 wide washers
- 2 × **M4 heat-set inserts** (Ø 5.6 × 8 mm hole) + 2 M4×16 screws — bracket mounting
- 2 × **M3×16** self-tapping screws — sensor carrier (from above, through the partition)
- 4 × 2.5 mm cable ties — securing the two breakout boards
- adhesive **2 mm EPDM gasket** around the port opening (compresses to ~1 mm)
- **3-4 mm foam** strip behind the SPS30 (preload against the gasket)
- 1 × Ø 13 mm rubber cable gland
- 2 × M6/M8 U-bolts or metal hose clamps for the mast (22 × 9 mm slots)

## Assembly

1. Thread the 3 rods through; stack from the bottom: nut + washer, 5 `louver`
   plates, `plate_partition`. The built-in standoffs alone set the 15.6 mm pitch.
2. Mount the BMP280 and HTU21D-F on the `sensor_carrier`: they rest on the 4×
   3 mm standoffs (air on both sides) and are held down with one cable tie per board.
   The STEMMA QT connectors stay free on the short sides.
3. Screw the `sensor_carrier` under the partition with the 2 M3 screws from above.
4. Glue the EPDM gasket to the inside face of the `pm_body` wall, around the
   opening. Lower the SPS30 into its cradle (ports facing the opening,
   **inlets on top**), slide the foam in behind it, and route the cable out
   through the cable gland in the partition.
5. Close with `pm_lid`: the internal rib presses on the sensor's top edge
   (add 1 mm of foam there). Nyloc nuts on top, don't overtighten.
6. Heat-set inserts into the rear boss, screw on the `mast_bracket`, mount to the pole.

Orient the port opening **away from the mast and from prevailing rain-bearing winds**.

## Printing (ASA)

Nozzle 250-260 °C, bed 100-110 °C, **enclosed chamber** and no drafts.
0.4 mm nozzle, 0.2 mm layers, 4 perimeters, 5 top/bottom layers, 30-40% infill
(gyroid). Fan 0-20%. ASA is the right choice here: it resists UV, which PLA
and PETG don't outdoors. If a part lifts off the bed, use a 5 mm brim on the plates.

## Main parameters (top of the `.FCMacro` file)

`D_PLATE` plate diameter · `GAP` clearance between plates · `N_LOUVER` number of plates
`SKIRT_L` / `SKIRT_A` skirt geometry · `H_PM` PM chamber height
`BMP_X/Z`, `HTU_X/Z` breakout board envelope · `MAST_Z` bracket mounting spacing

Edit and re-run: everything regenerates, assembly and exports included.

## Already verified

- 6 topologically valid solids (`BRepCheck_Analyzer`)
- 0 interferences between assembly parts (pairwise boolean checks)
- all three sensors have collision-free clearance
- unsupported overhangs ≤ 4% for every part in the orientation shown

## Known limitations

- The roof is **flat** (so it can print upside down without supports). It sheds
  water fine thanks to the skirt, but if you want a dome, print it upright with a brim.
- The ASA mast bracket is the weakest point of the assembly: if the mast is
  exposed to strong wind, consider remaking it in aluminum using the STEP file
  as a reference.
- The SPS30's ZHR-5 connector vertical clearance isn't specified in the
  datasheet: the rear standoffs were kept low (z 0-17 mm) to leave it clear,
  but verify on the first dry-fit assembly.
- The Adafruit breakout boards are held by standoffs + a cable tie rather than
  screws through the corner holes, because Adafruit doesn't publish the hole
  spacing: this way the mount works regardless of board tolerance.
