"""Depth-based forward warp between two camera poses.

Each pixel of the source frame is back-projected with its depth and K0, moved by T (camera 0 to
camera i) and projected with Ki. Each target pixel keeps the nearest source point (z-buffer), and the
warp keeps that point's depth in camera i as a warped depth map.

Holes (pixels no source point reaches) are filled from the BACKGROUND side. For each hole the nearest
written pixel in each of the four directions (left, right, up, down) is a candidate, and the hole
takes the colour of the candidate with the LARGER depth, never the nearer one. Holes whose row and
column are both empty are filled in a later pass from the pixels already filled. Filled pixels are
then lightly blurred with a 3x3 mean over filled neighbours only, so written colours do not bleed
into the fill.

Pixel centres are at (u + 0.5, v + 0.5); K is in pixels, depth is z along the camera 0 axis.
"""

import numpy as np


def relative(cams, i):
    """Return the 4x4 transform from camera 0 to camera i (OpenCV axes)."""
    w0 = cams.frames[0].w2c
    return cams.frames[i].w2c @ np.linalg.inv(w0)


def hole_fraction(filled):
    """Fraction of pixels revealed by the move: the share of True in `filled`, in [0, 1].

    `filled` is the mask returned by warp(). It is True only for real disocclusions: pixels still
    empty after a 3x3 closing of the written mask. Sampling cracks are not counted.
    """
    return float(np.mean(np.asarray(filled, dtype=bool)))


def _nearest_written_in_rows(written):
    """For each pixel, the column of the nearest written pixel strictly left and strictly right.

    Missing sides are -1 (left) and w (right). Written pixels themselves are not candidates for
    themselves, so this is meant for hole pixels.
    """
    h, w = written.shape
    cols = np.broadcast_to(np.arange(w), (h, w))
    left = np.maximum.accumulate(np.where(written, cols, -1), axis=1)
    right = np.minimum.accumulate(np.where(written, cols, w)[:, ::-1], axis=1)[:, ::-1]
    # Shift so each pixel sees its neighbours, not itself.
    left_strict = np.concatenate([np.full((h, 1), -1), left[:, :-1]], axis=1)
    right_strict = np.concatenate([right[:, 1:], np.full((h, 1), w)], axis=1)
    return left_strict, right_strict


def _best_sources(depth, img, written):
    """Colour and depth of the background-most candidate for every pixel.

    Candidates are the nearest written pixels to the left, right, up and down. Returns
    (colour, depth, ok) where ok is True where at least one candidate exists.
    """
    h, w = written.shape
    rows = np.broadcast_to(np.arange(h)[:, None], (h, w))
    cols = np.broadcast_to(np.arange(w)[None, :], (h, w))

    left, right = _nearest_written_in_rows(written)
    up_t, down_t = _nearest_written_in_rows(written.T)  # along columns: rows above and below
    up = up_t.T
    down = down_t.T

    candidates = [
        (rows, left, (left >= 0)),
        (rows, right, (right < w)),
        (up, cols, (up >= 0)),
        (down, cols, (down < h)),
    ]
    scores, colours = [], []
    for src_r, src_c, valid in candidates:
        sr = np.clip(src_r, 0, h - 1)
        sc = np.clip(src_c, 0, w - 1)
        scores.append(np.where(valid, depth[sr, sc], -np.inf))
        colours.append(img[sr, sc])

    scores = np.stack(scores, axis=0)
    colours = np.stack(colours, axis=0)
    best = np.argmax(scores, axis=0)
    ok = np.max(scores, axis=0) > -np.inf
    best_colour = best.reshape((1,) + best.shape + (1,) * (colours.ndim - 3))
    chosen_colour = np.take_along_axis(colours, best_colour, axis=0)[0]
    chosen_depth = np.take_along_axis(scores, best[None, ...], axis=0)[0]
    return chosen_colour, chosen_depth, ok


def _blur_filled(img, filled):
    """3x3 mean over filled neighbours, applied to filled pixels only."""
    h, w = filled.shape
    f = filled.astype(np.float64)
    pad = [(1, 1), (1, 1)] + [(0, 0)] * (img.ndim - 2)
    fp = np.pad(f, [(1, 1), (1, 1)])
    ip = np.pad(img.astype(np.float64) * f.reshape(h, w, *([1] * (img.ndim - 2))), pad)
    num = np.zeros(img.shape, dtype=np.float64)
    den = np.zeros((h, w), dtype=np.float64)
    for dy in range(3):
        for dx in range(3):
            num += ip[dy:dy + h, dx:dx + w]
            den += fp[dy:dy + h, dx:dx + w]
    safe = np.where(den > 0, den, 1.0).reshape(h, w, *([1] * (img.ndim - 2)))
    blurred = num / safe
    keep = filled.reshape(h, w, *([1] * (img.ndim - 2))) & (den > 0).reshape(h, w, *([1] * (img.ndim - 2)))
    out = img.copy()
    out = np.where(keep, np.clip(np.round(blurred), 0, 255), img).astype(img.dtype)
    return out


