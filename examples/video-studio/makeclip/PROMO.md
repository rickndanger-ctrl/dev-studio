# Make a promo: contract (second tab of the Make a clip page)

Multi-shot promos from 2 to 6 pictures, rendered with `shotplan render`. Read API.md first:
everything there (guards, X-Requested-With, sizes, safe ranges, one job at a time) still holds.

## Part A: shotplan learns per-shot layers (shotplan.py, shotrender.py, schema.json)

- A shot may carry `"layers": true` (optional boolean, default false). `check` accepts it.
- `shotplan render ... [--matte isnet|fake]` (default isnet). A shot with layers renders through
  `stillmove.layers.Layers` with `stillmove.matte.get_matte(matte)`; with fake/plane depth the
  foreground sits at `--fg-depth` (default 4.6). The matte backend is created once per render, not
  once per shot. Shots without layers render exactly as today.
- Progress: unchanged files on disk (`shots/<id>/frames/`, then `frames/`), so a caller can count.

## Part B: the page and server (makeclip/server.py, app.html)

Folders: writes stay in INPUT_ROOT and OUTPUT_ROOT. New: PLANS_ROOT = `~/Pictures`, READ-ONLY, used
only to list and load plan JSON files and to read the pictures a loaded plan names. Every path is
resolved with realpath and must stay inside its root; nothing is ever written under PLANS_ROOT
except through INPUT_ROOT/OUTPUT_ROOT (which are inside it).

Routes (all POSTs need the guards from API.md):

| Route | Reply |
|---|---|
| `GET /api/promo/plans` | plan files under PLANS_ROOT, at most 3 folders deep, that parse as JSON objects with a `shots` list: `[{path, name, shots}]` (path relative to PLANS_ROOT) |
| `POST /api/promo/load` `{path}` | `{size, shots: [{image, found, preset, amount, seconds, transition, crossfade, layers, editable}], missing: [image paths]}`; a shot whose sequence is not one inline segment of a known preset is `editable: false` (shown read-only, still renderable) |
| `POST /api/promo/render` | body `{size, shots: [{image, preset, amount, seconds, transition: "cut"|"crossfade", crossfade, layers}], source}` (source = the loaded plan's path or null) → 202 `{job}`; 409 while any job runs; 400 for 2 > shots > 6, out-of-range values, a missing picture, or a crossfade not shorter than both neighbouring shots |
| `GET /api/render/status` | as API.md, plus for promos `{kind: "promo", shot, shots, frame, frames, stage: "shots"|"joining"}` |
| `GET /api/clips` | includes promos, with `kind: "clip"|"promo"` |

Images in a form shot are input-picture names (INPUT_ROOT); images in a loaded plan are resolved
from the plan's own folder. Either way the server copies each picture into
`<OUTPUT_ROOT>/<name>-<YYYYMMDD-HHMMSS>/images/` and writes `plan.json` there with paths relative to
it, then runs `RENDER_PYTHON shotplan/shotplan.py render plan.json <dir> --depth fake --mp4`
(+ `--matte isnet` when any shot has layers). fps 24; sizes as API.md; the last shot is always a cut.

Page: a second tab "Make a promo": add 2-6 shots (pick or drop a picture per shot), per shot a move
(safe-range slider), seconds, transition, Layers; size; "Load plan" (list from /api/promo/plans) that
fills the form and lists missing photos plainly ("Still needed: shot-1.jpg ..."); Render with
"shot N of M, frame X of Y" (then "joining, frame X of Y"), the MP4 player and downloads.
