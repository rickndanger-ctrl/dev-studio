# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.3

### Fixed

- The unchanged-presets test compares values within 1e-9 instead of byte-exact hashes, so it passes on Linux.

## 0.1.2

### Fixed

- `camera-motion serve` no longer shares a port with another program: a busy port prints
  `error: port N is in use; try --port N+1` and exits 1. The tutorial test starts the server on a
  free port, which fixes the macOS CI failure.
- The web viewer checks slider values against the preset schema and returns 400 with the reason
  (for example, a distance of 0, or an angle on a dolly). Unexpected server errors return a 500
  JSON error instead of an empty reply.
- `export` rejects a zero or negative `--fps`, `--seconds`, `--width` or `--height`.
- Bad input files print one `error:` line and exit 1 instead of a traceback; `--debug` shows the
  traceback.
- Malformed glTF files give a `ValueError` naming the field and index.
- `import` and `match` reject trajectories whose timestamps are not evenly spaced.
- Every command reads catalogs with one `load_catalog()`, so both catalog forms (a list, or
  `{"presets": [...]}`) work everywhere.
- `match` prints a `preset  score (lower = closer)` header, and nothing at all when it fails.
- `python3 -m camera_motion.export.trajectory` runs through the main `export` command.
- The CLI usage text lists every subcommand, checked by a test.
- The release check no longer scans dot-directories or virtual environments for machine paths.
- README and tutorial use one virtual environment location, `~/venvs/camera-motion`, outside the
  project. The README Tests section says to run from the folder that contains `pyproject.toml`.
- Troubleshooting covers `port N is in use` and the page title, 'Camera motion'.
- A missing `help.json` now raises a clear `FileNotFoundError` instead of returning no help.
- `camera-motion doctor` checks that help is present (9 checks) and reads the catalog with
  `load_catalog`.
- Help text numbers (degrees, units, an eighth of a circle) are checked against each preset's
  parameters.

## 0.1.1

### Fixed

- Dolly zoom keeps the subject's on-screen size constant: the field of view follows
  the distance.
- The catalog dolly zoom presets no longer set `fov_end`.
- `fov_end` is deprecated and ignored.
- New optional `subject_distance` parameter for dolly zoom.

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
