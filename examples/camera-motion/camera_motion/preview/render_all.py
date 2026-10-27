"""Render every preset in a catalog to an SVG preview, plus an index page.

Usage (from camera-motion/):
    python3 -m camera_motion.preview.render_all [catalog.json] [-o dir]
"""
import argparse
import html
import json
import os
import sys

from camera_motion.engine.camera_path import path
from camera_motion.preview.svg_path import render

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = os.path.join(HERE, "sample_catalog.json")
DEFAULT_OUT = os.path.join(HERE, "out")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Camera motion previews</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #fff; color: #111; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
figure {{ margin: 0; }}
img {{ width: 100%; height: auto; border: 1px solid #ddd; }}
figcaption {{ margin-top: 4px; font-weight: 600; }}
figcaption span {{ display: block; font-weight: 400; font-size: 0.9em; color: #555; }}
</style>
</head>
<body>
<h1>Camera motion previews</h1>
<div class="grid">
{items}
</div>
</body>
</html>
"""


def _index_html(presets):
    items = []
    for preset in presets:
        items.append(
            '<figure><img src="{src}" alt="{name}">'
            "<figcaption>{name}<span>{desc}</span></figcaption></figure>".format(
                src=html.escape(preset["id"] + ".svg"),
                name=html.escape(preset["name"]),
                desc=html.escape(preset.get("description", "")),
            )
        )
    return _PAGE.format(items="\n".join(items))


def main(catalog_path=DEFAULT_CATALOG, out_dir=DEFAULT_OUT):
    """Render each preset to <out_dir>/<id>.svg and write <out_dir>/index.html."""
    with open(catalog_path) as f:
        presets = json.load(f)
    os.makedirs(out_dir, exist_ok=True)
    for preset in presets:
        frames = path(preset)
        render(frames, os.path.join(out_dir, preset["id"] + ".svg"), preset["name"])
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w") as f:
        f.write(_index_html(presets))
    return index_path


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Render camera-motion preset previews.")
    parser.add_argument("catalog", nargs="?", default=DEFAULT_CATALOG, help="catalog JSON file")
    parser.add_argument("-o", "--out", dest="out_dir", default=DEFAULT_OUT, help="output directory")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])
    print(main(args.catalog, args.out_dir))
