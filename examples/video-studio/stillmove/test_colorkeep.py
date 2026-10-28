"""Keep colours: a two-colour picture stays within a small Delta-E, the tone curve is monotone, no light rim
round strokes, and (model test, on the owner's logo) strokes, gold and sharpness hold up."""
import json
import os
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageFilter

from stillmove.colorkeep import (apply_tone_curve, delta_e76, downsample, fit_tone_curve, keep_colours,
                                 match_stats, rgb_to_ycbcr, ycbcr_to_rgb)
from stillmove.upscale import LanczosBackend, upscale_path

# the owner's logo stays outside the repo: point STILLMOVE_TEST_LOGO at it to run the logo test
LOGO = os.path.expanduser(os.environ.get("STILLMOVE_TEST_LOGO", "~/Pictures/stillmove-input/logo.png"))
from stillmove.backends.esrgan import DEFAULT_MODEL, EsrganGeneral  # noqa: E402

try:
    import onnxruntime  # noqa: F401

    HAVE_MODEL = os.path.isfile(DEFAULT_MODEL)
except ImportError:
    HAVE_MODEL = False


def two_colours(h=40, w=60):
    """Left half one colour, right half another, with a dark stroke across both."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, : w // 2] = (196, 64, 48)  # red
    img[:, w // 2:] = (214, 170, 60)  # gold
    img[h // 2 - 1: h // 2 + 1, :] = (30, 30, 30)  # stroke
    return img


def drifted(img, scale):
    """A stand-in for a learned upscaler: Lanczos, then a colour drift (lighter, bluer on the left)."""
    big = LanczosBackend().upscale(img, scale).astype(np.int16)
    big[:, : big.shape[1] // 2] += (10, -6, 14)
    return np.clip(big, 0, 255).astype(np.uint8)


def region_error(out, src, scale, left=True):
    """Delta-E between the mean colour of one half of out (downsampled) and the same half of src."""
    small = downsample(out, scale)
    half = src.shape[1] // 2
    part = (slice(None), slice(0, half)) if left else (slice(None), slice(half, None))
    mean_out = small[part].reshape(-1, 3).mean(axis=0).round().astype(np.uint8)[None, None]
    mean_src = src[part].reshape(-1, 3).mean(axis=0).round().astype(np.uint8)[None, None]
    return float(delta_e76(mean_out, mean_src)[0, 0])


def luma(rgb):
    return 0.299 * rgb[..., 0].astype(np.float32) + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def stroke_rings(src, scale, dilate=3):
    """(stroke, ring) masks at scale x the source: stroke is dark source pixels; ring is 1..dilate output
    pixels around them, outside the stroke. Built from the source's dark pixels, enlarged with nearest."""
    dark = (luma(src) < 110).astype(np.uint8) * 255
    stroke = np.repeat(np.repeat(dark, scale, axis=0), scale, axis=1)
    grown = np.asarray(Image.fromarray(stroke).filter(ImageFilter.MaxFilter(2 * dilate + 1)))
    stroke_mask = stroke > 0
    return stroke_mask, (grown > 0) & ~stroke_mask


def paper_level(lan_rgb, size=9):
    """The brightest luminance of the Lanczos picture in a size x size window: the local paper level."""
    y = Image.fromarray(np.clip(np.rint(luma(lan_rgb)), 0, 255).astype(np.uint8))
    return np.asarray(y.filter(ImageFilter.MaxFilter(size)), dtype=np.float32)


class ColourKeepTests(unittest.TestCase):
    def test_a_two_colour_picture_stays_within_a_small_delta_e(self):
        src = two_colours()
        model = drifted(src, 4)
        kept = keep_colours(model, LanczosBackend().upscale(src, 4), src)
        self.assertEqual(kept.shape, model.shape)
        self.assertEqual(kept.dtype, np.uint8)
        for left in (True, False):
            self.assertLess(region_error(kept, src, 4, left), 2.0)

    def test_without_keeping_the_drift_is_visible(self):
        src = two_colours()
        self.assertGreater(region_error(drifted(src, 4), src, 4, left=True), 4.0)

    def test_the_two_pictures_must_be_the_same_size(self):
        src = two_colours()
        with self.assertRaisesRegex(ValueError, "same size"):
            keep_colours(drifted(src, 4), LanczosBackend().upscale(src, 2), src)

    def test_tone_curve_is_monotone_and_maps_the_model_onto_the_source(self):
        rng = np.random.default_rng(1)
        source_y = rng.uniform(40, 200, (64, 64))
        model_y = 0.8 * source_y + 30.0  # a learned picture that is too bright and flat
        lut = fit_tone_curve(model_y, source_y)
        self.assertEqual(lut.shape, (256,))
        self.assertTrue(np.all(np.diff(lut) >= 0))
        mapped = apply_tone_curve(model_y, lut)
        self.assertLess(abs(mapped.mean() - source_y.mean()), 2.0)
        self.assertGreater(mapped.std(), 0.9 * source_y.std())

    def test_chroma_match_moves_the_mean_and_spread(self):
        flat = np.full((4, 4), 100.0)
        self.assertTrue(np.allclose(match_stats(flat, 50.0, 3.0), 50.0))
        rng = np.random.default_rng(0)
        x = rng.normal(0, 5, (32, 32))
        y = match_stats(x, 128.0, 9.0)
        self.assertAlmostEqual(y.mean(), 128.0, places=6)
        self.assertAlmostEqual(y.std(), 9.0, places=6)

    def test_ycbcr_round_trip(self):
        img = two_colours()
        back = ycbcr_to_rgb(*rgb_to_ycbcr(img))
        self.assertLessEqual(int(np.abs(back.astype(int) - img.astype(int)).max()), 1)

    def test_no_light_rim_beside_a_stroke_on_paper(self):
        # A dark stroke on paper and a learned picture that rings next to it (an unsharp mask at 4x). The
        # tone curve has no local terms, so it cannot add a rim: the kept picture's ring pixels must be no
        # brighter than the local paper, to within 2 levels.
        src = np.full((40, 40, 3), 240, dtype=np.uint8)
        src[18:22, :] = 20
        lan = LanczosBackend().upscale(src, 4)
        model = np.asarray(Image.fromarray(lan).filter(ImageFilter.UnsharpMask(radius=3, percent=400, threshold=0)),
                           dtype=np.uint8)
        kept = keep_colours(model, lan, src)
        _, ring = stroke_rings(src, 4)
        paper = paper_level(lan)
        self.assertTrue(ring.any())
        self.assertLessEqual(float((luma(kept)[ring] - paper[ring]).max()), 2.0)


