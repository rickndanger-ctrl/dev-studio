"""Two-layer still move: a background plate and one foreground layer, composited frame by frame.

The background is the plate warped with the depth map. The plate is the original picture, except
that the matte's core (alpha of 1) is refilled from background pixels only, so no foreground
colour can reach a hole.

The foreground is the picture with its alpha, lying on one flat plane at a near depth: the 10th
percentile of depth inside the matte. A flat plane is warped backward, not forward, so no pinholes
open in it. For each target pixel the plane's homography gives the source pixel, and RGB and alpha
are sampled there together (bilinear, premultiplied). Outside the source, alpha is 0.

Each frame is

    out = fg * a + bg * (1 - a)

where a is the sampled foreground alpha.

A matte is any object with name and alpha(rgb) -> HxW float in 0..1, such as stillmove.matte's
get_matte("fake") or get_matte("isnet").
"""

import warnings

import numpy as np

from stillmove import warp

NEAR_PERCENTILE = 10  # the flat foreground sits at this depth percentile inside the matte
INSIDE = 0.5          # pixels with alpha above this count as inside the matte for that depth
BACKGROUND_ALPHA = 0.02  # alpha below this is background; the plate is filled from these pixels only
VIEW_AXIS = np.array([0.0, 0.0, 1.0])  # frame-0 view axis: the plane is n^T X = near, in camera 0


def _plate(image, alpha, depth):
    """The clean background: every pixel with alpha of at least BACKGROUND_ALPHA is refilled from
    pixels with alpha below it. Thin, anti-aliased foreground edges therefore do not stay in the
    background, where they would double when the foreground moves."""
    background = alpha < BACKGROUND_ALPHA
    if not background.any():
        raise ValueError("the matte covers the whole picture, so there is no background to fill from")
    # fill_holes copies from the background side (larger depth); the matte decides what is a hole.
    filled, _ = warp.fill_holes(image, background, depth, blur=False)
    # fill_holes can return a transposed view. warp.warp reshapes its output, and a reshape of a
    # non-contiguous array copies it, so the warped background would come out all zeros.
    return np.ascontiguousarray(filled)


def _foreground_colour(image, alpha, plate):
    """The foreground colour F from the matting equation I = a F + (1 - a) B, with B the plate:
    F = (I - (1 - a) B) / a, where a is at least BACKGROUND_ALPHA. Below that F is set to the plate,
    so it adds nothing over the background.

    F is not clipped. The plate is refilled at soft edges, so B can differ from the true background
    there by a few levels, and F can then fall just outside 0..255. Clipping it would break the
    identity: out = F a + B (1 - a) equals I exactly only while F is unclipped. The output is clipped
    once, after the composite."""
    I = image.astype(np.float64)
    B = plate.astype(np.float64)
    a = np.asarray(alpha, dtype=np.float64)[..., None]
    solid = a[..., 0] >= BACKGROUND_ALPHA
    safe_a = np.where(a >= BACKGROUND_ALPHA, a, 1.0)
    F = (I - (1.0 - a) * B) / safe_a
    return np.where(solid[..., None], F, B)


def _near_depth(depth, alpha):
    inside = alpha > INSIDE
    if not inside.any():
        raise ValueError("the matte is empty, so there is no foreground to place")
    return float(np.percentile(depth[inside], NEAR_PERCENTILE))


def _fg_depth(fg_depth, depth, alpha):
    """The foreground plane's depth when the caller sets it. Refused if not positive; warned about
    if it is not nearer than the background's median depth (then the foreground is not in front)."""
    if not (np.isfinite(fg_depth) and fg_depth > 0):
        raise ValueError("fg_depth must be a number greater than 0")
    if not (alpha > INSIDE).any():
        raise ValueError("the matte is empty, so there is no foreground to place")
    background = alpha < INSIDE
    if background.any():
        background_median = float(np.median(depth[background]))
        if fg_depth >= background_median:
            warnings.warn(
                f"fg_depth {fg_depth:g} is not nearer than the background's median depth "
                f"{background_median:g}, so the foreground will not be in front of it",
                stacklevel=3,
            )
    return float(fg_depth)


