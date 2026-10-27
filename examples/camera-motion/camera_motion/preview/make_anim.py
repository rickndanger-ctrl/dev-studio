"""Regenerate the animated preview SVGs and the README gallery.

Run from camera-motion/:  python3 -m camera_motion.preview.make_anim

Writes docs/anim/<id>.svg for every preset in the bundled catalog, then rewrites the grid
between <!-- anim:start --> and <!-- anim:end --> in README.md (inserted under the title
the first time).
"""
import html
import json
import os
import re
import sys

from camera_motion.engine.camera_path import path

FPS = 24
COLUMNS = 4
THUMB_WIDTH = 180
START = "<!-- anim:start -->"
END = "<!-- anim:end -->"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "camera_motion", "presets", "catalog.json")
ANIM_DIR = os.path.join(ROOT, "docs", "anim")
README = os.path.join(ROOT, "README.md")


def grid_block(presets):
    """The marked HTML table: COLUMNS thumbnails per row, each with its name."""
    rows = []
    for i in range(0, len(presets), COLUMNS):
        cells = []
        for preset in presets[i:i + COLUMNS]:
            pid = html.escape(preset["id"])
            name = html.escape(preset.get("name", preset["id"]))
            cells.append(
                f'<td align="center"><img src="docs/anim/{pid}.svg" width="{THUMB_WIDTH}" '
                f'alt="{name}"><br>{name}</td>'
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return START + "\n<table>\n" + "\n".join(rows) + "\n</table>\n" + END


def update_readme(text, presets):
    """Return README text with the grid replaced, or inserted under the first heading."""
    block = grid_block(presets)
    if START in text and END in text:
        pattern = re.escape(START) + r".*?" + re.escape(END)
        return re.sub(pattern, lambda _: block, text, count=1, flags=re.S)
    heading, _, rest = text.partition("\n")
    return heading + "\n\n" + block + "\n" + rest


def main(argv=None):
    # Imported here so the README helpers above can be used without the renderer.
    from camera_motion.preview.anim_svg import render_animated

    with open(CATALOG, encoding="utf-8") as f:
        presets = json.load(f)
    os.makedirs(ANIM_DIR, exist_ok=True)
    for preset in presets:
        out = os.path.join(ANIM_DIR, preset["id"] + ".svg")
        render_animated(path(preset), out, preset.get("name", preset["id"]), fps=FPS)

    with open(README, encoding="utf-8") as f:
        text = f.read()
    with open(README, "w", encoding="utf-8") as f:
        f.write(update_readme(text, presets))
    print(f"wrote {len(presets)} animations to {ANIM_DIR} and updated {README}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
