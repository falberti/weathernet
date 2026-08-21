# Probe enclosure

Weatherproof(ish) enclosure for the probe's BME680, sized for the
[Seengreat BME68x Environmental Sensor (I2C/SPI)](https://seengreat.com/product/304/bme68x-environmental-sensor-for-temperature-humidity-pressure-and-gas)
breakout (30mm x 20mm) specifically. BME680 breakout boards come in
several different physical shapes/sizes depending on vendor (Adafruit,
DFRobot, and generic ones all differ) -- this enclosure fits *this
specific board*, not "a BME680" in general. Check the fit before
assuming a different board will work.

<!-- Fill in once actually printed:
- Printer/material actually used for this build
- Layer height / infill, if either turned out to matter for fit or strength
- Non-printed hardware needed (screws, heat-set inserts, gasket, ...)
- Assembly notes
-->

## Credits

Inspired by [allmysparetime's Sensirion SPS30 / Adafruit BME280
Weatherproof Outdoor Enclosure](https://www.printables.com/model/530737-sensirion-sps30-adafruit-bme280-weatherproof-outdo)
(CC BY-NC-SA 4.0) -- that design was printed first and didn't fit this
BME680 board, so this one was modeled from scratch in FreeCAD instead
of modifying the original's files. No geometry from the original was
imported or reused, so this isn't a derivative work under its license
(only the specific expression of a design is copyrightable, not the
general idea of "a two-part weatherproof sensor case" or dimensions a
physical component dictates) -- this directory is MIT-licensed like
the rest of the repository, same as everywhere else under `hardware/`
that isn't otherwise marked. Credited here anyway as the actual
starting inspiration, which is simply the honest thing to do.

## Files

- `case.FCStd` -- the FreeCAD project (sketches, features, full
  parametric history). This is the working file -- open this one, not
  the STEP or STL, to keep editing.
- `case.step` -- exported from the FCStd, for anyone who wants to open
  or modify the geometry without FreeCAD specifically. STEP round-trips
  geometry reliably; it doesn't always preserve every parametric
  feature/history the way the native FCStd does, which is why both are
  kept.
- `case.stl` -- exported for the slicer. Regenerate it from the FCStd
  whenever the design changes -- it's a derived export, not a separate
  edit target.

See `hardware/README.md` for the general file-format convention this
follows.
