# Video studio: shortlist and first build

Clean-room: written from general knowledge and from `FEATURES.md` only. No competitor sites, apps
or code were opened, and no network calls were made. Every model name, license and hardware
figure that is not firmly known carries **(verify)**. Scores are judgement, not measurement.

Targets: two Mac minis with 16 GB unified memory, one Linux laptop with 46 GB RAM, and no large
GPU assumed.

## Scoring

Each feature gets 1 to 5 on three axes (5 is best):

- **Hardware:** runs on our machines without a big GPU. 5 = CPU or Apple Silicon 16 GB is enough;
  1 = needs a 24+ GB GPU.
- **Value:** value to a filmmaker.
- **Effort:** how little we must build to get a useful first version. 5 = easiest.

The total is the plain sum (max 15). Feature 1 is already built, so it is listed for reference and
not ranked.

| # | Feature | Hardware | Value | Effort | Total | Note |
|---|---|---|---|---|---|---|
| 1 | Camera-motion control | 5 | 4 | 5 | 14 | done; reference only |
| 2 | Motion presets on images (warp first) | 5 | 4 | 4 | 13 | no model needed for v1 |
| 3 | Image-to-video, camera-conditioned | 1 | 5 | 1 | 7 | 24+ GB GPU (verify) |
| 4 | Video upscaling | 3 | 3 | 4 | 10 | per-frame, slow on CPU |
| 5 | Frame interpolation | 3 | 3 | 4 | 10 | 16 GB (verify) |
| 6 | Relighting | 2 | 3 | 2 | 7 | needs a light model (license verify) |
| 7 | Style transfer | 2 | 3 | 2 | 7 | per-frame ControlNet cost |
| 8 | Lip-sync | 2 | 4 | 2 | 8 | Wav2Lip is non-commercial (verify) |
| 9 | Background removal | 4 | 4 | 4 | 12 | images CPU ok; video 16 GB (verify) |
| 10 | Keyframe and shot planning | 5 | 4 | 4 | 13 | data and scheduler; sequences exist |

## Top 5 (after the reference feature)

| Rank | # | Feature | Total | Why it ranks here |
|---|---|---|---|---|
| 1 | 2 | Motion presets on images | 13 | Fits the Mac minis; reuses our preset names and timing; warp needs no model for v1. |
| 2 | 10 | Keyframe and shot planning | 13 | Pure data and scheduling; `camera-motion` sequences already hold the shape. |
| 3 | 9 | Background removal | 12 | CPU ok for stills; the matte enables parallax moves over a fixed background. |
| 4 | 4 | Video upscaling | 10 | Useful but slow; a post step, not a camera task. |
| 5 | 5 | Frame interpolation | 10 | Useful for smoothness; mostly a time-mapping check on our exports. |

Features 3 and 6 to 8 score lowest on hardware or license risk for our machines, so they wait.

## First build: still-to-motion (feature 2, warp-first)

**Goal.** Take one still image, a camera preset from `camera-motion`, and a duration, and produce a
short clip (a frame folder, plus MP4 later) whose camera follows that move.

**Why this one.** It runs on a Mac mini, reuses our preset names and timing, and the first version
needs only geometry: a depth map plus a per-frame camera pose. The learned depth model is one
adapter, so the core stays stdlib-first and testable with a fake backend.

**Stdlib-first, honestly.** The camera path, manifest, CLI, web API, tests and doctor are all
stdlib. Pixel warping needs an imaging library for decode and fast array math. Pure Python works for
tests and small images but is too slow for real clips. The choice (Pillow or numpy, both outside
the standard library) is an open decision for the owner. The adapter keeps it contained.

### Where `camera-motion` feeds in

The export is the input. Run it once per move:

    camera-motion export dolly_in --format json --seconds 3 --fps 24 --width 1024 --height 576 -o cam.json

The JSON has `fps`, `width`, `height` and `frames`. Each frame has `t`, `K` (3x3, normalized
intrinsics), `c2w` (4x4, OpenGL camera-to-world) and `w2c`. For warping:

- Pixel focal lengths come from `K`: `fx_px = K[0][0] * width`, `fy_px = K[1][1] * height`,
  principal point `(K[0][2] * width, K[1][2] * height)`.
- The reference frame is frame 0. A point in camera 0's coordinates moves into camera i's
  coordinates with `w2c[i] @ c2w[0]`. (`inv(c2w[0]) @ c2w[i]` is the other direction: camera i's
  pose seen from camera 0.)
