"""Generate the camera-motion preset docs from the catalog.

Run from camera-motion/:
    python3 -m camera_motion.presets.make_docs

Writes docs/PRESETS.md and docs/presets.html. The output has no timestamps,
so re-running it on the same catalog gives identical files. End poses come from
integration.run_catalog.run(). If any preset fails validation, nothing is written
and the exit status is 1.

SVG path previews are drawn only when preview/svg_path.py imports and exposes
render(frames, out_path, title). Each SVG is written to a temp file, read back and
inlined without its <?xml?> line. Otherwise the docs say the previews were skipped.
"""

import argparse
import html
import importlib
import os
import sys
import tempfile

from camera_motion.engine.camera_path import path
from camera_motion.integration.run_catalog import run
from camera_motion.presets.validate import load_json

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(HERE))  # camera-motion/
CATALOG_PATH = os.path.join(HERE, "catalog.json")
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
MD_PATH = os.path.join(DOCS_DIR, "PRESETS.md")
HTML_PATH = os.path.join(DOCS_DIR, "presets.html")

SCENE = "scene units"
DEG = "degrees"

# (type, parameter) -> (units, what a sign or value means).
# Signs follow the engine's conventions (engine/SUMMARY.md).
PARAM_INFO = {
    ("dolly", "axis"): ("none", "z = forward and back along the view; x = sideways"),
    ("dolly", "distance"): (SCENE,
        "axis z: positive = forward toward the subject. axis x: positive = right (+x)."),
    ("dolly_zoom", "distance"): (SCENE, "positive = in, the camera moves forward toward the subject"),
    ("dolly_zoom", "fov_end"): (DEG, "lens field of view at the end of the move; larger = wider lens"),
    ("crane", "distance"): (SCENE, "positive = up"),
    ("orbit", "angle"): (DEG, "+angle = camera travels to its right; Orbit Left = negative"),
    ("orbit", "radius"): (SCENE, "distance from the subject to the camera during the orbit"),
    ("pan", "angle"): (DEG, "positive = yaw left, the view turns left"),
    ("tilt", "angle"): (DEG, "positive = pitch up, the view tilts up"),
}

EASING_MEANING = {
    "linear": "constant speed",
    "ease_in": "starts slowly, speeds up",
    "ease_out": "starts quickly, slows down",
    "ease_in_out": "starts and ends slowly",
}

CONVENTIONS_SOURCE = "engine/SUMMARY.md"

PREVIEW_ENTRY = "render"


class DocsError(Exception):
    """The catalog cannot be documented, so no docs are written."""


def _presets_of(data):
    return data if isinstance(data, list) else data.get("presets")


def build_entries(catalog_path=CATALOG_PATH):
    """Return one dict per preset, in catalog order, with the engine's end pose."""
    rows, errors = run(catalog_path)
    if errors:
        raise DocsError("catalog has errors: %s" % "; ".join(errors))
    failed = [row[0] for row in rows if row[1] != "OK"]
    if failed:
        raise DocsError("presets failed the engine run: %s" % ", ".join(failed))

    cells_by_id = {row[0]: row[2] for row in rows}
    presets = _presets_of(load_json(catalog_path))
    entries = []
    for preset in presets:
        frames, start, end, fov = cells_by_id[preset["id"]]
        entries.append({
            "id": preset["id"],
            "name": preset["name"],
            "description": preset["description"],
            "type": preset["type"],
            "easing": preset["easing"],
            "params": list(preset["params"].items()),
            "frames": frames,
            "end_pose": end,
            "end_fov": fov,
            "preset": preset,
        })
    return entries


def _param_meaning(kind, key):
    return PARAM_INFO.get((kind, key), ("?", "not documented"))


def _strip_xml_decl(text):
    """Drop a leading <?xml ...?> line so the SVG can be inlined in HTML."""
    if text.startswith("<?xml"):
        return text.split("\n", 1)[1] if "\n" in text else ""
    return text


