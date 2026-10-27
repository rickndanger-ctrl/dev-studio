# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.0

### Added

- Camera engine: dolly, dolly zoom, crane, orbit, pan and tilt moves, with the
  easing curves linear, ease in, ease out and ease in and out.
- Conventions: the engine is documented as `[yaw, pitch, roll]` in degrees, world
  Y-up. `frame: "camera"` makes dolly, dolly zoom and crane follow the start
  pose's view. The orbit `radius` is optional.
- A catalog of 14 presets, with a JSON schema and a validator.
- SVG previews, static and animated (SMIL), with a side view for crane and tilt.
- Exports: RealEstate10K text, camera JSON, a Blender script and glTF 2.0.
- Importers for RealEstate10K, camera JSON and glTF, and a match check.
- A web viewer and a JSON API, served on 127.0.0.1.
- Shot sequences: chain presets into one shot, with `frame: "camera"` and an optional
  orbit `radius`. Added the `sequence` command and a Sequences tab in the web viewer.
- `camera-motion doctor`, which checks the install.
- A tutorial that installs into a fresh virtual environment and runs every
  command it shows.
