"""The RIFE midpoint backend: refusals, call counts and padding with a fake session; the real model if installed."""

import importlib.util
import os
import unittest

import numpy as np

from stillmove.backends.rife import DEFAULT_MODEL, RifeMidpoint
from stillmove.interp import interpolate

MISSING = os.path.join(os.path.dirname(__file__), "no-such-rife-model.onnx")


class FakeSession:
    """Stands in for the ONNX session: the midpoint is the plain average of the two stacked frames."""

    def __init__(self):
        self.calls = 0
        self.shapes = []

    def run(self, outputs, feed):
        x = feed["frames"]
        self.calls += 1
        self.shapes.append(x.shape)
        return [(x[:, :3] + x[:, 3:]) / 2.0]


def flat(value, height=40, width=56):
    return np.full((height, width, 3), value, dtype=np.uint8)


class RifeRefusalTests(unittest.TestCase):
    def test_other_fractions_are_refused_plainly(self):
        backend = RifeMidpoint(model_path=MISSING, session=FakeSession())
        for t in (0.1, 0.3, 0.6, 0.9):
            with self.assertRaisesRegex(ValueError, "only the midpoint"):
                backend.between(flat(0), flat(255), t)

    def test_a_missing_model_says_where_it_should_be(self):
        backend = RifeMidpoint(model_path=MISSING)
        with self.assertRaisesRegex(ValueError, "not installed at"):
            backend.between(flat(0), flat(255), 0.5)


class RifeCallTests(unittest.TestCase):
    def test_a_4x_pair_makes_three_model_calls(self):
        session = FakeSession()
        backend = RifeMidpoint(model_path=MISSING, session=session)
        a, b = flat(0), flat(255)
        for t in (0.25, 0.5, 0.75):
            backend.between(a, b, t)
        self.assertEqual(session.calls, 3)

    def test_the_midpoint_is_cached_for_the_same_pair(self):
        session = FakeSession()
        backend = RifeMidpoint(model_path=MISSING, session=session)
        a, b = flat(0), flat(255)
        first = backend.mid(a, b)
        second = backend.mid(a, b)
        self.assertEqual(session.calls, 1)
        self.assertIs(first, second)

    def test_a_4x_interpolation_gives_five_frames_for_two(self):
        session = FakeSession()
        backend = RifeMidpoint(model_path=MISSING, session=session)
        out = interpolate([flat(0), flat(255)], 4, _Adapter(backend))
        self.assertEqual(len(out), 5)
        self.assertEqual(session.calls, 3)
        for frame, expect in ((out[1], 64), (out[2], 128), (out[3], 191)):
            self.assertLessEqual(abs(int(frame[0, 0, 0]) - expect), 2)


class _Adapter:
    """interpolate() takes an object with between(); this forwards to a RifeMidpoint."""

    def __init__(self, impl):
        self.impl = impl

    def between(self, a, b, t):
        return self.impl.between(a, b, t)


class RifePaddingTests(unittest.TestCase):
    def test_inputs_are_multiples_of_32_and_the_output_is_cropped_back(self):
        session = FakeSession()
        backend = RifeMidpoint(model_path=MISSING, session=session)
        picture = backend.mid(flat(10, height=40, width=56), flat(30, height=40, width=56))
        self.assertEqual(picture.shape, (40, 56, 3))
        self.assertEqual(session.shapes[-1][2] % 32, 0)
        self.assertEqual(session.shapes[-1][3] % 32, 0)
        self.assertEqual(session.shapes[-1][1], 6)
        self.assertLessEqual(abs(int(picture[0, 0, 0]) - 20), 1)


HAVE_MODEL = os.path.isfile(DEFAULT_MODEL) and importlib.util.find_spec("onnxruntime") is not None


@unittest.skipUnless(HAVE_MODEL, "needs onnxruntime and the RIFE model at ~/models/rife (see MODELS.md)")
class RifeModelTests(unittest.TestCase):
    def test_the_real_midpoint_has_the_right_shape_and_type(self):
        yy, xx = np.mgrid[0:90, 0:150].astype(np.float32)
        a = np.stack([xx / 150 * 255, yy / 90 * 255, np.full_like(xx, 80)], axis=2).astype(np.uint8)
        b = np.roll(a, 6, axis=1)
        picture = RifeMidpoint().between(a, b, 0.5)
        self.assertEqual(picture.shape, a.shape)
        self.assertEqual(picture.dtype, np.uint8)


if __name__ == "__main__":
    unittest.main()
