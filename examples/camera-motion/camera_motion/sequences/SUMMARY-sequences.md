# Camera-motion sequences

A sequence chains catalog presets into one shot: each segment is one preset, played one after another,
and each starts exactly where the previous one ended. Python 3, standard library only.

## Format

One JSON object per shot (contract: `schema.json`):

| Field | Meaning |
|---|---|
| `id` | lowercase id (`^[a-z][a-z0-9_]*$`) |
| `name` | display name |
| `description` | one line, at most 120 characters |
| `fps` | frames per second, 1 to 120 |
| `segments` | list, at least one: `preset` (an id in `presets/catalog.json`), `duration_s` (above 0, up to 60), optional `params` |

`segments[].params` are merged over that preset's catalog params. The merged preset must still pass
`presets/schema.json`, so an override can change or remove a value but cannot add a param the preset type does not have.

### Null override

A param set to `null` is removed from the merged preset. Example: `{"radius": null}` on an orbit makes it keep the
current distance to the subject instead of the catalog radius. There is one merge rule (`merge_params()` in
`validate.py`); `build()` uses it too.

### Camera frame after turns

Dollies, dolly zooms and cranes move along fixed world axes by default. After a pan, tilt or orbit the camera faces a
new way, so add `{"frame": "camera"}` to the move that follows a turn and it travels along the camera's own axes
(for example, `dolly_out` after `pan_left` pulls straight back from where the camera now looks).

## Build and validate

Run from `camera-motion/` (not the repository root: the folder name has a hyphen, so Python cannot import it from there).

```
python3 -m camera_motion.sequences.build camera_motion/sequences/example.json
python3 -m camera_motion.sequences.build SEQUENCE.json -o frames.json
python3 -m camera_motion.sequences.build SEQUENCE.json --catalog OTHER_CATALOG.json
```

The build CLI validates first (`validate_sequence()`), so a bad sequence prints every error and exits 1. On success it
prints the segment count, frame count, duration and the start and end poses, and `-o` also writes the frames
(`{t, position, rotation, fov}` per frame). The first frame of each later segment is dropped because it equals the
previous segment's last frame; time runs continuously, frame `i` has `t = i / fps`.

To validate from code: `validate_sequence(sequence, catalog=None)` returns a list of error strings, empty when valid.

## Samples

Three shots in `samples/`, plus `example.json` at the top of the folder. The description is the `description` field of each file.

| File | Segments | Description (Ornith) |
|---|---|---|
| `establishing.json` | `crane_up` 2 s, `pan_left` 2 s, `dolly_out` 2 s in camera frame | The camera rises, sweeps across the scene, then pulls back to reveal the full landscape. |
| `house_walkup.json` | `dolly_in` 3 s, `tilt_up` 1.5 s | Camera tracks toward the front door, then tilts up to the roofline. |
| `product_reveal.json` | `dolly_in` 2 s with `distance` 2.0, `orbit_left` 3 s with `radius` null | The camera dollies in toward the product, then orbits around it. |

## Tests

| File | Tests | Covers |
|---|---|---|
| `test_validate.py` | 36 | schema bounds, unknown presets, the merge rule, null removal, params a preset type does not allow |
| `test_build.py` | 16 | frame counts, times, exact joins, overrides, null removal, rejections, JSON-serialisable frames (the CLI itself is not tested) |
| `test_samples.py` | 10 | samples and `example.json` validate and build, camera-frame moves after turns, orbits hold distance, no internal names |

The whole suite, from `camera-motion/`:

```
python3 -m unittest      # 284 tests, all green
```

`camera_motion/test_privacy.py` scans every shipped file (this one included) for user names, home paths, private IPs,
`.local` hostnames and machine names, so keep them out of docs.