@unittest.skipUnless(HAVE_MODEL and os.path.isfile(LOGO),
                     "needs the Real-ESRGAN model (see MODELS.md) and the owner's logo picture (STILLMOVE_TEST_LOGO)")
class LogoTests(unittest.TestCase):
    """The logo: strokes and gold keep their colour; edges stay sharper than Lanczos; no light rim."""

    @classmethod
    def setUpClass(cls):
        # the 320 px copy of the logo is the test input: the brief's real case (small pictures)
        full = Image.open(LOGO).convert("RGB")
        src = np.asarray(full.resize((320, 320), Image.BOX), dtype=np.uint8)
        cls.src = src
        cls.lan = LanczosBackend().upscale(src, 4)
        cls.kept = keep_colours(EsrganGeneral().upscale(src, 4), cls.lan, src, radius=4)

    def test_strokes_and_gold_stay_within_lanczos_plus_1_5_under_box_and_lanczos(self):
        # the bar is relative to the Lanczos round-trip on the same filter: the resize itself costs Delta-E
        for name, resample in (("box", Image.BOX), ("lanczos", Image.LANCZOS)):
            def small(rgb):
                return np.asarray(Image.fromarray(rgb).resize((320, 320), resample), dtype=np.uint8)

            s = small(self.src)
            sr, sg, sb = (s[..., i].astype(int) for i in range(3))
            strokes = (0.299 * sr + 0.587 * sg + 0.114 * sb) < 110
            gold = ((sr - sb) > 70) & (sr > 150)
            kept_err = delta_e76(small(self.kept), s)
            lan_err = delta_e76(small(self.lan), s)
            for mask in (strokes, gold):
                self.assertLessEqual(float(kept_err[mask].mean()), float(lan_err[mask].mean()) + 1.5, name)

    def test_edges_are_at_least_110_percent_of_lanczos_sharpness(self):
        # the p99 luminance gradient at 4x
        def p99(rgb):
            gy, gx = np.gradient(luma(rgb))
            return float(np.percentile(np.hypot(gx, gy), 99))

        self.assertGreaterEqual(p99(self.kept), 1.10 * p99(self.lan))

    def test_no_light_rim_beside_the_strokes(self):
        _, ring = stroke_rings(self.src, 4)
        paper = paper_level(self.lan)
        self.assertLessEqual(float((luma(self.kept)[ring] - paper[ring]).max()), 2.0)


class ManifestTests(unittest.TestCase):
    def test_upscale_manifest_records_keep_colours(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "pic.png")
            Image.fromarray(two_colours()).save(src)
            for keep in (True, False):
                out = os.path.join(tmp, "out-%s" % keep)
                manifest = upscale_path(src, 2, out, backend_name="lanczos", keep_colours=keep)
                self.assertIs(manifest["upscale"]["keep_colours"], keep)
                with open(os.path.join(out, "manifest.json")) as handle:
                    self.assertIs(json.load(handle)["upscale"]["keep_colours"], keep)


class FlagTests(unittest.TestCase):
    def test_keep_colours_is_on_by_default_and_can_be_turned_off(self):
        from stillmove.cli import _build_parser

        parser = _build_parser()
        for argv in (["upscale", "pic.png", "-o", "x"], ["render", "pic.png", "--preset", "dolly_in", "-o", "x"]):
            self.assertIs(parser.parse_args(argv).keep_colours, True, argv)
        off = parser.parse_args(["upscale", "pic.png", "-o", "x", "--no-keep-colours"])
        self.assertIs(off.keep_colours, False)
        off = parser.parse_args(["render", "pic.png", "--preset", "dolly_in", "-o", "x", "--no-keep-colours"])
        self.assertIs(off.keep_colours, False)


if __name__ == "__main__":
    unittest.main()
