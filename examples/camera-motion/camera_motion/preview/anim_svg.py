"""Animated SVG preview with two panels: top-down (left) and side (right).

Top-down: screen x = world x, screen y = world z (z down the screen). The camera triangle
points up the screen (world -z). One animateMotion moves it along the path. Its inner
rotate is -yaw, because SVG rotate is clockwise and positive yaw turns left.

Side: screen x = world z, screen y = -world y (y up). The camera marker points screen-left
(world -z) at pitch 0. Its position is an animateTransform translate, and its inner rotate
is +pitch, so positive pitch tips the nose up.

Static fallback: viewers without SMIL draw each marker at frame 0. The top-down group has
transform translate(x0 y0) and its animateMotion path is relative to (x0, y0). The side
group's animated translate replaces its transform, so its absolute values need no offset.
Both inner groups carry their frame-0 rotation.
"""
import math
import os
from xml.sax.saxutils import escape

CANVAS = 480
PAD = 40
WIDTH = 2 * CANVAS
CAMERA_POINTS = "0,-8 5,6 -5,6"  # top-down: apex up the screen (world -z)
SIDE_POINTS = "-8,0 6,-5 6,5"  # side: apex screen-left (world -z)


def _f2(value):
    return f"{value + 0.0:.2f}"  # + 0.0 turns -0.0 into 0.0


def _f6(value):
    return f"{value + 0.0:.6f}"


def _arc_fractions(points):
    """Cumulative path-length fraction at each point, from 0 to 1."""
    lengths = [0.0]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.hypot(x1 - x0, y1 - y0))
    total = lengths[-1]
    if total == 0:
        return [0.0] * len(points)
    return [length / total for length in lengths]


def _bounds(values):
    """Low and high bounds of values, with the origin included."""
    return min(values + [0.0]), max(values + [0.0])


