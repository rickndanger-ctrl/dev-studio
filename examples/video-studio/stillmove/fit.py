"""Keep the subject in frame: shorten a planned camera move just enough (see FIT.md).

Poses here are OpenCV world-to-camera matrices, as stillmove.cameras keeps them. The camera-to-world
pose of frame i is inv(w2c_i). Frame 0's camera-to-world is the world the subject lives in.

scale_cameras(cams, s): the move, scaled toward frame 0 by s in [0, 1]. Position is interpolated
linearly; rotation is interpolated on the rotation vector (R0 exp(s log(R0^T Ri))); focal lengths are
interpolated; the principal point and the frame times are unchanged. s = 1 gives the original frames,
s = 0 holds frame 0.

subject_points(depth, alpha, fg_depth, step, K): the subject as 3D points in frame-0 OpenCV camera
coordinates. The intrinsics K (frame 0's pixel 3x3) are needed to place a pixel in space.

fit_move(cams, points, margin): the largest s whose move keeps the subject's bounding box at least
margin * width from the left and right edges and margin * height from the top and bottom, on every frame.
"""

import math
from collections import namedtuple

import numpy as np

from stillmove.cameras import Cameras, Frame

Fit = namedtuple("Fit", "scale worst_frame min_gap note")

FLAT_RATIO = 0.95   # the depth foreground must sit below 95% of the median depth to count as a subject
TOLERANCE = 0.005   # bisection stops when the interval is this narrow


def _skew(w):
    return np.array([[0.0, -w[2], w[1]], [w[2], 0.0, -w[0]], [-w[1], w[0], 0.0]])


def _rot_log(R):
    """Rotation vector of R (a rotation by less than 180 degrees)."""
    cos_t = np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0)
    theta = math.acos(cos_t)
    if theta < 1e-12:
        return np.zeros(3)
    sin_t = math.sin(theta)
    if abs(sin_t) < 1e-9:  # about 180 degrees: not a camera move this module supports
        return np.zeros(3)
    v = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return v * theta / (2.0 * sin_t)


def _rot_exp(w):
    theta = float(np.linalg.norm(w))
    if theta < 1e-12:
        return np.eye(3) + _skew(w)
    k = _skew(w / theta)
    return np.eye(3) + math.sin(theta) * k + (1.0 - math.cos(theta)) * (k @ k)


def _c2w(w2c):
    return np.linalg.inv(np.asarray(w2c, dtype=np.float64))


