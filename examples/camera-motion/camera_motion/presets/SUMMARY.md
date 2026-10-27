# Camera-motion presets

Our own catalog of camera moves in the shared preset format that the engine reads.
Python 3, standard library only.

## Files

| File | What it is |
|---|---|
| `schema.json` | The contract: JSON Schema (2020-12) for one preset. Types, required params per type, value ranges, sign conventions. |
| `catalog.json` | The 14 presets, as a plain JSON list. |
| `validate.py` | Stdlib checker. Reads its rules from `schema.json` at run time, reports one clear error per bad field, rejects duplicate ids and `NaN`/`Infinity`. |
| `test_presets.py` | 38 unittest tests: the catalog validates, ids are unique, bad presets are rejected with messages that name the field and the rule. |
| `__init__.py` | Empty; lets `python3 -m unittest` find the tests from `camera-motion/`. |
| `make_docs.py` | Generates `docs/PRESETS.md` and `docs/presets.html` from the catalog (end poses via `integration.run_catalog.run()`, SVG previews via `preview/svg_path.py`). |
| `test_docs.py` | Every preset is in both docs, generation is byte-identical run to run, the committed docs match a fresh run, the page loads nothing from the network. |

## How to check

Run from `camera-motion/` (or from `camera-motion/presets/`):

```
python3 -m unittest                         # 114 tests: presets, docs, engine, preview, web, integration
python3 presets/validate.py catalog.json    # OK: 14 preset(s) valid
```

`validate.py` exits 0 when everything is valid and 1 on any error. A catalog name that is not found in the
current folder is looked up next to the script.

Do not run `python3 -m unittest` from the repository root: the folder name `camera-motion` contains a hyphen, so
Python cannot import it, finds no tests, and exits 5 ("NO TESTS RAN").

## The catalog

| id | type | params | meaning |
|---|---|---|---|
| dolly_in | dolly | axis z, distance +1.0 | forward toward the subject |
| dolly_out | dolly | axis z, distance -1.0 | backward |
| dolly_left | dolly | axis x, distance -1.0 | slide left |
| dolly_right | dolly | axis x, distance +1.0 | slide right |
| dolly_zoom_in | dolly_zoom | distance +1.0 | moves in while the lens widens (fov 50 -> about 60.5); subject keeps its size |
| dolly_zoom_out | dolly_zoom | distance -1.0 | moves back while the lens narrows (fov 50 -> about 42.5); subject keeps its size |
| crane_up | crane | distance +1.0 | rise |
| crane_down | crane | distance -1.0 | lower |
| orbit_left | orbit | angle -45, radius 5.0 | camera travels to its left (clockwise seen from above) |
| orbit_right | orbit | angle +45, radius 5.0 | camera travels to its right (counter-clockwise seen from above) |
| pan_left | pan | angle +30 | turn view left in place (positive yaw = left, as in the engine) |
| pan_right | pan | angle -30 | turn view right in place |
| tilt_up | tilt | angle +20 | pitch view up in place |
| tilt_down | tilt | angle -20 | pitch view down in place |

All presets use `ease_in_out`.

## Rules the validator enforces

- Exactly the fields `id`, `name`, `type`, `params`, `easing`, `description`; no extras, at the top level or in `params`.
- `id` is snake_case (`^[a-z][a-z0-9_]*$`) and unique in the catalog; `name` and `description` are non-empty.
- Ranges: dolly/dolly_zoom/crane distance -50..50; orbit angle -360..360, radius above 0 up to 50; pan angle -180..180;
  tilt angle -90..90; dolly_zoom subject_distance > 0 (fov_end is deprecated and ignored). A distance or angle of exactly 0 is rejected (the move would do nothing).

## Sign conventions

Matching the engine (conventions at the top of `engine/SUMMARY.md`):
positive dolly z = forward, positive dolly x = right, positive crane = up, positive dolly_zoom distance = in,
positive orbit angle = camera travels to its right (so Orbit Left is negative), positive pan = yaw left,
positive tilt = pitch up. Every move's frame 0 equals the start pose.

## Integration with the engine

Run from `camera-motion/`:

```
python3 -m camera_motion.integration.run_catalog   # validates each preset, runs it through the engine's path(),
                                                   # prints id, frames, start pose, end pose, end fov; exit 1 on any failure
python3 -W error -m unittest                       # all tests
python3 -m camera_motion.presets.make_docs         # regenerates docs/PRESETS.md and docs/presets.html
```

`integration/test_integration.py` checks that every preset validates and runs to 73 finite frames
(3 s at 24 fps), starts at the engine's default pose, and ends where its name says: dolly_in closer to the origin,
crane_up higher, orbits rotate yaw by exactly `angle` and keep their radius, pans and tilts change only yaw or pitch,
dolly zooms keep the subject the same size on screen. Synthetic presets pin the engine's own sign convention.

Conventions the catalog follows, all matching the engine:

| Topic | Convention |
|---|---|
| dolly x | +distance moves the camera right; dolly_left / dolly_right use the sign accordingly |
| pan | positive yaw turns left: pan_left +30, pan_right -30 |
| tilt | positive pitch looks up |
| orbit | +angle travels to the camera's right: orbit_left -45, orbit_right +45; the distance eases to `radius`, so frame 0 is the start pose |
| lens | the engine's default start lens is 50 degrees; dolly zoom 75 / 35 stand |