def fill_holes(img, written, depth, blur=True):
    """Fill every pixel not in `written` from the background-side candidate, then blur the fill.

    depth: HxW depth of each written pixel (ignored where not written). blur=False keeps the fill
    exact, which depth maps need. Returns (filled_image, holes) where holes is True for the pixels
    that were filled. If nothing was written there is nothing to copy from: the image is returned
    unchanged and every pixel counts as a hole.
    """
    holes = ~written
    if not written.any():
        return img.copy(), holes
    out = img.copy()
    z = np.where(written, depth, 0.0).astype(np.float64)
    mask = written.copy()
    while not mask.all():
        colour, cdepth, ok = _best_sources(z, out, mask)
        new = ok & ~mask
        if not new.any():
            break
        out[new] = colour[new]
        z[new] = cdepth[new]
        mask = mask | new
    if blur and holes.any():
        out = _blur_filled(out, holes)
    return out, holes


def warp(image, depth, K0, Ki, T):
    """Warp an HxW(x3) image from camera 0 to camera i.

    image: HxWx3 uint8 (HxW also accepted). depth: HxW float, z along camera 0, > 0 where valid.
    K0, Ki: 3x3 pixel intrinsics. T: 4x4 camera 0 -> camera i. Returns (out, filled): out has the
    same shape as image, with every hole filled. filled is HxW bool: True only for real disocclusions,
    the pixels still empty after a 3x3 closing of the written mask (sampling cracks are filled but not
    reported). Use hole_fraction(filled) for the share revealed. A move with no holes returns the
    warped image unchanged. Each source point is splatted to its 2x2 target cells, so magnification
    up to about 2x leaves no cracks.
    """
    image = np.asarray(image)
    depth = np.asarray(depth, dtype=np.float64)
    h, w = depth.shape
    if image.shape[:2] != (h, w):
        raise ValueError("image and depth must have the same height and width")
    K0 = np.asarray(K0, dtype=np.float64)
    Ki = np.asarray(Ki, dtype=np.float64)
    T = np.asarray(T, dtype=np.float64)

    vs, us = np.nonzero(depth > 0)
    d = depth[vs, us]
    x0 = (us + 0.5 - K0[0, 2]) / K0[0, 0] * d
    y0 = (vs + 0.5 - K0[1, 2]) / K0[1, 1] * d
    pts = np.stack([x0, y0, d, np.ones_like(d)], axis=0)
    # T @ pts written as explicit sums: Accelerate's BLAS sets a spurious divide-by-zero flag on
    # this product, which -W error turns into a failure. The result is the same.
    q = np.empty_like(pts)
    for r in range(4):
        q[r] = T[r, 0] * pts[0] + T[r, 1] * pts[1] + T[r, 2] * pts[2] + T[r, 3] * pts[3]
    zq = q[2]
    front = zq > 1e-9
    zq = zq[front]
    src_r = vs[front]
    src_c = us[front]
    px = Ki[0, 0] * q[0, front] / zq + Ki[0, 2]
    py = Ki[1, 1] * q[1, front] / zq + Ki[1, 2]
    # Stage 1: each point writes its own cell (nearest point wins a cell, as before). An unmoved
    # image maps back to itself exactly. Stage 2: the 2x2 footprint around each point fills only
    # cells stage 1 left empty, so magnification up to about 2x leaves no cracks.
    point_id = np.arange(zq.size)
    own_x = np.floor(px).astype(np.int64)
    own_y = np.floor(py).astype(np.int64)
    own_dist = np.hypot(px - (own_x + 0.5), py - (own_y + 0.5))
    s1_flat, s1_pid, s1_z = _zwrite(own_x, own_y, zq, own_dist, point_id, w, h)

    written1 = np.zeros(h * w, dtype=bool)
    written1[s1_flat] = True
    fx = np.concatenate([np.floor(px - 0.5).astype(np.int64) + dx for dy in (0, 1) for dx in (0, 1)])
    fy = np.concatenate([np.floor(py - 0.5).astype(np.int64) + dy for dy in (0, 1) for dx in (0, 1)])
    fz = np.concatenate([zq] * 4)
    fpid = np.concatenate([point_id] * 4)
    fdist = np.hypot(np.concatenate([px] * 4) - (fx + 0.5), np.concatenate([py] * 4) - (fy + 0.5))
    free = (fx >= 0) & (fx < w) & (fy >= 0) & (fy < h)
    free[free] = ~written1[(fy * w + fx)[free]]
    s2_flat, s2_pid, s2_z = _zwrite(fx[free], fy[free], fz[free], fdist[free], fpid[free], w, h)

    flat_unique = np.concatenate([s1_flat, s2_flat])
    winners = np.concatenate([s1_pid, s2_pid])
    depths = np.concatenate([s1_z, s2_z])

    written = np.zeros(h * w, dtype=bool)
    written[flat_unique] = True
    written = written.reshape(h, w)
    warped_depth = np.zeros(h * w, dtype=np.float64)
    warped_depth[flat_unique] = depths
    warped_depth = warped_depth.reshape(h, w)

    # Backward mapping. Forward-splat only the depth to camera i (z-buffer), fill its holes from
    # the background side, then give every output pixel its own colour by sampling the source.
    if not written.any():
        return image.copy(), np.ones((h, w), dtype=bool)
    target_depth, holes = fill_holes(warped_depth, written, warped_depth, blur=False)
    sampled = _sample_backward(image, target_depth, K0, Ki, np.linalg.inv(T))
    # Hole pixels are background the source does not show: sampling there at their depth can land
    # on the occluder. Their colour comes from the background side (forward splat, then fill).
    splat = np.zeros_like(image)
    splat.reshape(h * w, *image.shape[2:])[flat_unique] = image[src_r[winners], src_c[winners]]
    hole_colour, _ = fill_holes(splat, written, warped_depth)
    out = np.where(holes.reshape(h, w, *([1] * (image.ndim - 2))), hole_colour, sampled)
    # Real disocclusions: pixels still empty after a 3x3 closing of the written mask. Smaller gaps
    # (sampling cracks) are filled too, but are not counted as revealed content.
    real = ~_closing3(written)
    return out, real


