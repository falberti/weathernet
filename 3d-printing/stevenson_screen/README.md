# Stevenson-screen-style radiation shield for SPS30 + BMP280 + HTU21D-F

Overall envelope **197 × 145 × 154 mm** (bracket included). Shield Ø 145.5 mm, height 154 mm.
Material **ASA**. Estimated mass ~239 g at 35% infill.

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

The sensor's port face sits **flush with the outer wall**: its nose enters the
opening, which is deliberately 2.8 mm narrower than the sensor (15.6 vs 12.8 mm).
A **foam collar** wrapped around the nose fills the 1.4 mm gap per side. This stops
exhaust air from looping around the sensor inside the chamber and re-entering the
inlets; without it the leak path (64 mm2) would be wider than the outlet itself
(23 mm2). Openings stay large and in direct contact with ambient air, as Sensirion
requires.
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
| `cable_hub` | 1 | Ø83 × 2 | flat on the bed |
| `conduit_plate` | 1 | Ø110 × 7 | flat on the bed |

In these orientations, unsupported overhangs are ≤ 4% of the area and are only
short bridges (opening's ceiling, louver slots, slots): **no supports needed**.
Requires a bed of at least 150 × 150 mm.

## Hardware

All dimensions taken from the current model, not from memory.

| # | Item | Qty | Where it goes |
|---|---|---|---|
| 1 | M4 stainless threaded rod, **180 mm** | 3 | Through the 3 boss columns, bottom to top |
| 1 | M4 nyloc nut + washer | 6 | 3 under the lowest bosses, 3 on top of the roof |
| 2 | **M3 x 12** self-tapping screw | 2 | From above, through the partition at x = +/-21, into the carrier tabs |
| 3 | 2.5 mm cable tie | 4 | Two per breakout board, looped vertically through the 3.5 x 2.0 mm slots |
| 4 | Foam collar, **3 mm** thick, ~5 mm wide, ~110 mm long | 1 | Wrapped around the SPS30 nose, all four sides |
| 5 | **M6 x 25** hex-head bolt + nyloc nut + washer | 2 | Head drops into the hex pockets from *inside*; nut goes on outside |
| 6 | M8 U-bolt (or metal hose clamp) | 2 | Through the four 22 x 9 mm slots in the saddle plate |
| 7 | Insect mesh basket, **O83 x 77 mm** | 1 | Central aperture of the louver stack |
| 8 | Insect mesh strip, **13 x 285 mm** | 1 | Annular seat inside the PM module, behind the vent slots |
| 9 | Rubber cable gland, **O13 mm** (split type) | 1 | Partition hole at (0, -18) |
| 10 | Foam, 1 mm | 2 | One strip behind the SPS30, one pad under the lid rib |
| 11 | 2.5 mm cable tie | 2 | One per Cat5e, through the paired slots in `cable_hub` (strain relief) |
| 12 | **M25** conduit-to-box fitting | 1 | Ø25.4 hole in `conduit_plate`, ring nut on top |
| 13 | **Ø25** corrugated conduit | 1 | From the fitting down the mast to the Raspberry Pi |

Notes on sizing:

- **Rod length.** Clamped stack is 154.2 mm (lowest boss to roof top). Adding a washer,
  a nyloc nut and 2 mm of thread at each end gives 180 mm. Cut three from a 1 m rod.
- **Washers.** The bosses are only O11, so the bottom washers must be standard DIN 125
  (O9). Wide washers only fit on top, where they bear on the flat roof.
- **M3 length.** 2.6 mm of partition + 8 mm of engagement in the carrier tab = 10.6 mm.
  M3 x 16 would bottom out; use **M3 x 12**.
- **M6 length.** 7 mm of boss in tension + 8 mm bracket + washer + nyloc = 21.6 mm under
  the head. The pocket is 10.4 mm across flats against a 10.0 mm head, so the bolt can
  turn only +/-4.5 deg before it locks: tighten the nut only.
- **Cable gland.** Use a split/openable one. The ZHR-5 connector will not pass through a
  closed grommet, and you do not want to solder it on afterwards.

**No heat-set inserts are used anymore** — the M6 captive bolts replaced them.

## Insect mesh

Outdoors a louvered shield quickly becomes a wasp nest, so two seats are provided:

- **T/RH chamber**: every louver plate has an O83.4 x 1.6 mm rebate on its top face.
  Roll a O83 mesh basket (cylinder plus a bottom disc), drop it in from above before
  fitting the partition. It springs into the rebates and rests on the step of the
  lowest plate, so it cannot fall through. No glue.
- **PM module**: an annular seat is recessed into the inner wall across the vent band.
  A 13 mm tall strip holds itself by spring force.

Stainless or fibreglass mesh, 0.8-1 mm aperture. Do not go finer or you choke the airflow.

**Do not put mesh over the SPS30 ports** — Sensirion requires inlets and outlet to stay
unobstructed; a screen in front clogs and crops the large particles, skewing PM10 while
the sensor keeps returning plausible numbers. Leave the four 1.6 mm drain notches open
too, or they stop draining.

## Cable routing

Two Cat5e cables leave the shield downwards: one for the SPS30 on **UART**, one
shared by BMP280 and HTU21D-F on I2C. Sensirion recommends UART for any connection
cable longer than 20 cm, and warns to keep I2C runs under 10 cm, so the PM sensor
gets the robust link and only the two low-rate sensors share a bus.
Set `N_CABLE` in the macro if you want three instead.

- **SPS30** is the only cable that crosses the partition: down through the Ø13 split
  gland at (0, -18), into the T/RH chamber.
- **BMP280** and **HTU21D-F** are already below the partition and drop straight down.
- All two leave through `cable_hub`, a Ø83 disc resting in the Ø83.4 rebate of the
  lowest louver plate. `N_CABLE` Ø6.6 holes (snug on 5.0-5.5 mm Cat5e), a pair of slots
  beside each hole for a cable-tie strain relief, and three openings that keep 44% of
  the bottom aperture free for airflow.
- The mesh basket sits on top of the hub, so the mesh itself is never pierced: the hub
  is the single crossing point and the cables seal it themselves.

### Conduit

`conduit_plate` sits under the lowest louver plate and is clamped by the same three tie
rods, so the conduit is anchored to the structure and not hanging off the cables. It
carries a Ø25.4 hole for an **M25 conduit-to-box fitting**, on a 4 mm conical weir, so water
would have to pond more than 4 mm deep across the whole plate to reach it, plus three openings and three ribs.

**Size:** with two cables, Ø20 works: rigid Ø20 (16.9 mm bore) is fine up to Ø6.5 mm
cable, corrugated Ø20 (14.1 mm bore) only up to Ø5.5. Measure your cable first.

Drill a small **weep hole at the lowest point of the conduit run**: whatever gets in at either end leaves there. This, not the choice of exit point, is what keeps the Pi dry.

**The conduit must run downwards the whole way.** Do not take it up over the shield: the
shield would become the high point and every drop of rain or condensate inside the
conduit would drain into the Pi. End the conduit *below* the Raspberry Pi enclosure,
open and facing down so it drains, and bring the cables up into a gland on the bottom
of the box.

Leave slack outside for a **drip loop** — the lowest point of each cable must be clear
of the shield, so water drips there instead of tracking up into the housing. Tie the
cables to the mast below the loop, then run them to the Raspberry Pi enclosure.

The T/RH chamber is ventilated by design, so these holes do not need to be watertight;
they only need to keep insects out.

## Assembly

1. Thread the 3 rods; stack from the bottom: nut + washer, 5 `louver` plates,
   `plate_partition`. The built-in bosses set the 15.6 mm pitch on their own.
2. Drop `cable_hub` into the rebate of the lowest plate, then the mesh basket on top
   of it, before the partition goes on.
3. Mount BMP280 and HTU21D-F on the `sensor_carrier`: they sit on the four 3 mm
   standoffs (air on both faces), one cable tie per board holds them. STEMMA QT
   connectors stay clear on the short sides.
4. Screw the `sensor_carrier` under the partition, 2x M3 x 12 from above.
5. Wrap the foam collar around the SPS30 nose, flush with the port face but **not over
   the slots**. Lower the sensor into the cradle, **inlets on top**, and push the nose
   into the opening until the face is flush with the outer wall.
   Add the 1 mm foam behind it, against the rear stops.
6. Feed the cable through the split gland in the partition *before* plugging it in.
7. Press the mesh strip into the annular seat of the PM module.
8. Drop the two M6 bolts into the hex pockets from inside, while the lid is still off.
9. Close with `pm_lid` (1 mm foam pad under the internal rib). Nyloc nuts on top of the
   roof, snug only.
10. Pull the two Cat5e cables through `cable_hub`, one cable tie each as strain
    relief, and form the drip loop before running them to the Raspberry Pi.

Point the port opening **away from the mast and from prevailing rain-bearing winds**.

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
- The SPS30's ZHR-5 connector vertical clearance isn't specified in the
  datasheet: the rear standoffs were kept low (z 0-17 mm) to leave it clear,
  but verify on the first dry-fit assembly.
- The Adafruit breakout boards are held by standoffs + a cable tie rather than
  screws through the corner holes, because Adafruit doesn't publish the hole
  spacing: this way the mount works regardless of board tolerance.