def render_animated(frames, out_path, title, fps=24):
    """Write the two-panel animated SVG for frames to out_path and return out_path.

    Keyframes follow frame timing: keyTimes are i / (N - 1), keyPoints are the top-down
    path-length fractions, and dur is (N - 1) / fps seconds. Both panels use these keyTimes.
    """
    n = len(frames)
    if n < 2:
        raise ValueError("need at least two frames to animate")

    xs = [f["position"][0] for f in frames]
    zs = [f["position"][2] for f in frames]
    ys = [f["position"][1] for f in frames]

    # Top-down panel: x and z fit in one square, centred on the panel.
    min_x, max_x = _bounds(xs)
    min_z, max_z = _bounds(zs)
    span = max(max_x - min_x, max_z - min_z, 1.0)
    scale = (CANVAS - 2 * PAD) / span
    mid_x, mid_z = (min_x + max_x) / 2, (min_z + max_z) / 2

    def top(x, z):
        return (CANVAS / 2 + (x - mid_x) * scale, CANVAS / 2 + (z - mid_z) * scale)

    top_pts = [top(x, z) for x, z in zip(xs, zs)]
    top_pts = [(round(x, 2) + 0.0, round(y, 2) + 0.0) for x, y in top_pts]
    x0, y0 = top_pts[0]
    # Relative to the static translate, so the SMIL motion starts at (0, 0).
    top_path = "M 0.00 0.00 L " + " L ".join(
        f"{x - x0 + 0.0:.2f} {y - y0 + 0.0:.2f}" for x, y in top_pts[1:]
    )
    key_points = ";".join(_f6(p) for p in _arc_fractions(top_pts))
    key_times = ";".join(_f6(i / (n - 1)) for i in range(n))
    dur = f"{(n - 1) / fps:g}s"
    yaw_values = ";".join(_f2(-f["rotation"][0]) for f in frames)
    tcx, tcy = top(0.0, 0.0)

    # Side panel: z fits horizontally, y fits vertically (up is -screen y), origin included.
    min_zs, max_zs = _bounds(zs)
    min_ys, max_ys = _bounds(ys)
    span_s = max(max_zs - min_zs, max_ys - min_ys, 1.0)
    scale_s = (CANVAS - 2 * PAD) / span_s
    mid_zs, mid_ys = (min_zs + max_zs) / 2, (min_ys + max_ys) / 2

    def side(z, y):
        return (CANVAS + CANVAS / 2 + (z - mid_zs) * scale_s,
                CANVAS / 2 - (y - mid_ys) * scale_s)

    side_pts = [side(z, y) for z, y in zip(zs, ys)]
    side_pts = [(round(x, 2) + 0.0, round(y, 2) + 0.0) for x, y in side_pts]
    sx0, sy0 = side_pts[0]
    side_path = " ".join(f"{x:.2f},{y:.2f}" for x, y in side_pts)
    scx, scy = side(0.0, 0.0)
    translate_values = ";".join(f"{_f2(x)} {_f2(y)}" for x, y in side_pts)
    pitch_values = ";".join(_f2(f["rotation"][1]) for f in frames)

    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{CANVAS}" '
        f'viewBox="0 0 {WIDTH} {CANVAS}">\n'
        f'<rect width="{WIDTH}" height="{CANVAS}" fill="#fff"/>\n'
        f'<text x="10" y="22" font-family="sans-serif" font-size="14">{escape(title)}</text>\n'
        f'<text x="10" y="40" font-family="sans-serif" font-size="12" fill="#666">top</text>\n'
        f'<text x="{CANVAS + 10}" y="40" font-family="sans-serif" font-size="12" fill="#666">side</text>\n'
        f'<line x1="{CANVAS}" y1="0" x2="{CANVAS}" y2="{CANVAS}" stroke="#ddd"/>\n'
        f'<polyline class="path" points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in top_pts)}" '
        'fill="none" stroke="#ccc" stroke-width="2"/>\n'
        f'<path class="subject" d="M {_f2(tcx - 6)} {_f2(tcy)} L {_f2(tcx + 6)} {_f2(tcy)} '
        f'M {_f2(tcx)} {_f2(tcy - 6)} L {_f2(tcx)} {_f2(tcy + 6)}" stroke="#333" stroke-width="1.5"/>\n'
        f'<polyline class="path-side" points="{side_path}" fill="none" stroke="#ccc" stroke-width="2"/>\n'
        f'<path class="subject-side" d="M {_f2(scx - 6)} {_f2(scy)} L {_f2(scx + 6)} {_f2(scy)} '
        f'M {_f2(scx)} {_f2(scy - 6)} L {_f2(scx)} {_f2(scy + 6)}" stroke="#333" stroke-width="1.5"/>\n'
        f'<g id="cam" transform="translate({_f2(x0)} {_f2(y0)})">\n'
        f'<animateMotion path="{top_path}" dur="{dur}" repeatCount="indefinite" calcMode="linear" '
        f'keyPoints="{key_points}" keyTimes="{key_times}"/>\n'
        f'<g transform="rotate({_f2(-frames[0]["rotation"][0])})">\n'
        f'<animateTransform attributeName="transform" type="rotate" values="{yaw_values}" '
        f'keyTimes="{key_times}" dur="{dur}" repeatCount="indefinite" calcMode="linear"/>\n'
        f'<polygon points="{CAMERA_POINTS}" fill="#c0392b"/>\n'
        "</g>\n"
        "</g>\n"
        f'<g id="cam-side" transform="translate({_f2(sx0)} {_f2(sy0)})">\n'
        f'<animateTransform attributeName="transform" type="translate" values="{translate_values}" '
        f'keyTimes="{key_times}" dur="{dur}" repeatCount="indefinite" calcMode="linear"/>\n'
        f'<g transform="rotate({_f2(frames[0]["rotation"][1])})">\n'
        f'<animateTransform attributeName="transform" type="rotate" values="{pitch_values}" '
        f'keyTimes="{key_times}" dur="{dur}" repeatCount="indefinite" calcMode="linear"/>\n'
        f'<polygon points="{SIDE_POINTS}" fill="#c0392b"/>\n'
        "</g>\n"
        "</g>\n"
        "</svg>\n"
    )

    folder = os.path.dirname(out_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    return out_path
