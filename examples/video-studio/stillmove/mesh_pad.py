"""Padding for the mesh: outer rings so no edge of the frame shows while the camera moves.

The mesh is the still's grid. A camera move (and the orbit after it) can see past the grid's border;
without padding, the background shows as a hole at the edge. The frustum of every camera pose is
cast through its four corners and intersected with a backdrop plane at the mesh's far-edge depth.
Each hit is projected back into camera 0's pixels. The grid is then extended by whole rings of
vertices (same spacing, depth of the nearest edge vertex, UV clamped to the edge) until every hit
lies inside the padded grid, with a margin of 5% of the frame width or height.

Everything here is pure geometry. Poses are Blender world coordinates: a location and a 3x3 rotation
whose columns are the camera's x (right), y (up) and z (points away from the view). A camera looks
down its -z. Pixel coordinates are continuous: pixel k has its centre at k + 0.5, as in mesh.py.
"""

import math

import numpy as np

from stillmove import _cm

_cm.import_camera_motion()

from camera_motion.export.blender import MW  # noqa: E402
from camera_motion.export.quat import quat_to_mat  # noqa: E402

MARGIN = 0.05  # of the frame width (x) and height (y)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add_scaled(a, b, t):
    return (a[0] + t * b[0], a[1] + t * b[1], a[2] + t * b[2])


def _col(rotation, k):
    return (rotation[0][k], rotation[1][k], rotation[2][k])


def rotation_from_quat(quat_wxyz):
    """3x3 rotation from a Blender-order quaternion (w, x, y, z)."""
    w, x, y, z = quat_wxyz
    return quat_to_mat((x, y, z, w))


def frustum_rays(K, width, height, location, rotation):
    """The four corner rays (origin, direction) of a camera, in world coordinates.

    K is the pixel intrinsics. The corners are (0,0), (W,0), (W,H), (0,H). A pixel's OpenCV direction
    is ((u - cx) / fx, (v - cy) / fy, 1); in the camera's own axes that is (x, -y, -1).
    """
    fx, fy, cx, cy = K[0][0], K[1][1], K[0][2], K[1][2]
    rays = []
    for u, v in ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height)):
        local = ((u - cx) / fx, -(v - cy) / fy, -1.0)
        direction = tuple(_dot(_row(rotation, r), local) for r in range(3))
        rays.append((tuple(location), direction))
    return rays


def _row(rotation, r):
    return (rotation[r][0], rotation[r][1], rotation[r][2])


def far_depth(points, location0, rotation0):
    """The mesh's far-edge depth: the largest distance of a grid point in front of camera 0."""
    forward = tuple(-c for c in _col(rotation0, 2))
    return max(_dot(_sub(p, location0), forward) for p in points)


def backdrop_hits(rays, location0, rotation0, depth):
    """Where each ray meets the plane at `depth` in front of camera 0. Rays that miss are skipped."""
    forward = tuple(-c for c in _col(rotation0, 2))
    hits = []
    for origin, direction in rays:
        denominator = _dot(direction, forward)
        if abs(denominator) < 1e-12:
            continue
        t = (depth - _dot(_sub(origin, location0), forward)) / denominator
        if t > 0:
            hits.append(_add_scaled(origin, direction, t))
    return hits


def pixel_of(point, K0, location0, rotation0):
    """The continuous pixel (u, v) of a world point in camera 0, or None if it is not in front."""
    fx, fy, cx, cy = K0[0][0], K0[1][1], K0[0][2], K0[1][2]
    rel = _sub(point, location0)
    x = _dot(_col(rotation0, 0), rel)
    y = _dot(_col(rotation0, 1), rel)
    z = _dot(_col(rotation0, 2), rel)
    if z >= 0:
        return None
    return (fx * x / -z + cx, fy * (-y) / -z + cy)


def _steps(width, height, n, m):
    return (width - 1) / (n - 1), (height - 1) / (m - 1)


def required_rings(pixels, width, height, n, m, margin=MARGIN):
    """The fewest rings (rx, ry) so every pixel sits inside the padded grid with the margin.

    The grid's continuous extent is [0.5, W - 0.5] in x, with spacing (W - 1) / (n - 1). A ring adds
    one spacing on each side. The margin is a fraction of the frame size.
    """
    sx, sy = _steps(width, height, n, m)
    mx, my = margin * width, margin * height
    if not pixels:
        return 0, 0
    us = [p[0] for p in pixels]
    vs = [p[1] for p in pixels]
    need_x = max(0.5 + mx - min(us), max(us) + mx - (width - 0.5)) / sx
    need_y = max(0.5 + my - min(vs), max(vs) + my - (height - 0.5)) / sy
    return max(0, math.ceil(need_x - 1e-9)), max(0, math.ceil(need_y - 1e-9))


def covers(pixels, width, height, n, m, rx, ry, margin=0.0):
    """True if every pixel lies inside the grid padded by rx and ry rings, with the given margin."""
    sx, sy = _steps(width, height, n, m)
    mx, my = margin * width, margin * height
    lo_x, hi_x = 0.5 - rx * sx, (width - 0.5) + rx * sx
    lo_y, hi_y = 0.5 - ry * sy, (height - 0.5) + ry * sy
    return all(lo_x + mx <= u <= hi_x - mx and lo_y + my <= v <= hi_y - my for u, v in pixels)


