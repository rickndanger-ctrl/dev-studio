import os
import shutil
import tempfile
import time
import unittest

from shotplan.web.server import Studio


def _wait_idle(studio, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if studio.status()["state"] != "running":
            return
        time.sleep(0.01)
    raise AssertionError("the render did not finish")


class ShotPlanJobCapTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _render(self, plan_path, outdir):
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "preview.gif"), "wb") as handle:
            handle.write(b"GIF89a")

    def test_the_newest_render_folders_are_kept_and_older_ones_deleted(self):
        studio = Studio(job_root=self.root, renderer=self._render, max_jobs=2)
        self.addCleanup(studio.close)
        plan = studio.sample(studio.plans()[0]["name"])
        ids = []
        for _ in range(3):
            ids.append(studio.start_render(plan))
            _wait_idle(studio)
        self.assertFalse(os.path.exists(os.path.join(self.root, ids[0])), "the oldest folder is deleted")
        for kept in ids[1:]:
            self.assertTrue(os.path.isdir(os.path.join(self.root, kept)))
        self.assertEqual(studio.status()["id"], ids[2])


if __name__ == "__main__":
    unittest.main()
