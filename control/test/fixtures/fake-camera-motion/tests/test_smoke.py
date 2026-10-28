import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from camera_motion import detect  # noqa: E402


class SmokeTest(unittest.TestCase):
    def test_detects_motion(self):
        self.assertTrue(detect([0, 0, 0], [50, 50, 50]))


if __name__ == "__main__":
    unittest.main()