def _sample_backward(image, target_depth, K0, Ki, T_inv):
    """Colour for each output pixel: back-project with its depth and Ki, move into camera 0 with
    T_inv, project with K0, and sample the source with bilinear interpolation (clamped at edges)."""
    h, w = target_depth.shape
    d = target_depth
    vs, us = np.mgrid[0:h, 0:w].astype(np.float64)
    X = (us + 0.5 - Ki[0, 2]) / Ki[0, 0] * d
    Y = (vs + 0.5 - Ki[1, 2]) / Ki[1, 1] * d
    pts = np.stack([X.ravel(), Y.ravel(), d.ravel(), np.ones(h * w)], axis=0)
    q = np.empty_like(pts)
    for r in range(4):
        q[r] = T_inv[r, 0] * pts[0] + T_inv[r, 1] * pts[1] + T_inv[r, 2] * pts[2] + T_inv[r, 3] * pts[3]
    z0 = np.where(np.abs(q[2]) > 1e-12, q[2], 1e-12)
    u0 = (K0[0, 0] * q[0] / z0 + K0[0, 2]).reshape(h, w)
    v0 = (K0[1, 1] * q[1] / z0 + K0[1, 2]).reshape(h, w)
    # Pixel centres are at +0.5: the sample coordinate in index space is u0 - 0.5.
    sx = np.clip(u0 - 0.5, 0, w - 1)
    sy = np.clip(v0 - 0.5, 0, h - 1)
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    extra = (1,) * (image.ndim - 2)
    wx = (sx - x0).reshape(h, w, *extra)
    wy = (sy - y0).reshape(h, w, *extra)
    src = image.astype(np.float64)
    top = src[y0, x0] * (1 - wx) + src[y0, x1] * wx
    bottom = src[y1, x0] * (1 - wx) + src[y1, x1] * wx
    value = top * (1 - wy) + bottom * wy
    if np.issubdtype(image.dtype, np.integer):
        value = np.clip(np.round(value), 0, np.iinfo(image.dtype).max)
    return value.astype(image.dtype)


def _zwrite(tx, ty, zz, dist, pid, w, h):
    """Z-test writes: per target cell the nearest point wins; ties go to the point nearest its cell
    centre. Returns (flat cell indices, winning point ids, winning depths)."""
    inside = (tx >= 0) & (tx < w) & (ty >= 0) & (ty < h)
    tx, ty, zz, pid, dist = tx[inside], ty[inside], zz[inside], pid[inside], dist[inside]
    flat = ty * w + tx
    order = np.lexsort((dist, zz))
    flat_o = flat[order]
    flat_unique, first = np.unique(flat_o, return_index=True)
    chosen = order[first]
    return flat_unique, pid[chosen], zz[chosen]


def _dilate3(mask):
    h, w = mask.shape
    p = np.pad(mask, 1, mode="constant", constant_values=False)
    out = np.zeros_like(mask)
    for dy in range(3):
        for dx in range(3):
            out |= p[dy:dy + h, dx:dx + w]
    return out


def _erode3(mask):
    h, w = mask.shape
    p = np.pad(mask, 1, mode="edge")
    out = np.ones_like(mask)
    for dy in range(3):
        for dx in range(3):
            out &= p[dy:dy + h, dx:dx + w]
    return out


def _closing3(mask):
    """3x3 morphological closing: dilate then erode. Fills gaps up to two pixels wide."""
    return _erode3(_dilate3(mask))