- Axes: `c2w`/`w2c` use OpenGL camera axes (x right, y up, looking down -z), but `K` projects in
  OpenCV axes (y down, looking down +z). Flip with `diag(1, -1, -1)` between the two, as the RE10K
  export already does; without it the warp comes out upside down and behind the camera.

For a depth-based warp, a pixel `(u, v)` with depth `d` in the source frame is back-projected to a
3D point, moved into frame `i` with the relative pose, then projected with that frame's `K`. The
Python import route is also available: `camera_motion.engine.camera_path.path(preset, duration_s,
fps)` returns the same per-frame position, rotation and fov, if the JSON file is not needed.

Sequences feed later: `camera-motion sequence` output (a list of frames with `t`) can drive one
shot per segment.

### Package layout (stdlib-first)

    video-studio/stillmove/
      __init__.py
      cli.py          subcommands: render, presets, serve, doctor
      cameras.py      read cam.json (from camera-motion) -> frames with K, c2w; pixel intrinsics
      depth.py        the one adapter class: DepthBackend.depth(image) -> per-pixel depth
      backends/
        fake.py       constant or gradient depth plane; no model; used by tests and doctor
        depth_model.py  a real depth model (name and license verify); optional import
      warp.py         back-project, move, project; writes frame images
      jobs.py         a render job: id, status, output folder, manifest
      web/
        app.html      one page: preset list, image upload, Render, preview
        server.py     127.0.0.1 only; small JSON API
      test_*.py       unit tests, golden render from the fake backend, CLI and API tests
    video-studio/docs/SHORTLIST.md (this file)

### The adapter (one class)

    class DepthBackend:            # the only model-facing interface
        name = "fake"
        def depth(self, image):    # image: H x W x 3 (list or array) -> H x W depths (float)
            raise NotImplementedError

`FakeBackend` returns a flat plane at a fixed depth, so warp and CLI tests run with no model
installed. `DepthModelBackend` wraps a real depth model behind the same method; swapping backends
is one line in `cli.py` or a `--backend` flag. The model license and install steps are **(verify)**
and must be confirmed before any shipping use.

### CLI

    stillmove presets                                  # list camera-motion presets (from the catalog)
    stillmove render IMAGE --cam cam.json -o OUTDIR    # frames, then manifest.json
    stillmove render IMAGE --preset dolly_in -o OUTDIR # calls camera-motion for cam.json first
    stillmove serve --port 8800                         # web page + API on 127.0.0.1 only
    stillmove doctor                                    # one PASS or FAIL line per check

### Web page and small JSON API (127.0.0.1)

- `GET /` the page; `GET /api/presets` the preset list; `POST /api/jobs` with the image and a
  preset id, returns a job id; `GET /api/jobs/<id>` the status and the manifest when done;
  `GET /api/jobs/<id>/frames/<n>` one rendered frame.
- Bind 127.0.0.1 only. Upload size limit, job folder only (no arbitrary paths from the request).

### Tests and doctor

- Unit: `cameras.py` parses a real camera-motion export; pixel intrinsics match `K` times size.
- Warp: a flat depth plane and an identity move reproduce the input (within 1 pixel).
- Golden: a fixed image, the fake backend and `dolly_in` give frame count equal to the engine's
  (`round(seconds * fps) + 1`) and pixels within a small tolerance of stored golden frames. Never
  byte-exact checksums: float math differs in the last bit between macOS and Linux (camera-motion
  0.1.3 learned this).
- CLI and API: render, presets, job status, 404 for an unknown job, 400 for a bad preset.
- Doctor: Python >= 3.9; `camera_motion` importable; export round trip; the fake backend renders
  one frame; the optional depth model is reported as WARN (not FAIL) when absent (**verify** how
  it is installed).

### Done means

- `python3 -W error -m unittest` is green with the fake backend and no model installed.
- `stillmove doctor` passes on a Mac mini with no GPU.
- One real depth backend is wired behind the adapter, or it is marked not yet done.

### Open decisions for the owner

1. Imaging library: Pillow or numpy (both outside the standard library).
2. Which depth model, and its license (**verify**; never assumed).
3. Output: frame folder first; MP4 encoding needs an external tool (**verify** which).
4. License for our own code: not chosen.
