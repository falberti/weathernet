# WeatherNet Hardware

3D-printable parts for probe enclosures and sensor mounts. Starting
with the probe enclosure; an anemometer, rain gauge, and wind vane are
expected to follow (see `PROJECT_SPEC.md` Section 12, which already
lists these as sensors a future probe should support).

## Layout

One subdirectory per physical part or assembly:

```
3d-printing/
├── stevenson_screen/
│   ├── README.md                     -- print settings, hardware (screws/heat-inserts), assembly notes
│   ├── SPS30_StevensonScreen.FCStd    -- working file (parametric CAD -- see "File formats" below)
│   ├── louver.step, louver.stl, louver.3mf                 (×5 at print time)
│   ├── plate_partition.step, plate_partition.stl, plate_partition.3mf
│   ├── pm_body.step, pm_body.stl, pm_body.3mf
│   ├── pm_lid.step, pm_lid.stl, pm_lid.3mf
│   ├── sensor_carrier.step, sensor_carrier.stl, sensor_carrier.3mf
│   └── mast_bracket.step, mast_bracket.stl, mast_bracket.3mf
└── anemometer/             (future)
```

Each part's own `README.md` should cover whatever a printer actually
needs to know: material, layer height/infill if it matters for fit or
strength, non-printed hardware required (screws, heat-set inserts,
bearings, ...), and any assembly notes.

## File formats

Two files per printable part, not one -- but which two depends on how
the part was actually made:

- **Modeled/edited in a parametric CAD tool** (FreeCAD, Fusion 360,
  ... -- see `stevenson_screen/`): keep the tool's native project file (e.g.
  `.FCStd`, `.f3d`) as the actual working file -- full parametric
  history, sketches, everything editable. Also export a **`.step`**
  (or `.stp`) alongside it, the closest thing to a universal,
  tool-independent CAD interchange format, so anyone (including
  future-you on a different machine, or someone without that specific
  tool) can still open and modify the geometry -- STEP round-trips
  geometry reliably, but not always every parametric feature/history
  the native format preserves, which is why both are kept, not just
  one. Then export a **`.stl`** for the slicer.
- **Modeled/edited directly as a mesh** (Blender, Meshmixer, or a
  slicer's own mesh tools like Bambu Studio's cut/scale/hole tools):
  there is no parametric history to preserve, so the tool's own
  **project file** (e.g. Bambu Studio's `.3mf`, PrusaSlicer's `.3mf`)
  *is* the most-editable form the design exists in -- keep that as the
  working file instead of a `.step` that wouldn't exist anyway. Still
  export a plain **`.stl`** alongside it for slicers/services that
  don't read the project format.

Either way, export STL as **binary**, not ASCII, when the tool offers
a choice -- same compatibility, a fraction of the file size. And
either way, never commit *only* the `.stl` for a part -- that's the
equivalent of shipping a compiled binary with no source, whether the
"source" is a `.step` or a mesh-editor project file.

## Licensing

**The rest of this repository is MIT-licensed; parts under `3d-printing/`
aren't necessarily.** Every part here so far *is* MIT (see each part's
own README for why, where it matters). But that won't always be true: 3D-printable
designs can easily start as an actual modification of someone else's
Creative-Commons-licensed model instead of a fresh build, in which case
that part's directory would carry the original's license forward, not
MIT.

Before reusing anything from a subdirectory here, check that
subdirectory's own `LICENSE`/`README.md` if one exists -- don't assume
the top-level `LICENSE` (MIT) covers it just because most parts happen
to be MIT today. And before *adding* a new part that's an actual
modification of someone else's design (as opposed to just inspired
by it): check what license they published it under, and if it requires
the derivative to carry the same license (most Creative Commons
"ShareAlike" variants do, as does the GPL family, if a design ever
comes from that world) or forbids commercial use, add a `LICENSE` file
to that part's own subdirectory documenting it, rather than assuming
MIT applies by default.
