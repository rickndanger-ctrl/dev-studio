# camera-motion engine

camera-engine is the single source of camera-motion: engine, presets (the 14-preset catalog), integration, docs, preview, web, export, CLI.

## Conventions

- Units: scene units for distances, degrees for angles. Rotation is [yaw, pitch, roll].
- Default start: position [0, 0, 5], rotation [0, 0, 0], fov 50. At yaw 0 the camera looks down -z at the subject at the origin.
- +x is the camera's right when looking down -z. +y is up.
- +yaw turns the camera left. +pitch looks up.
- dolly: on x, +distance moves the camera right. On z, +distance moves toward the subject (z decreases).
- crane: +distance moves up.
- orbit: +angle makes the camera travel to its right around the subject, and yaw increases by the same angle so the camera keeps facing the subject. "Orbit left" is a negative angle. The distance eases from the start distance to params.radius, so frame 0 always equals the start pose.
- dolly_zoom: the subject stays the same size on screen. The subject is at the look-at point, at distance d0 = params.subject_distance if given, else the distance from the start position to the origin along the start forward direction (dot(origin - position, forward)). d0 <= 0 is a ValueError. The camera moves along forward by distance·e, so d(e) = d0 - distance·e, which must stay at least 0.05·d0 (else ValueError). The fov follows the distance: k = d0·tan(fov0/2) and fov(e) = 2·atan(k / d(e)) in degrees. `fov_end` is deprecated and ignored.
- frame (dolly, dolly_zoom, crane; optional): "world" (default) moves along the world axes, as described above. "camera" moves along the camera's own axes, taken from the start pose and fixed for the whole move: forward (-sin yaw·cos pitch, sin pitch, -cos yaw·cos pitch), right (cos yaw, 0, -sin yaw), up = right × forward. In camera frame, dolly z +distance moves toward where the camera looks, dolly x +distance moves to its right, dolly_zoom moves along forward, and crane moves along camera up. At the default start both frames give the same path. Use "camera" when chaining presets.
- orbit radius is optional. Without it, orbit keeps the camera's current distance from the origin. With it, the distance eases from the current distance to radius.
- Every move: frame 0 equals the start pose exactly.

Turns a camera-motion preset into per-frame camera state.

## Contract
- Preset: `{"type": ..., "params": {...}, "easing": ...}`. `type` is one of
  `dolly`, `dolly_zoom`, `crane`, `orbit`, `pan`, `tilt`. Type parameters live under
  `params` (`distance`, `angle`, `axis`, `radius`, `frame`, `subject_distance`); `easing` stays top-level and
  defaults to `linear`.
- Easing names: `linear`, `ease_in`, `ease_out`, `ease_in_out` (`engine/easing.py`, `ease(name, u)`).
- `path(preset, duration_s=3.0, fps=24, start=None)` returns `N = round(duration_s * fps) + 1`
  frames of `{t, position, rotation, fov}`; frame i has `t = i / fps` and eased progress
  `ease(easing, i / (N - 1))`.
- Units: degrees. Position `[x, y, z]`. Rotation `[yaw, pitch, roll]`.
  Orbit and pan change yaw, tilt changes pitch.
- Default start: position `[0, 0, 5]`, rotation `[0, 0, 0]`, fov 50.
- Unknown motion type or easing raises `ValueError`.

## Files
- `easing.py` - easing curves
- `camera_path.py` - path generation and a small CLI
- `test_camera_path.py` - 11 tests

## Run
    cd camera-motion && python3 -W error -m unittest
    camera-motion path preset.json   # prints first and last frame (or: python3 -m camera_motion.engine.camera_path preset.json)

## Preview (preview/)

Draws a preset's camera path as an SVG, standard library only.

- `preview/svg_path.py` — `render(frames, out_path, title)` writes one SVG with two panels: top-down (x vs z) and side (z vs y). It shows the path (`polyline.path`), start (green `circle.start`), end (red `circle.end`), the subject at the origin (`path.subject`), and look-direction ticks (`line.look`) about every 12 frames. Look direction comes from yaw/pitch: (-sin yaw·cos pitch, sin pitch, -cos yaw·cos pitch). Each panel scales itself with padding.
  CLI: `camera-motion preview <id|preset.json> -o out.svg` (or `python3 -m camera_motion.preview.svg_path preset.json [-o out.svg]`)
- `preview/render_all.py` — `main(catalog, out_dir)` writes `<id>.svg` per preset plus an `index.html` grid.
  CLI: `python3 -m camera_motion.preview.render_all [catalog.json] [-o dir]` (default `preview/out/`, git-ignored)
- `preview/sample_catalog.json` — 6 sample presets: dolly_in, crane_up, orbit_left, pan_right, tilt_up, dolly_zoom_in. Positive yaw turns left, so pan_right uses -30.
- `preview/test_svg_path.py` — valid XML, N-point polylines, start/end markers, every sample preset renders.

Run all tests from `camera-motion/`: `python3 -W error -m unittest` (28 tests).

## Live preview page (web/)

- `web/export_frames.py`: writes `web/frames.json` (`{id: {name, type, fps, frames}}`) from `presets/catalog.json` if it exists, otherwise `preview/sample_catalog.json`. CLI: `python3 -m camera_motion.web.export_frames [catalog] [-o out]`.
- `web/serve.py`: `camera-motion serve [--port N]` (or `python3 -m camera_motion.web.serve [port]`) (default 8790). It regenerates `frames.json` on start, then serves `web/`. `frames.json` is git-ignored.
- `web/index.html`: self-contained page with a preset list, Play/Pause and a scrub slider. It shows a top-down view (path, subject, camera triangle) and a camera view (ground grid and subject cube in perspective), looping at the preset's fps.
- `web/test_export.py`: every preset is exported, frame counts match the engine, all values are finite.

## Export (export/)

Turns engine frames into per-frame camera trajectories for camera-controlled video models (CameraCtrl / MotionCtrl style).

- `export/trajectory.py`: `pose_matrix(frame)` gives the camera-to-world 4x4 and `world_to_camera(frame)` its inverse. `intrinsics(fov, w, h)` gives normalized (fx, fy, cx, cy), and `K(...)` the 3x3. Rotation is R = Ry(yaw) · Rx(pitch) · Rz(roll).
- `export/formats.py`: `to_realestate10k`, `parse_re10k` and `to_json`, plus the CLI.
- CLI: `camera-motion export <id|preset.json> --format re10k|json -o out [--fps 24 --seconds 3 --width 1024 --height 576]` (or `python3 -m camera_motion.export.trajectory ...`)

Axis conventions:
- **JSON** (`c2w`, `w2c`): OpenGL camera axes, the same as the engine. x right, y up, the camera looks down -z.
- **RE10K** lines: OpenCV camera axes, which RealEstate10K loaders expect. x right, y down, the camera looks down +z. This is the OpenGL w2c with its y and z rows negated.
- Intrinsics are normalized: vertical fov, fy = 0.5 / tan(fov/2), fx = fy · height / width, cx = cy = 0.5.

RE10K line format: `<timestamp_us> fx fy cx cy 0 0 <3x4 world_to_camera, row-major>`. Line 0 is `# <preset name>`. Example (dolly_in, frame 0, 1024x576, fov 50):

    0 0.6031425713933134 1.0722534602547793 0.5 0.5 0.0 0.0 1.0 0.0 0.0 -0.0 -0.0 -1.0 -0.0 0.0 -0.0 -0.0 -1.0 5.0