def _svg_map(entries):
    """Return (svgs, note). svgs maps preset id to inline SVG text, or is None when skipped."""
    try:
        module = importlib.import_module("camera_motion.preview.svg_path")
    except ImportError:
        return None, "SVG path previews skipped: preview/svg_path.py is not present in this studio."
    render = getattr(module, PREVIEW_ENTRY, None)
    if not callable(render):
        return None, ("SVG path previews skipped: preview/svg_path.py has no %s(frames, out_path, title) "
                      "entry point." % PREVIEW_ENTRY)
    svgs = {}
    with tempfile.TemporaryDirectory() as tmp:
        for entry in entries:
            out_path = os.path.join(tmp, entry["id"] + ".svg")
            render(path(entry["preset"]), out_path, entry["name"])
            with open(out_path, encoding="utf-8") as handle:
                svg = _strip_xml_decl(handle.read()).strip()
            if not svg.startswith("<svg"):
                return None, "SVG path previews skipped: %s did not write an <svg> document." % PREVIEW_ENTRY
            svgs[entry["id"]] = svg
    return svgs, None


def render_markdown(entries=None):
    if entries is None:
        entries = build_entries()
    out = []
    out.append("# Camera-motion presets")
    out.append("")
    out.append("Generated by `python3 -m camera_motion.presets.make_docs` from `presets/catalog.json`. Do not edit by hand.")
    out.append("")
    out.append("Units: distances are in scene units, angles and fov are in degrees. "
               "Every end pose is the engine's result for the preset run from the default start "
               "(position [0, 0, 5], rotation [0, 0, 0], fov 50) over 3 s at 24 fps.")
    out.append("")
    out.append("## Sign conventions")
    out.append("")
    out.append("The sign conventions below were answered by the engine studio in `%s`." % CONVENTIONS_SOURCE)
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| id | name | type | frames | end pose | end fov |")
    out.append("|---|---|---|---|---|---|")
    for e in entries:
        out.append("| `%s` | %s | `%s` | %d | %s | %s |"
                   % (e["id"], e["name"], e["type"], e["frames"], e["end_pose"], e["end_fov"]))
    out.append("")
    out.append("## Presets")
    for e in entries:
        out.append("")
        out.append("### `%s` - %s" % (e["id"], e["name"]))
        out.append("")
        out.append(e["description"])
        out.append("")
        out.append("- Type: `%s`" % e["type"])
        out.append("- Easing: `%s` (%s)" % (e["easing"], EASING_MEANING.get(e["easing"], "")))
        out.append("- End pose: %s, end fov %s" % (e["end_pose"], e["end_fov"]))
        out.append("")
        out.append("| Parameter | Value | Units | Sign / meaning |")
        out.append("|---|---|---|---|")
        for key, value in e["params"]:
            units, meaning = _param_meaning(e["type"], key)
            out.append("| `%s` | %s | %s | %s |" % (key, value, units, meaning))
    out.append("")
    out.append("## Previews")
    out.append("")
    _, note = _svg_map(entries)
    out.append(note or "SVG path previews are embedded in `presets.html`.")
    out.append("")
    return "\n".join(out)


def _esc(value):
    return html.escape(str(value))


