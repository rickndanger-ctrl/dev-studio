"""Review fixes for shotplan: a crossfade longer than its shot reports one real error."""

import shutil
import tempfile
import unittest

import shotplan  # noqa: E402  (sets up the import path for camera_motion)
from shotplan.test_shotplan import plan, shot  # noqa: E402


class CrossfadeTooLongTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _check(self):
        # a's 1.5 s crossfade is longer than b (1 s): the starts that follow go negative
        p = plan([shot("a", 1, transition={"crossfade": 1.5}), shot("b", 1), shot("c", 3)])
        return shotplan.check(p, self.dir)

    def test_crossfade_longer_than_its_shot_reports_exactly_one_error(self):
        errors, _, _ = self._check()
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("crossfade 1.500 s must be shorter", errors[0])

    def test_no_overlap_error_follows_a_bad_crossfade(self):
        errors, _, _ = self._check()
        self.assertFalse(any("overlaps" in e for e in errors), errors)

    def test_no_negative_start_time_is_reported(self):
        _, rows, _ = self._check()
        self.assertTrue(all(row[0] >= 0 for row in rows), rows)


if __name__ == "__main__":
    unittest.main()
