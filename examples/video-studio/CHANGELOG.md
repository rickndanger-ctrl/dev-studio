# Changelog

## 0.3.0

### Added

- `stillmove interp FRAMES_DIR --factor 2|4 -o OUTDIR`: in-between frames for a folder of frames.
  Every original frame is kept, and N frames become (N - 1) x factor + 1.
- `stillmove render --smooth N` (2 or 4) renders at fps / N and interpolates up, so the clip has the same
  duration and smoother motion. `--slowmo N` renders at the full fps and interpolates, so the clip plays
  N times longer. Both also work with `--sizes`. `--interp-backend linear|rife` picks the method.
- The `rife` interpolation backend: RIFE's midpoint model run with onnxruntime. 2x uses the midpoint, and
  4x makes the quarter points from it. The model file, its source and checksum are in `MODELS.md`; without
  it, `--interp-backend rife` says so, and `linear` (a plain blend) needs no model.
- Make a clip has a "Smooth motion" choice (Off, 2x, 4x), also with "Every size". The page's lengths always
  fit; any other length is refused with a plain message when seconds times 24 fps is not a multiple of it.
- Checked on real pictures: 73 frames at 1080x1080 on an Apple-silicon Mac mini (CPU). RIFE 2x gave 145 frames
  in 284 s (about 3.9 s per model call, 1.9 GB peak); RIFE 4x gave 289 frames in 979 s (3 calls per pair,
  about 4.3 GB footprint); the linear blend at 2x took 44 s. The highest-motion in-between showed no ghosting.

## 0.2.2

### Added

- Make a clip has an "Every size" choice: one render makes the square, wide and vertical versions,
  each with its own preview and download, plus a zip of all three.
- `stillmove render --sizes square,wide,vertical` renders one move in all three shapes into one folder each,
  plus `sizes.zip`. The crop is centred on the subject (the matte's outline with `--layers`), and a
  camera file (`--cam`) is adjusted to each shape with the same vertical field of view.

## 0.2.1

### Added

- The subject is kept in frame on every render path (stillmove render, stillmove mesh, shotplan render);
  --no-fit turns it off.
- `--aspect crop|pad` on stillmove render, shotplan render and Make a clip: a picture whose shape
  differs from the frame is cropped to fill (default) or padded with its edge colour, never stretched.
  A warning says so, and the manifest records it.
- The README has a "Messages you may see" section.

### Fixed

- Models are checked against their pinned SHA256 before every use, not only by `doctor`.
- The stillmove and shot-plan pages keep only the newest 20 jobs.
- Make a clip says which Python renders and warns when it lacks numpy or Pillow. Without ffmpeg it
  makes a GIF and says "install ffmpeg to get an MP4". Clip folders carry one time stamp, and a name
  clash adds -2, -3.
- `stillmove render --mp4` without ffmpeg prints its note to stderr.
- A camera file with fps <= 0 is refused before anything is written. `--cam` help no longer claims re10k.
- `shotplan check` reports a crossfade longer than its shot once, without a second, false overlap error.
- The docs say where to run `pip install ../camera-motion`, and every example writes outside the project.
- Test fixtures use generic names (`logo.png`, "Sample Shop") instead of a real brand, and the privacy
  test now rejects that brand name in any spelling.

## 0.2.0

### Added

- `stillmove matte`: writes a foreground mask (8-bit PNG, white is the subject). The `isnet`
  backend runs IS-Net with onnxruntime (Apache-2.0 weights, pinned in `MODELS.md`, never
  downloaded by the code). The `fake` backend is a soft centred ellipse that needs no model.
- `stillmove render --layers`, with `--matte fake|isnet` and `--fg-depth D`: the foreground is cut
  out by the matte and moves over a clean background plate.
- Mesh layers: no face bridges a depth cut (faces whose corners differ in depth by more than 8 % are
  dropped), and a backdrop object with its own texture sits behind each cut.
- Mesh padding: the mesh is padded to cover the frustum of every camera pose and of the orbit, over
  the depth range of the scene. The coverage is tested over the whole depth slab.
- `stillmove mesh --matte fake|isnet`: the foreground becomes one flat textured quad, RGBA from the
  matte, in front of a clean backdrop plane fitted to the background depth.
- `stillmove mesh --blender-preview`: writes `preview_render.py` and, if Blender is found, renders
  `blender-preview.mp4`. The orbit poses are computed in Python and embedded in the script. Each mesh
  object gets its own picture (`still` and `backdrop`). A key light sits high and frontal, a fill and a
  faint rim light sit beside it, and the world is darker than the paper, so the edges read as a vignette.
- Make a clip (`makeclip`, `python3 -m makeclip.server`): a page on 127.0.0.1 for one clip from one
  picture, and a Make a promo tab for two to six pictures with cut or crossfade transitions and plan
  files. Pictures come from `~/Pictures/stillmove-input/`, outputs go to `~/Pictures/stillmove-output/`.
- `shotplan`: a shot may set `"layers": true`, rendered through the matte and layers; `shotplan render
  --matte isnet|fake` picks the matte.

### Changed

- The da2-small depth spread follows the picture's contrast. A flat picture stays near depth 5
  instead of being stretched to 3..8.
- The mesh grid follows the image aspect: `--grid auto` uses 256 vertices on the long side.
- The `stillmove mesh` depth goes through the same smoothing as `render`, and `--depth` takes every
  backend render takes (plane, gradient, da2-small). `fake` stays an alias for plane.

## 0.1.2

### Added

- stillmove mesh: a textured grid of the still at its depth (`mesh.obj`, `mesh.mtl`, the texture)
  and `scene.py`, a Blender script that keys the camera along the same path.

### Changed

- Mesh depth is smoothed like render, and `--depth` accepts every backend render takes
  (plane, gradient, da2-small). `fake` stays an alias for plane.

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
