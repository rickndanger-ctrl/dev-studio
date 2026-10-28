"""Keep colours: the learned picture's luminance (one global tone curve), colour from a plain resize.

A learned upscaler sharpens by changing luminance, and its overall brightness and contrast drift from the
source. keep_colours takes:
  Y   from the learned picture, corrected by one global, monotone tone curve (fit_tone_curve) that makes the
      learned picture, reduced to the source's size, match the source's luminance distribution. No local
      terms: no halo is added and the edges keep the learned picture's sharpness.
  Cb,Cr from the Lanczos picture, with each channel's mean and standard deviation matched to the source's.

delta_e76 is the CIE 1976 colour difference in Lab (D65), used by the tests to say how close two colours are.
"""
import numpy as np
from PIL import Image

_WHITE = np.array([0.95047, 1.0, 1.08883])
LEVELS = 256


def rgb_to_ycbcr(rgb):
    """uint8 or float RGB (0-255) to float32 Y, Cb, Cr (BT.601, full range, as in JPEG)."""
    x = np.asarray(rgb, dtype=np.float32)
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0
    return y, cb, cr


def ycbcr_to_rgb(y, cb, cr):
    """Float Y, Cb, Cr to uint8 RGB, clipped to 0-255."""
    r = y + 1.402 * (cr - 128.0)
    g = y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)
    b = y + 1.772 * (cb - 128.0)
    out = np.stack([r, g, b], axis=-1)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def match_stats(channel, mean, std):
    """Shift and scale one channel so its mean and standard deviation become mean and std.

    A flat channel (zero spread) is only shifted: there is no spread to scale."""
    spread = channel.std()
    if spread < 1e-6:
        return channel - channel.mean() + mean
    return (channel - channel.mean()) / spread * std + mean


def fit_tone_curve(model_y_small, source_y):
    """A global, monotone curve (LEVELS values) that maps the model's luminance levels onto the source's.

    Histogram matching: for each level v, the model's fraction at or below v gives a quantile, and the
    source's luminance at that quantile is the curve's output. The result is smoothed over 5 levels and
    made non-decreasing, so it can only brighten or darken and never reverses two levels."""
    levels = np.arange(LEVELS, dtype=np.float64)
    model_sorted = np.sort(np.asarray(model_y_small, dtype=np.float64).ravel())
    source_sorted = np.sort(np.asarray(source_y, dtype=np.float64).ravel())
    cdf = np.searchsorted(model_sorted, levels, side="right") / model_sorted.size
    lut = np.quantile(source_sorted, np.clip(cdf, 0.0, 1.0))
    pad = np.pad(lut, 2, mode="edge")
    lut = np.convolve(pad, np.ones(5) / 5.0, mode="valid")
    return np.maximum.accumulate(lut)


def apply_tone_curve(y, lut):
    """Map each luminance value through the curve (linear between levels)."""
    return np.interp(np.asarray(y, dtype=np.float64), np.arange(LEVELS, dtype=np.float64), lut)


def _window(x, r, op):
    """op (np.minimum or np.maximum) over a (2r+1) x (2r+1) window, edges clamped. Separable: 4r+2 passes."""
    h, w = x.shape
    p = np.pad(x, r, mode="edge")
    cols = p[:, 0:w].copy()
    for i in range(1, 2 * r + 1):
        op(cols, p[:, i:i + w], out=cols)
    rows = cols[0:h].copy()
    for i in range(1, 2 * r + 1):
        op(rows, cols[i:i + h], out=rows)
    return rows


def keep_colours(model_rgb, reference_rgb, source_rgb, radius=4):
    """The learned picture's luminance (one global tone curve, clamped to the reference's local range) with the
    reference picture's chroma.

    model_rgb and reference_rgb are the same size (the enlarged picture); source_rgb is the picture before
    enlargement. The tone curve is fitted on the learned picture reduced to the source's size. The clamp keeps
    luminance within the reference's min and max over a (2*radius+1) square, which removes the learned
    picture's ringing (a light rim beside strokes) without blurring. radius is the scale: 4 at 4x. Returns
    uint8 RGB the size of model_rgb."""
    if model_rgb.shape != reference_rgb.shape:
        raise ValueError("keep_colours needs the learned and reference pictures the same size")
    h, w = source_rgb.shape[:2]
    y_model, _, _ = rgb_to_ycbcr(model_rgb)
    y_small = np.asarray(Image.fromarray(np.ascontiguousarray(y_model, dtype=np.float32)).resize((w, h), Image.BOX))
    y_source, scb_src, scr_src = rgb_to_ycbcr(source_rgb)
    lut = fit_tone_curve(y_small, y_source)
    y = apply_tone_curve(y_model, lut)
    y_ref, _, _ = rgb_to_ycbcr(reference_rgb)
    y = np.clip(y, _window(y_ref, radius, np.minimum), _window(y_ref, radius, np.maximum))
    # saturated colour (gold, strong red) takes its luminance from the reference: the learned picture's
    # luminance shifts a saturated colour's lightness, so the weight ramps from 0 (sat 60) to 1 (sat 120)
    ref = np.asarray(reference_rgb, dtype=np.float32)
    saturation = ref.max(axis=-1) - ref.min(axis=-1)
    weight = np.clip((saturation - 60.0) / 60.0, 0.0, 1.0)
    y = y * (1.0 - weight) + y_ref * weight
    _, cb, cr = rgb_to_ycbcr(reference_rgb)
    _, scb, scr = rgb_to_ycbcr(source_rgb)
    cb = match_stats(cb, scb.mean(), scb.std())
    cr = match_stats(cr, scr.mean(), scr.std())
    return ycbcr_to_rgb(y, cb, cr)


def _linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb):
    """uint8 RGB to float Lab (D65). Works on any shape with a last axis of 3."""
    lin = _linear(np.asarray(rgb, dtype=np.float64))
    r, g, b = lin[..., 0], lin[..., 1], lin[..., 2]
    # sRGB to XYZ (D65), written as explicit sums: a BLAS matmul trips -W error on some macOS builds
    xyz = np.stack([0.4124564 * r + 0.3575761 * g + 0.1804375 * b,
                    0.2126729 * r + 0.7151522 * g + 0.0721750 * b,
                    0.0193339 * r + 0.1191920 * g + 0.9503041 * b], axis=-1) / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.stack([116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])],
                    axis=-1)


def delta_e76(rgb_a, rgb_b):
    """CIE76 colour difference per pixel between two uint8 RGB arrays of the same shape."""
    return np.sqrt(((rgb_to_lab(rgb_a) - rgb_to_lab(rgb_b)) ** 2).sum(axis=-1))


def downsample(rgb, factor):
    """Area-average an RGB picture down by an integer factor (the test compares a picture with its source)."""
    h, w = rgb.shape[:2]
    small = Image.fromarray(np.asarray(rgb, dtype=np.uint8)).resize((w // factor, h // factor), Image.BOX)
    return np.asarray(small, dtype=np.uint8)
