"""Depth layers for the mesh: no rubber-sheet faces across a depth cut, and a backdrop behind it.

A grid face whose four corners sit on very different depths is a stretched sheet bridging a gap
between two surfaces. keep_faces drops those faces, so no face spans a cut. Where a cut is, the
far side is hidden behind the near side, so backdrop puts a second surface behind the cut: its
depth and picture are the far side's, copied into the foreground pixels from the background side.

Pure numpy. No Blender here.
"""

import numpy as np

from stillmove.layers import BACKGROUND_ALPHA

RATIO_LIMIT = 1.08  # a face is dropped when its corners' depths differ by more than this ratio (max / min)
FLOOD = 3  # pixels the foreground is grown past each cut edge, so the backdrop covers the hidden side

_SHIFTS = ((-1, 0), (1, 0), (0, -1), (0, 1))
BACKDROP_TEXTURE = "backdrop.png"
BACKDROP_MATERIAL = "backdrop"


def grid_depths(depth, n, m):
    """The depth at each grid vertex, shape (m, n), sampled as grid_vertices samples it."""
    height, width = depth.shape
    ys = [int(round(j * (height - 1) / (m - 1))) for j in range(m)]
    xs = [int(round(i * (width - 1) / (n - 1))) for i in range(n)]
    return np.asarray(depth, dtype=np.float64)[np.ix_(ys, xs)]


def keep_faces(depth, n, m, limit=RATIO_LIMIT):
    """Boolean (m-1, n-1): True where the face's four corners are within the ratio limit."""
    g = grid_depths(depth, n, m)
    corners = np.stack([g[:-1, :-1], g[:-1, 1:], g[1:, 1:], g[1:, :-1]])
    return corners.max(axis=0) / corners.min(axis=0) <= limit


def _neighbour(a, dy, dx, fill):
    """out[y, x] = a[y + dy, x + dx], with fill outside the picture."""
    pad = ((1, 1), (1, 1)) + ((0, 0),) * (a.ndim - 2)
    padded = np.pad(a, pad, mode="constant", constant_values=fill)
    h, w = a.shape[:2]
    return padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]


def foreground_mask(depth, matte=None, flood=FLOOD, limit=RATIO_LIMIT):
    """True where the foreground is: the near side of every depth cut, grown by `flood` pixels.

    matte is the hook for a subject matte (a boolean array of the same shape, True = foreground).
    When it is given it is used as it is and the depth cut is not looked at.
    """
    depth = np.asarray(depth, dtype=np.float64)
    if matte is not None:
        mask = np.asarray(matte, dtype=bool)
        if mask.shape != depth.shape:
            raise ValueError("the matte must have the depth's shape")
        return mask
    near = np.zeros(depth.shape, dtype=bool)
    for a, b, ia, ib in (
        (depth[:, :-1], depth[:, 1:], (slice(None), slice(None, -1)), (slice(None), slice(1, None))),
        (depth[:-1, :], depth[1:, :], (slice(None, -1), slice(None)), (slice(1, None), slice(None))),
    ):
        cut = np.maximum(a, b) / np.minimum(a, b) > limit
        a_is_near = a < b
        near[ia] |= cut & a_is_near
        near[ib] |= cut & ~a_is_near
    # Grow the near side across the surface it belongs to: neighbours whose depth is within the ratio
    # limit of a foreground pixel join it, so the whole near plane is foreground, not only its edge.
    while True:
        grown = near.copy()
        for dy, dx in _SHIFTS:
            nd = _neighbour(depth, dy, dx, 1.0)  # outside the picture: 1.0 (never reached: near is False there)
            similar = np.maximum(nd, depth) / np.minimum(nd, depth) <= limit
            grown |= _neighbour(near, dy, dx, False) & similar
        if np.array_equal(grown, near):
            break
        near = grown
    for _ in range(flood):
        grown = near.copy()
        for dy, dx in _SHIFTS:
            grown |= _neighbour(near, dy, dx, False)
        near = grown
    return near


def _fill_from_background(depth, rgb, mask):
    """Fill the masked pixels with the depth and colour of their farthest known neighbour, repeatedly
    (outward from the unmasked pixels, the way warp fills holes). Returns (depth, rgb) as float arrays."""
    d = np.asarray(depth, dtype=np.float64).copy()
    c = np.asarray(rgb, dtype=np.float64).copy()
    known = ~mask
    while not known.all():
        best_d = np.full(d.shape, -np.inf)
        best_c = np.zeros(c.shape)
        has = np.zeros(d.shape, dtype=bool)
        for dy, dx in _SHIFTS:
            nk = _neighbour(known, dy, dx, False)
            nd = _neighbour(d, dy, dx, 0.0)
            nc = _neighbour(c, dy, dx, 0.0)
            better = nk & (nd > best_d)
            best_d = np.where(better, nd, best_d)
            best_c = np.where(better[..., None], nc, best_c)
            has |= nk
        newly = ~known & has
        if not newly.any():
            raise ValueError("no unmasked pixel to fill from")
        d = np.where(newly, best_d, d)
        c = np.where(newly[..., None], best_c, c)
        known |= newly
    return d, c