def render_html(entries=None):
    if entries is None:
        entries = build_entries()
    svgs, note = _svg_map(entries)

    css = """
:root { --bg: #fafafa; --fg: #1d1d1f; --muted: #6e6e73; --card: #ffffff; --line: #d2d2d7; --accent: #0a6cff; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { --bg: #111113; --fg: #f5f5f7; --muted: #a1a1a6; --card: #1c1c1f; --line: #3a3a3f; --accent: #5ea2ff; }
}
:root[data-theme="dark"] { --bg: #111113; --fg: #f5f5f7; --muted: #a1a1a6; --card: #1c1c1f; --line: #3a3a3f; --accent: #5ea2ff; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0.5rem 0; }
h2 { font-size: 1.2rem; margin-top: 2rem; }
h3 { font-size: 1.05rem; margin: 0 0 0.25rem; }
code { font-family: ui-monospace, Menlo, monospace; font-size: 0.9em; }
p.muted, .muted { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin: 12px 0; }
table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
.table-wrap { overflow-x: auto; }
.note { border-left: 3px solid var(--accent); padding: 4px 10px; margin: 8px 0; }
svg { max-width: 100%; height: auto; }
ul { padding-left: 1.2rem; }
"""

    parts = []
    parts.append("<!doctype html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>Camera-motion presets</title>")
    parts.append("<style>%s</style>" % css)
    parts.append("</head>")
    parts.append("<body>")
    parts.append("<main>")
    parts.append("<h1>Camera-motion presets</h1>")
    parts.append('<p class="muted">Generated by <code>python3 -m camera_motion.presets.make_docs</code> from '
                 "<code>presets/catalog.json</code>. Do not edit by hand.</p>")
    parts.append('<p class="muted">Units: distances are in scene units, angles and fov are in degrees. '
                 "End poses are the engine's result from the default start (position [0, 0, 5], rotation "
                 "[0, 0, 0], fov 50) over 3 s at 24 fps.</p>")
    parts.append("<h2>Sign conventions</h2>")
    parts.append('<p class="muted">The sign conventions below were answered by the engine studio in '
                 "<code>%s</code>.</p>" % _esc(CONVENTIONS_SOURCE))

    parts.append("<h2>Summary</h2>")
    parts.append('<div class="table-wrap"><table>')
    parts.append("<tr><th>id</th><th>name</th><th>type</th><th>frames</th><th>end pose</th><th>end fov</th></tr>")
    for e in entries:
        parts.append("<tr><td><code>%s</code></td><td>%s</td><td><code>%s</code></td><td>%d</td>"
                     "<td>%s</td><td>%s</td></tr>"
                     % (_esc(e["id"]), _esc(e["name"]), _esc(e["type"]), e["frames"],
                        _esc(e["end_pose"]), _esc(e["end_fov"])))
    parts.append("</table></div>")

    parts.append("<h2>Presets</h2>")
    for e in entries:
        parts.append('<section class="card">')
        parts.append("<h3><code>%s</code> - %s</h3>" % (_esc(e["id"]), _esc(e["name"])))
        parts.append("<p>%s</p>" % _esc(e["description"]))
        parts.append("<ul>")
        parts.append("<li>Type: <code>%s</code></li>" % _esc(e["type"]))
        parts.append("<li>Easing: <code>%s</code> (%s)</li>"
                     % (_esc(e["easing"]), _esc(EASING_MEANING.get(e["easing"], ""))))
        parts.append("<li>End pose: %s, end fov %s</li>" % (_esc(e["end_pose"]), _esc(e["end_fov"])))
        parts.append("</ul>")
        parts.append('<div class="table-wrap"><table>')
        parts.append("<tr><th>Parameter</th><th>Value</th><th>Units</th><th>Sign / meaning</th></tr>")
        for key, value in e["params"]:
            units, meaning = _param_meaning(e["type"], key)
            parts.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (_esc(key), _esc(value), _esc(units), _esc(meaning)))
        parts.append("</table></div>")
        if svgs is not None:
            parts.append(svgs[e["id"]])
        parts.append("</section>")

    parts.append("<h2>Previews</h2>")
    if note:
        parts.append('<p class="note">%s</p>' % _esc(note))
    else:
        parts.append('<p class="muted">Path previews are drawn above each preset.</p>')
    parts.append("</main>")
    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"


def _write(path_, text):
    with open(path_, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate docs/PRESETS.md and docs/presets.html.")
    parser.add_argument("--catalog", default=CATALOG_PATH, help="catalog JSON (default: presets/catalog.json)")
    args = parser.parse_args(argv)

    try:
        entries = build_entries(args.catalog)
    except (DocsError, OSError, ValueError, KeyError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1

    md = render_markdown(entries)
    page = render_html(entries)
    os.makedirs(DOCS_DIR, exist_ok=True)
    _write(MD_PATH, md)
    _write(HTML_PATH, page)
    print("wrote %s" % MD_PATH)
    print("wrote %s" % HTML_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
