import io
import os
import shutil
import tempfile
import unittest

from PIL import Image

from stillmove.web import server
from stillmove.web.server import ApiError, Studio


class _Cams:
    def __init__(self, fps, width, height):
        self.fps, self.width, self.height, self.frames = fps, width, height, [0]


def _png():
    out = io.BytesIO()
    Image.new("RGB", (8, 6), (1, 2, 3)).save(out, "PNG")
    return out.getvalue()


class JobCapTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _studio(self, max_jobs):
        def render(image_path, cams, backend, outdir, mp4=False):
            os.makedirs(outdir)
            with open(os.path.join(outdir, "preview.gif"), "wb") as handle:
                handle.write(b"GIF89a")
            return {"frames": [], "preview": "preview.gif", "mp4": None, "backend": "gradient",
                    "fps": cams.fps, "width": cams.width, "height": cams.height}

        presets = [{"id": "dolly_in", "type": "dolly", "params": {"axis": "z", "distance": 0.2}}]
        return Studio(presets=presets, cameras=lambda p, s, f, w, h: _Cams(f, w, h),
                      backend=lambda name: name, renderer=render, page=b"", job_root=self.root,
                      limits={}, max_jobs=max_jobs)

    def test_the_newest_jobs_are_kept_and_older_folders_are_deleted(self):
        studio = self._studio(max_jobs=2)
        self.addCleanup(studio.close)
        ids = [studio.render_job(_png(), "dolly_in", "gradient", 1.0, 4, False) for _ in range(3)]
        self.assertEqual(list(studio.jobs), ids[1:])
        self.assertFalse(os.path.exists(os.path.join(self.root, ids[0])), "the oldest folder is deleted")
        for kept in ids[1:]:
            self.assertTrue(os.path.isdir(os.path.join(self.root, kept)))
        with self.assertRaises(ApiError) as ctx:
            studio.job(ids[0])
        self.assertEqual(ctx.exception.status, 404)

    def test_the_default_cap_is_twenty(self):
        self.assertEqual(server.MAX_JOBS, 20)
        studio = self._studio(max_jobs=server.MAX_JOBS)
        self.addCleanup(studio.close)
        self.assertEqual(studio.max_jobs, 20)


if __name__ == "__main__":
    unittest.main()
