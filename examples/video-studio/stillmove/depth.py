"""Depth estimation behind one interface, and the registry of backends.

A backend turns an RGB image into a per-pixel depth map: HxW float32, every value finite and
greater than 0, z along +z in camera 0 (OpenCV axes). A real model plugs in as one subclass of
DepthBackend. The fakes in stillmove.backends.fake need no model.
"""
import numpy as np


class DepthBackend:
    name = "base"

    def depth(self, image):
        """image: HxWx3 uint8 -> HxW float32 depths, all > 0."""
        raise NotImplementedError


def check_depth(depth, shape):
    """Return depth as float32 after checking it matches the image shape (H, W) and is > 0."""
    arr = np.asarray(depth, dtype=np.float32)
    if arr.shape != tuple(shape):
        raise ValueError(f"depth shape {arr.shape} does not match the image {tuple(shape)}")
    if not np.all(np.isfinite(arr)) or np.any(arr <= 0):
        raise ValueError("depth must be finite and greater than 0")
    return arr


def smooth_depth(depth, image, median=5, bilateral=5, sigma_space=1.5, sigma_range=0.1):
    """Edge-aware smoothing of a depth map, applied once per image.

    A median filter (median x median window) removes speckle. A bilateral filter
    (bilateral x bilateral window) then averages depth with weights from distance and from the
    image's luminance, so a depth step is kept where the picture has an edge. Flat regions stay flat.
    image: HxW or HxWx3 (any dtype Pillow reads as uint8 or float in 0..255 or 0..1 scale by max).
    """
    depth = np.asarray(depth, dtype=np.float64)
    img = np.asarray(image, dtype=np.float64)
    if img.ndim == 3:
        # Explicit weighted sum: Accelerate BLAS flags a spurious divide-by-zero on matmul here.
        lum = 0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]
    else:
        lum = img
    top = lum.max() if lum.size else 1.0
    lum = lum / top if top > 0 else lum

    h, w = depth.shape
    r = median // 2
    padded = np.pad(depth, r, mode="edge")
    stack = np.stack([padded[dy:dy + h, dx:dx + w] for dy in range(median) for dx in range(median)])
    med = np.median(stack, axis=0)

    rb = bilateral // 2
    mp = np.pad(med, rb, mode="edge")
    lp = np.pad(lum, rb, mode="edge")
    num = np.zeros_like(med)
    den = np.zeros_like(med)
    for dy in range(bilateral):
        for dx in range(bilateral):
            oy, ox = dy - rb, dx - rb
            spatial = np.exp(-(oy * oy + ox * ox) / (2.0 * sigma_space ** 2))
            shifted_l = lp[dy:dy + h, dx:dx + w]
            weight = spatial * np.exp(-((shifted_l - lum) ** 2) / (2.0 * sigma_range ** 2))
            num += weight * mp[dy:dy + h, dx:dx + w]
            den += weight
    return num / den


# Every name get_backend accepts. Kept in one place so other commands can list them.
BACKEND_NAMES = ("da2-small", "gradient", "plane")


def get_backend(name):
    """A backend by name. Only the fakes are registered here."""
    from stillmove.backends import fake

    backends = {"plane": fake.PlaneBackend, "gradient": fake.GradientBackend}
    if name == "da2-small":
        # Imported here so the fakes never need onnxruntime.
        from stillmove.backends.depth_anything import DepthAnythingSmall

        return DepthAnythingSmall()
    if name not in backends:
        raise ValueError(
            f"unknown depth backend {name!r}; choose one of: {', '.join(sorted(backends) + ['da2-small'])}"
        )
    return backends[name]()