def plane_homography(K0, K1, transform, near):
    """Homography from camera-0 pixels to camera-i pixels for the plane n^T X = near (n = VIEW_AXIS).

    For X on the plane, X_i = R X + t and X = near * ray, so p_i ~ K1 (R + t n^T / near) K0^-1 p0.
    transform is the 4x4 camera 0 -> camera i. Pixel coordinates are continuous: the centre of
    pixel (u, v) is (u + 0.5, v + 0.5), as in warp.py.
    """
    transform = np.asarray(transform, dtype=np.float64)
    R, t = transform[:3, :3], transform[:3, 3]
    return np.asarray(K1, dtype=np.float64) @ (R + np.outer(t, VIEW_AXIS) / near) @ np.linalg.inv(
        np.asarray(K0, dtype=np.float64))


def sample_backward(source, H):
    """Sample an HxWxC source at every target pixel through H (target -> source, continuous coords).

    Bilinear. A target pixel whose source point falls outside the source, or behind the camera,
    gets zeros. Returns (sampled HxWxC, inside HxW bool).
    """
    source = np.asarray(source, dtype=np.float64)
    h, w = source.shape[:2]
    H_inv = np.linalg.inv(H)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    pts = np.stack([xs + 0.5, ys + 0.5, np.ones_like(xs)], axis=-1)
    src = pts @ H_inv.T
    front = src[..., 2] > 1e-12
    safe_w = np.where(front, src[..., 2], 1.0)
    sx = src[..., 0] / safe_w - 0.5
    sy = src[..., 1] / safe_w - 0.5
    inside = front & (sx >= 0) & (sx <= w - 1) & (sy >= 0) & (sy <= h - 1)
    sx = np.where(inside, sx, 0.0)
    sy = np.where(inside, sy, 0.0)
    x0 = np.clip(np.floor(sx).astype(np.int64), 0, w - 1)
    y0 = np.clip(np.floor(sy).astype(np.int64), 0, h - 1)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (sx - x0)[..., None]
    fy = (sy - y0)[..., None]
    top = source[y0, x0] * (1 - fx) + source[y0, x1] * fx
    bottom = source[y1, x0] * (1 - fx) + source[y1, x1] * fx
    out = top * (1 - fy) + bottom * fy
    out[~inside] = 0.0
    return out, inside


class Layers:
    """The per-picture data for a layered move: built once, then composed for each frame."""

    def __init__(self, image, depth, alpha, fg_depth=None):
        """fg_depth: the foreground plane's depth. None places it at the 10th depth percentile inside
        the matte (the default); a number places it there instead."""
        image = np.asarray(image, dtype=np.uint8)
        alpha = np.asarray(alpha, dtype=np.float32)
        if alpha.shape != image.shape[:2]:
            raise ValueError("the matte must be HxW, the same size as the picture")
        if not np.all(np.isfinite(alpha)):
            raise ValueError("the matte has values that are not finite")
        alpha = np.clip(alpha, 0.0, 1.0)
        self.alpha = alpha
        self.depth = np.asarray(depth, dtype=np.float32)
        self.plate = _plate(image, alpha, depth)
        self.near = (_near_depth(self.depth, alpha) if fg_depth is None
                     else _fg_depth(float(fg_depth), self.depth, alpha))
        # Foreground colour from the matting equation, then premultiplied with alpha, so bilinear
        # sampling does not bleed background colour into the edge of the foreground.
        colour = _foreground_colour(image, alpha, self.plate)
        self.fg_premult = np.dstack([colour * alpha[..., None], alpha.astype(np.float64)])

    def _sample_foreground(self, cam0, frame, transform):
        """(rgb HxWx3 float, alpha HxW float) of the foreground in the target camera."""
        H = plane_homography(cam0.K, frame.K, transform, self.near)
        sampled, _ = sample_backward(self.fg_premult, H)
        a = np.clip(sampled[..., 3], 0.0, 1.0)
        rgb = np.where(a[..., None] > 0, sampled[..., :3] / np.maximum(a, 1e-12)[..., None], 0.0)
        return rgb, a

    def foreground_alpha(self, cam0, frame, transform):
        """The sampled foreground alpha, HxW float32 in 0..1."""
        _, a = self._sample_foreground(cam0, frame, transform)
        return a.astype(np.float32)

    def compose(self, cam0, frame, transform):
        """One composited frame, uint8 HxWx3, for the camera frame `frame` (transform from cam0)."""
        bg, _ = warp.warp(self.plate, self.depth, cam0.K, frame.K, transform)
        rgb, a = self._sample_foreground(cam0, frame, transform)
        a = a[..., None]
        out = rgb * a + bg.astype(np.float64) * (1.0 - a)
        return np.clip(np.rint(out), 0, 255).astype(np.uint8)
