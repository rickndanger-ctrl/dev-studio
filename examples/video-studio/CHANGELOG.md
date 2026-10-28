# Changelog

## 0.1.1

### Fixed

- shotplan's web render runs with the server's own Python (`sys.executable`), so it works in any
  venv and on Linux. It used to run `/usr/bin/python3`, which has no numpy on Linux, GitHub runners
  or most venvs.

### Changed

- Internal study notes moved out of the package into `notes/`.

## 0.1.0

The first release of video-studio: two packages in one project, `stillmove` and `shotplan`, built
on camera-motion. Licensed under the MIT License (see `LICENSE`).

### stillmove

- Moves one still picture along a camera-motion path with a backward warp and smoothed depth. Writes
  the frames, a preview GIF, an optional mp4 (only when ffmpeg is on PATH) and a manifest.
- Depth backends: `plane` and `gradient` (fakes that need no model), and `da2-small` (optional; needs
  onnxruntime and the model in `MODELS.md`).
- Single-photo safe ranges per move (`docs/LIMITS.md`). A move past its range prints a warning;
  `--strict` makes it an error.
- `--photo-report` prints per-frame time, depth time, peak memory and the share of holes.
- A web page on 127.0.0.1: upload a picture, pick a move, see the preview.
- `doctor` checks the install with one PASS, WARN or FAIL line per check.

### shotplan

- Reads a shot plan (`shotplan/schema.json`): each shot is a still, a camera sequence and a duration,
  with an optional crossfade into the next shot.
- `check` validates the plan and prints its timeline; `timeline` writes it as an SVG.
- `export` writes one camera-path JSON per shot.
- `render` moves every shot with stillmove and joins them into one preview.
- `serve` runs a web page on 127.0.0.1 to check and preview plans.
