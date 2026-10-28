# Make a clip: page contract

A one-page tool for the owner: pick or drop a picture, pick a move, render, watch, download.
Stdlib server (`server.py`), one page (`app.html`), same look as the shotplan page.

## Folders (the only two the server may write to)

- Inputs: `~/Pictures/stillmove-input/` (`INPUT_ROOT`)
- Outputs: `~/Pictures/stillmove-output/<name>-<YYYYMMDD-HHMMSS>/` (`OUTPUT_ROOT`)

Both are constructor arguments of the server's `Studio` object, so tests pass temporary folders and
never touch `~/Pictures`. Every path the server writes or serves is resolved with `realpath` and must
stay strictly inside one of the two roots.

## Safety (same rules as the shotplan page)

- Bind 127.0.0.1 only; `allow_reuse_address = False`; no `getfqdn` in `server_bind`.
- `Host` must be `127.0.0.1` or `localhost` (any port), else 403.
- Any request with an `Origin` that is not 127.0.0.1/localhost: 403.
- Every POST also needs header `X-Requested-With: makeclip` (a cross-site form cannot set it): 403.
- JSON POST bodies: `Content-Type: application/json`, at most 64 KB (413 above).
- Upload: `POST /api/upload?name=<file name>` with the raw image as the body,
  `Content-Type: image/png` or `image/jpeg` (415 otherwise), at most 25 MB (413 above);
  the first bytes must be a real PNG or JPEG signature (400 otherwise). The saved name is
  `<slug of the name, a-z0-9- only, max 40>-<YYYYMMDD-HHMMSS>.<png|jpg>`.

## Routes

| Route | Reply |
|---|---|
| `GET /` | the page |
| `GET /api/options` | `{presets: [{id, name, param, unit, min, max, default, help}], durations: {min:2, max:8, default:4}, sizes: [{id:"square", w:1080, h:1080}, {id:"wide", w:1920, h:1080}, {id:"vertical", w:1080, h:1920}], depth: {simple: true, real: <model file present>}, layers: <isnet model present and onnxruntime importable by the render python>}` |
| `GET /api/images` | input pictures, newest first: `[{name, url, width, height}]` (width and height are null when the file's header cannot be read) |
| `GET /inputs/<name>` | one input picture (thumbnail use) |
| `POST /api/upload?name=` | `{name, url, width, height}` of the saved picture |
| `POST /api/render` | body `{image, preset, amount, seconds, size, layers, depth, smooth, enhance}` (`smooth`: 0 or absent for off, 2 or 4, also with Every size; refused when seconds times 24 fps is not a multiple of it) → 202 `{job}` (with `enhance` true and a picture smaller than the output, the render gets `--upscale 2|4` (the smallest that reaches the output size, at most 4) and `--upscale-backend esrgan` when `~/models/realesrgan/realesr-general-x4v3.onnx` and onnxruntime are present in the render python, else `lanczos`; status has `upscale: {factor, method}` and `clip.json` has `upscale`); (with `smooth` on, the render uses `--interp-backend rife` when the RIFE model file and onnxruntime are present in the render python, else `linear`; `GET /api/render/status` then has `smooth: {factor, backend}` and `clip.json` has `interp`); 409 while one runs; 400 with a plain message for any value out of range |
| `GET /api/render/status` | `{state: idle|running|done|error, frame, frames, message, clip}` |
| `GET /api/clips` | recent clips, newest first, at most 20: `[{name, made, seconds, size, preset, mp4, gif}]` |
| `GET /clips/<dir>/preview.mp4` and `/preview.gif` | the file; `?download=1` adds `Content-Disposition: attachment` |

## Safe ranges (amount = the preset's one parameter; the server clamps nothing, it refuses)

| preset | param | unit | min | max | default |
|---|---|---|---|---|---|
| dolly_in / dolly_out | distance | scene units | 0.1 | 0.6 | 0.4 |
| dolly_left / dolly_right | distance | scene units | 0.1 | 0.4 | 0.25 |
| dolly_zoom_in / dolly_zoom_out | distance | scene units | 0.1 | 0.5 | 0.3 |
| crane_up / crane_down | distance | scene units | 0.1 | 0.4 | 0.25 |
| orbit_left / orbit_right | angle | degrees | 2 | 12 | 8 |
| pan_left / pan_right | angle | degrees | 2 | 10 | 6 |
| tilt_up / tilt_down | angle | degrees | 2 | 8 | 5 |

The sign comes from the preset (left/right, up/down); `amount` is always positive.
Orbits keep the current distance (no radius).

## Render

1. Camera: the preset with its one parameter set from `amount` (sign from the catalog), run by
   camera-motion for `seconds` at 24 fps, exported with `to_json` at the chosen size into
   `<clip>/camera.json`.
2. `stillmove render <image> --cam <clip>/camera.json --backend <plane|da2-small> --mp4 -o <clip>`,
   plus `--layers --matte isnet` when layers is on, plus `--fg-depth 4.6` when layers is on with
   simple depth (a flat backdrop needs it to float).
3. It runs as a subprocess under `RENDER_PYTHON` (the video-studio venv when present).
4. Progress = files in `<clip>/frames/` out of `round(seconds * 24) + 1`.
5. Done when the subprocess exits 0 and `preview.mp4` exists (else `error` with the last stderr line).
   `<clip>/clip.json` records the request for the Recent list.