def _w2c(c2w):
    R, p = c2w[:3, :3], c2w[:3, 3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ p
    return out


def scale_cameras(cams, s):
    """The camera path scaled toward frame 0 by s in [0, 1] (see the module docstring)."""
    if not 0.0 <= s <= 1.0:
        raise ValueError("s must be between 0 and 1")
    f0 = cams.frames[0]
    if s == 1.0:
        frames = [Frame(t=f.t, K=np.array(f.K, dtype=np.float64), w2c=np.array(f.w2c, dtype=np.float64))
                  for f in cams.frames]
        return Cameras(fps=cams.fps, width=cams.width, height=cams.height, frames=frames)
    if s == 0.0:
        frames = [Frame(t=f.t, K=np.array(f0.K, dtype=np.float64), w2c=np.array(f0.w2c, dtype=np.float64))
                  for f in cams.frames]
        return Cameras(fps=cams.fps, width=cams.width, height=cams.height, frames=frames)
    c2w0 = _c2w(f0.w2c)
    p0, R0 = c2w0[:3, 3], c2w0[:3, :3]
    frames = []
    for f in cams.frames:
        ci = _c2w(f.w2c)
        pi, Ri = ci[:3, 3], ci[:3, :3]
        R = R0 @ _rot_exp(s * _rot_log(R0.T @ Ri))
        p = p0 + s * (pi - p0)
        c2w = np.eye(4)
        c2w[:3, :3] = R
        c2w[:3, 3] = p
        K = np.array(f.K, dtype=np.float64)
        K0 = np.array(f0.K, dtype=np.float64)
        K[0, 0] = K0[0, 0] + s * (K[0, 0] - K0[0, 0])
        K[1, 1] = K0[1, 1] + s * (K[1, 1] - K0[1, 1])
        frames.append(Frame(t=f.t, K=K, w2c=_w2c(c2w)))
    return Cameras(fps=cams.fps, width=cams.width, height=cams.height, frames=frames)


def _sample(mask, depth, fg_depth, step, K):
    """3D points (frame-0 OpenCV camera) for the mask's pixels: every step-th pixel, plus the outline."""
    if not mask.any():
        return None
    padded = np.pad(mask, 1, constant_values=False)
    interior = (padded[1:-1, 1:-1] & padded[:-2, 1:-1] & padded[2:, 1:-1]
                & padded[1:-1, :-2] & padded[1:-1, 2:])
    outline = mask & ~interior
    ys, xs = np.mgrid[0:mask.shape[0], 0:mask.shape[1]]
    chosen = (mask & (ys % step == 0) & (xs % step == 0)) | outline
    v_idx, u_idx = np.nonzero(chosen)
    if fg_depth is None:
        z = depth[v_idx, u_idx].astype(np.float64)
    else:
        z = np.full(u_idx.shape, float(fg_depth))
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    x = (u_idx + 0.5 - cx) * z / fx
    y = (v_idx + 0.5 - cy) * z / fy
    return np.stack([x, y, z], axis=1)


def subject_points(depth, alpha=None, fg_depth=None, step=4, *, K=None):
    """The subject as (N, 3) points in frame-0 OpenCV camera coordinates, or None (no subject).

    With alpha: the pixels with alpha > 0.5, every `step` pixels plus the mask's outline, at fg_depth
    when given (the flat foreground layer) else at their own depth. Without alpha: the depth
    foreground, the pixels nearer than the 25th percentile, but only when that is clearly nearer than
    the median (below 95% of it); a flat depth map has no subject and gives None.
    K: frame 0's pixel intrinsics (3x3). Required, to place pixels in space.
    """
    if K is None:
        raise ValueError("subject_points needs K, frame 0's pixel intrinsics, to place the pixels in space")
    K = np.asarray(K, dtype=np.float64)
    depth = np.asarray(depth, dtype=np.float64)
    if alpha is not None:
        mask = np.asarray(alpha) > 0.5
        return _sample(mask, depth, fg_depth, step, K)
    p25 = float(np.percentile(depth, 25))
    median = float(np.median(depth))
    if not p25 < FLAT_RATIO * median:
        return None
    return _sample(depth <= p25, depth, fg_depth, step, K)


def _gap(cams, points, s, margin):
    """(gap in pixels, worst frame): how far the subject's box is inside the margin on the worst frame.
    Negative means it crosses the margin. A point behind a camera counts as crossing."""
    cand = scale_cameras(cams, s)
    c2w0 = _c2w(cams.frames[0].w2c)
    world = (c2w0 @ np.concatenate([points, np.ones((len(points), 1))], axis=1).T).T[:, :3]
    W, H = cams.width, cams.height
    worst, worst_frame = math.inf, 0
    for i, f in enumerate(cand.frames):
        cam = (np.asarray(f.w2c) @ np.concatenate([world, np.ones((len(world), 1))], axis=1).T).T[:, :3]
        if np.any(cam[:, 2] <= 0):
            return -math.inf, i
        u = f.K[0, 0] * cam[:, 0] / cam[:, 2] + f.K[0, 2]
        v = f.K[1, 1] * cam[:, 1] / cam[:, 2] + f.K[1, 2]
        gap = min(u.min() - margin * W, W * (1.0 - margin) - u.max(),
                  v.min() - margin * H, H * (1.0 - margin) - v.max())
        if gap < worst:
            worst, worst_frame = float(gap), i
    return worst, worst_frame


def fit_move(cams, points, margin=0.06):
    """The largest scale s in [0, 1] whose move keeps the subject inside the margin on every frame.

    points: the subject from subject_points, or None (nothing to keep in frame: the move is left as planned).
    Returns Fit(scale, worst_frame, min_gap, note); min_gap is in pixels, negative means crossing.
    """
    if points is None or len(points) == 0:
        return Fit(1.0, None, None, "")
    pct = int(round(margin * 100))
    gap1, worst1 = _gap(cams, points, 1.0, margin)
    if gap1 >= 0:
        return Fit(1.0, worst1, gap1, "")
    gap0, worst0 = _gap(cams, points, 0.0, margin)
    if gap0 < 0:
        return Fit(1.0, worst0, gap0,
                   "subject is already within %d%% of the edge in the first frame; move left as planned" % pct)
    lo, hi = 0.0, 1.0
    while hi - lo > TOLERANCE:
        mid = (lo + hi) / 2.0
        if _gap(cams, points, mid, margin)[0] >= 0:
            lo = mid
        else:
            hi = mid
    gap, worst = _gap(cams, points, lo, margin)
    return Fit(lo, worst, gap,
               "move shortened to %d%% to keep the subject %d%% inside the frame" % (round(lo * 100), pct))