def slab_points(K, width, height, location, rotation, loc0, rot0, dmin, dmax):
    """Points whose convex hull, projected into camera 0, contains the whole frustum between depths dmin and dmax.

    The frustum of a pose, cut to the slab dmin <= depth <= dmax in camera 0, is convex. Its corners
    are the apex (when it lies in the slab) and the four corner rays cut by the two planes at dmin and
    dmax. Projection keeps convexity, so these points cover every surface point between the two depths.
    """
    forward = tuple(-c for c in _col(rot0, 2))
    points = []
    apex_depth = _dot(_sub(location, loc0), forward)
    if dmin <= apex_depth <= dmax:
        points.append(tuple(location))
    for origin, direction in frustum_rays(K, width, height, location, rotation):
        denominator = _dot(direction, forward)
        if abs(denominator) < 1e-12:
            continue
        for depth in (dmin, dmax):
            t = (depth - _dot(_sub(origin, loc0), forward)) / denominator
            if t > 0:
                points.append(_add_scaled(origin, direction, t))
    return points


def plan_rings(cams, key_rows, base_points, n, m):
    """The padding (rx, ry) for a camera move plus its orbit.

    key_rows: (location, (w, x, y, z), fov) per frame (mesh._key_rows); base_points: every point of the
    layers (grid and, when present, the backdrop) in Blender coordinates. A surface point can be at any
    depth between the nearest and the farthest of these, so each pose's frustum is tested over that
    whole slab (slab_points), not on one plane. The points are projected into camera 0.
    """
    from stillmove import blender_preview

    loc0 = key_rows[0][0]
    rot0 = rotation_from_quat(key_rows[0][1])
    k0 = cams.frames[0].K
    forward = tuple(-c for c in _col(rot0, 2))
    depths = [_dot(_sub(p, loc0), forward) for p in base_points]
    dmin, dmax = min(depths), max(depths)
    poses = [(loc, rotation_from_quat(q), cams.frames[i].K) for i, (loc, q, _) in enumerate(key_rows)]
    orbit = blender_preview.orbit_poses(key_rows[-1][0])
    poses += [(loc, rotation_from_quat(q), cams.frames[-1].K) for loc, q in orbit]
    pixels = []
    for loc, rot, k in poses:
        for point in slab_points(k, cams.width, cams.height, loc, rot, loc0, rot0, dmin, dmax):
            px = pixel_of(point, k0, loc0, rot0)
            if px is not None:
                pixels.append(px)
    return required_rings(pixels, cams.width, cams.height, n, m)


def pad_vertices(cam0, depth, n, m, rx, ry):
    """Vertices of the padded grid in Blender coordinates, shape ((n+2rx) * (m+2ry), 3).

    Vertex (i, j) of the unpadded grid is the same as mesh.grid_vertices gives it. A ring vertex
    takes the depth of the nearest edge vertex, so the edge stretches outward. Index: row-major,
    j' * (n + 2 rx) + i', with i = i' - rx and j = j' - ry.
    """
    height, width = depth.shape
    sx, sy = _steps(width, height, n, m)
    fx, fy = cam0.K[0, 0], cam0.K[1, 1]
    cx, cy = cam0.K[0, 2], cam0.K[1, 2]
    c2w = np.linalg.inv(cam0.w2c)
    out = []
    for jp in range(m + 2 * ry):
        j = jp - ry
        cj = min(max(j, 0), m - 1)
        py_edge = cj * sy
        py = j * sy
        for ip in range(n + 2 * rx):
            i = ip - rx
            ci = min(max(i, 0), n - 1)
            px_edge = ci * sx
            px = i * sx
            z = float(depth[int(round(py_edge)), int(round(px_edge))])
            x_cam = (px + 0.5 - cx) * z / fx
            y_cam = (py + 0.5 - cy) * z / fy
            world = (c2w @ np.array([x_cam, y_cam, z, 1.0]))[:3]
            out.append(tuple(sum(MW[r][k] * world[k] for k in range(3)) for r in range(3)))
    return out


def pad_keep(keep, rx, ry):
    """The face mask for the padded grid: a ring face repeats the nearest edge face's keep flag.

    keep: (m - 1, n - 1) bool for the plain grid. Returns ((m-1) + 2ry, (n-1) + 2rx) bool, so a face of
    the padded grid is kept exactly when the edge face it lies beyond is kept.
    """
    rows, cols = keep.shape
    out = np.zeros((rows + 2 * ry, cols + 2 * rx), dtype=bool)
    for jp in range(rows + 2 * ry):
        j = min(max(jp - ry, 0), rows - 1)
        for ip in range(cols + 2 * rx):
            i = min(max(ip - rx, 0), cols - 1)
            out[jp, ip] = keep[j, i]
    return out


def pad_uvs(n, m, rx, ry):
    """UVs of the padded grid. Ring vertices are clamped to the edge, so they lie on the unit square's border."""
    uvs = []
    for jp in range(m + 2 * ry):
        j = jp - ry
        for ip in range(n + 2 * rx):
            i = ip - rx
            u = min(max(i / (n - 1), 0.0), 1.0)
            v = min(max(1.0 - j / (m - 1), 0.0), 1.0)
            uvs.append((u, v))
    return uvs
