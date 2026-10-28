import json
import os
import tempfile
import unittest

import numpy as np

from stillmove import cameras


class LoadExportTest(unittest.TestCase):
    def setUp(self):
        from camera_motion.engine.camera_path import path
        from camera_motion.export.formats import to_json
        from camera_motion.export.trajectory import intrinsics
        from camera_motion.presets import load_catalog

        self._intrinsics = intrinsics
        preset = [p for p in load_catalog() if p["id"] == "dolly_in"][0]
        self.width, self.height, self.fps = 64, 36, 4
        self.frames = path(preset, duration_s=1.0, fps=self.fps)
        self.doc = to_json(self.frames, width=self.width, height=self.height, fps=self.fps)
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.file = os.path.join(self._dir.name, "cam.json")
        with open(self.file, "w", encoding="utf-8") as handle:
            json.dump(self.doc, handle)

    def test_loads_size_fps_and_frame_count(self):
        cams = cameras.load(self.file)
        self.assertEqual((cams.width, cams.height, cams.fps), (64, 36, 4))
        self.assertEqual(len(cams.frames), len(self.frames))

    def test_pixel_intrinsics_match_the_engine_fov(self):
        cams = cameras.load(self.file)
        fx, fy, cx, cy = self._intrinsics(self.frames[0]["fov"], self.width, self.height)
        K = cams.frames[0].K
        self.assertAlmostEqual(K[0, 0], fx * self.width, places=9)
        self.assertAlmostEqual(K[1, 1], fy * self.height, places=9)
        self.assertAlmostEqual(K[0, 2], cx * self.width, places=9)
        self.assertAlmostEqual(K[1, 2], cy * self.height, places=9)

    def test_opencv_w2c_puts_the_origin_five_units_in_front(self):
        cams = cameras.load(self.file)
        origin_cam = cams.frames[0].w2c @ np.array([0.0, 0.0, 0.0, 1.0])
        self.assertAlmostEqual(origin_cam[2], 5.0, places=9)  # +z is forward in OpenCV
        self.assertAlmostEqual(origin_cam[0], 0.0, places=9)
        self.assertAlmostEqual(origin_cam[1], 0.0, places=9)

    def test_times_come_from_the_export(self):
        cams = cameras.load(self.file)
        self.assertAlmostEqual(cams.frames[1].t, 0.25)


class FromPresetTest(unittest.TestCase):
    def test_matches_loading_the_same_export_from_disk(self):
        cams = cameras.from_preset("dolly_in", 1.0, 4, 64, 36)
        self.assertEqual(len(cams.frames), 5)
        self.assertAlmostEqual(cams.frames[0].w2c[2, 3], 5.0, places=9)

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            cameras.from_preset("no_such_move", 1.0, 4, 64, 36)


class LoadErrorTest(unittest.TestCase):
    def _write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def test_not_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            cameras.load(self._write("{not json"))

    def test_missing_key_raises_value_error(self):
        with self.assertRaises(ValueError):
            cameras.load(self._write(json.dumps({"fps": 24, "width": 8, "height": 8})))

    def test_frame_without_w2c_raises_value_error(self):
        doc = {"fps": 24, "width": 8, "height": 8, "frames": [{"t": 0, "K": [[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]]}]}
        with self.assertRaises(ValueError):
            cameras.load(self._write(json.dumps(doc)))


if __name__ == "__main__":
    unittest.main()