def backdrop(depth, rgb, matte=None):
    """The layer behind the cut, or None when there is no cut, or no far side for it.

    Returns (backdrop_depth, backdrop_rgb uint8). backdrop_depth is the depth map with the foreground
    replaced by the far side's depth; backdrop_rgb is the picture with the foreground replaced by the
    far side's colour.
    """
    mask = foreground_mask(depth, matte)
    if not mask.any() or mask.all():  # no cut, or no far side to put behind it
        return None
    d, c = _fill_from_background(depth, rgb, mask)
    return d, np.clip(np.rint(c), 0, 255).astype(np.uint8)


FOREGROUND_ALPHA = 0.5  # with a matte, a pixel at or above this alpha is foreground
FOREGROUND_LIFT = 0.25  # the foreground stands this far (view-axis units) in front of the backdrop plane


def foreground_bounds(alpha):
    """(x0, y0, x1, y1): the continuous pixel box (x1, y1 exclusive) of the matte's foreground."""
    ys, xs = np.nonzero(np.asarray(alpha) >= FOREGROUND_ALPHA)
    if xs.size == 0:
        raise ValueError("the matte has no foreground, so there is nothing to place")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def foreground_depth(plane, alpha, near):
    """The foreground's depth: the layer's near depth, or FOREGROUND_LIFT in front of the backdrop plane at the
    foreground's centre, whichever is nearer.

    Layers' near depth is a percentile of the depth estimate inside the matte. On a flat logo that estimate
    can read deeper than the paper it is printed on, which would hide the logo behind the backdrop. The plane
    is the backdrop's depth, so the foreground is kept in front of it.
    """
    x0, y0, x1, y1 = foreground_bounds(alpha)
    a, b, c = plane
    inverse = a * (x0 + x1) / 2.0 + b * (y0 + y1) / 2.0 + c
    if inverse <= 0:
        raise ValueError("the backdrop plane is behind the camera at the foreground")
    return min(float(near), 1.0 / inverse - FOREGROUND_LIFT)


def smooth_plate(plate, alpha, limit=BACKGROUND_ALPHA):
    """The backdrop picture: the plate with the matte's foreground replaced by a smooth quadratic fitted to the
    background (alpha below limit), per colour channel.

    The plate's refill copies neighbouring paper pixel by pixel, which leaves letter-shaped patches. A smooth
    surface has none; the paper is a gentle gradient, so a quadratic follows it.
    """
    plate = np.asarray(plate)
    h, w = plate.shape[:2]
    ys, xs = np.nonzero(np.asarray(alpha) < limit)
    if xs.size < 6:
        raise ValueError("too few background pixels to fit the backdrop picture")

    def design(x, y):
        u = (x + 0.5) / w * 2.0 - 1.0
        v = (y + 0.5) / h * 2.0 - 1.0
        return np.stack([np.ones_like(u), u, v, u * u, u * v, v * v], axis=-1)

    fit_rows = design(xs, ys)
    all_rows = design(*np.meshgrid(np.arange(w), np.arange(h)))
    out = plate.astype(np.float64).copy()
    foreground = np.asarray(alpha) >= limit
    for c in range(plate.shape[2]):
        coef, *_ = np.linalg.lstsq(fit_rows, plate[ys, xs, c].astype(np.float64), rcond=None)
        surface = all_rows @ coef
        out[..., c] = np.where(foreground, surface, out[..., c])
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def background_plane(depth, alpha, limit=BACKGROUND_ALPHA):
    """The backdrop plane, least-squares fitted to the background (alpha below limit).

    Returns (a, b, c) with 1 / z = a * qx + b * qy + c, where qx, qy are continuous pixel coordinates
    (pixel k has its centre at k + 0.5). A plane in camera space is exactly linear in 1 / z, so every
    point back-projected from this plane lies on one plane.
    """
    ys, xs = np.nonzero(np.asarray(alpha) < limit)
    if xs.size < 3:
        raise ValueError("too few background pixels to fit a backdrop plane")
    z = np.asarray(depth, dtype=np.float64)[ys, xs]
    A = np.stack([xs + 0.5, ys + 0.5, np.ones(xs.size)], axis=1)
    coef, *_ = np.linalg.lstsq(A, 1.0 / z, rcond=None)
    return float(coef[0]), float(coef[1]), float(coef[2])
