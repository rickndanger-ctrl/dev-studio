"""Render a camera path as an SVG: a plan view (x, z) and a side view (z, y).

Run from camera-motion/:  python3 -m camera_motion.preview.svg_path preset.json [-o out.svg]
"""
import argparse
import json
import math
import os
import sys
from xml.sax.saxutils import escape

from camera_motion.engine.camera_path import path

PANEL_W = 400
PANEL_H = 400
TITLE_H = 36
PAD = 30
TICKS = 8
TICK_FRACTION = 0.2  # look-direction tick length, as a share of the scene reach

# (label, horizontal axis index, vertical axis index, vertical sign). Plan: z points down.
# Side: y points up.
PANELS = (
    ("plan (x, z)", 0, 2, 1),
    ("side (z, y)", 2, 1, -1),
)


def frames_for(preset):
    """Frames for a catalog or CLI preset, using its duration_s and fps when given."""
    return path(preset, duration_s=preset.get("duration_s", 3.0), fps=preset.get("fps", 24))


def look_direction(rotation):
    """Unit view direction for rotation [yaw, pitch, roll] in degrees. Yaw 0 looks along -z."""
    yaw, pitch = math.radians(rotation[0]), math.radians(rotation[1])
    return (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch))


def _reach(frames):
    return max(1.0, max(abs(c) for f in frames for c in f["position"])) * 1.3


def _screen(p, panel, ox, scale):
    _, h, v, vsign = panel
    return (ox + PANEL_W / 2 + p[h] * scale, TITLE_H + PANEL_H / 2 + vsign * p[v] * scale)


def _panel(frames, panel, ox, reach, scale):
    label, h, v, vsign = panel
    cx, cy = _screen((0.0, 0.0, 0.0), panel, ox, scale)
    parts = [
        f'<rect class="frame" x="{ox + 1}" y="{TITLE_H + 1}" width="{PANEL_W - 2}" '
        f'height="{PANEL_H - 2}" fill="none" stroke="#999"/>',
        f'<text class="label" x="{ox + 10}" y="{TITLE_H + 18}">{escape(label)}</text>',
        f'<path class="subject" d="M {cx - 6:.2f} {cy:.2f} L {cx + 6:.2f} {cy:.2f} '
        f'M {cx:.2f} {cy - 6:.2f} L {cx:.2f} {cy + 6:.2f}" stroke="#333" stroke-width="1.5"/>',
    ]

    points = []
    for f in frames:
        x, y = _screen(f["position"], panel, ox, scale)
        points.append(f"{x:.2f},{y:.2f}")
    parts.append(
        f'<polyline class="path" points="{" ".join(points)}" fill="none" '
        f'stroke="#1f5fa8" stroke-width="2"/>'
    )

    step = max(1, len(frames) // TICKS)
    tick_len = TICK_FRACTION * reach
    for i in sorted(set(range(0, len(frames), step)) | {len(frames) - 1}):
        p = frames[i]["position"]
        look = look_direction(frames[i]["rotation"])
        q = list(p)
        q[h] += look[h] * tick_len
        q[v] += look[v] * tick_len
        x1, y1 = _screen(p, panel, ox, scale)
        x2, y2 = _screen(q, panel, ox, scale)
        parts.append(
            f'<line class="look" x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="#c77d00" stroke-width="1.5"/>'
        )

    sx, sy = _screen(frames[0]["position"], panel, ox, scale)
    ex, ey = _screen(frames[-1]["position"], panel, ox, scale)
    parts.append(f'<circle class="start" cx="{sx:.2f}" cy="{sy:.2f}" r="5" fill="green"/>')
    parts.append(f'<circle class="end" cx="{ex:.2f}" cy="{ey:.2f}" r="5" fill="red"/>')
    return "\n".join(parts)


def render(frames, out_path, title):
    """Write the two-panel SVG for frames to out_path and return out_path."""
    if not frames:
        raise ValueError("no frames to render")
    reach = _reach(frames)
    scale = (PANEL_W / 2 - PAD) / reach
    width = PANEL_W * len(PANELS)
    height = TITLE_H + PANEL_H
    body = [
        f'<text class="title" x="10" y="24" font-family="sans-serif" font-size="16">'
        f"{escape(title)}</text>"
    ]
    for i, panel in enumerate(PANELS):
        body.append(_panel(frames, panel, i * PANEL_W, reach, scale))
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )
    folder = os.path.dirname(out_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render a camera preset as an SVG preview.")
    parser.add_argument("preset", help="preset JSON file")
    parser.add_argument("-o", "--out", help="output SVG (default: <id>.svg)")
    args = parser.parse_args(argv)
    with open(args.preset, encoding="utf-8") as f:
        preset = json.load(f)
    pid = preset.get("id") or os.path.splitext(os.path.basename(args.preset))[0]
    out = args.out or f"{pid}.svg"
    print(render(frames_for(preset), out, title=pid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
