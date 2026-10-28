"""Keep-in-frame on the shotplan render path: each shot is fitted (or not), and its record goes into the
manifest. The subject is synthetic and sits 14 px in from the right edge of a 96x54 frame, so dolly_in
pushes it out and the fit must shorten the shot's move. With no fit the shot's camera is as planned.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

import shotplan
from shotplan import shotrender
from camera_motion.engine.camera_path import path
from camera_motion.export.formats import to_json
from camera_motion.presets import load_catalog

W, H, FPS = 96, 54, 24


class SubjectDepth:
    name = "subject"

    def depth(self, image):
        depth = np.full((H, W), 8.0)
        depth[4:50, 50:82] = 2.0
        return depth


class SubjectMatte:
    name = "subject"

    def alpha(self, rgb):
        alpha = np.zeros((H, W))
        alpha[4:50, 50:82] = 1.0
        return alpha


def placeholder_plan(shots):
    return {"id": "demo", "name": "Demo", "fps": FPS, "width": W, "height": H, "shots": shots}


class ShotFitTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.image = os.path.join(self.dir, "subject.png")
        img = np.full((H, W, 3), 120, dtype=np.uint8)
        img[4:50, 50:82] = (230, 60, 60)
        Image.fromarray(img).save(self.image)
        self.cam = os.path.join(self.dir, "camera.json")
        preset = [p for p in load_catalog() if p.get("id") == "dolly_in"][0]
        with open(self.cam, "w", encoding="utf-8") as handle:
            json.dump(to_json(path(preset, duration_s=1.0, fps=FPS), width=W, height=H, fps=FPS), handle)

    def test_render_shot_shortens_a_near_edge_subject(self):
        count, record, _aspect = shotrender.render_shot(self.image, self.cam, SubjectDepth(),
                                               os.path.join(self.dir, "s1"), fit_margin=0.06)
        self.assertEqual(count, round(FPS) + 1)
        self.assertLess(record["scale"], 1.0)
        self.assertEqual(record["margin"], 0.06)
        self.assertIn("shortened", record["note"])

    def test_a_layered_shot_fits_to_its_foreground(self):
        _count, record, _aspect = shotrender.render_shot(self.image, self.cam, SubjectDepth(),
                                                os.path.join(self.dir, "s2"), matte=SubjectMatte(),
                                                fit_margin=0.06)
        self.assertLess(record["scale"], 1.0)

    def test_render_shot_without_a_margin_has_no_record(self):
        _count, record, _aspect = shotrender.render_shot(self.image, self.cam, SubjectDepth(),
                                                os.path.join(self.dir, "s3"))
        self.assertIsNone(record)

    def test_render_plan_writes_each_shots_fit_into_the_manifest(self):
        shots = [{"id": "a", "image": "placeholder:a", "sequence": {"segments": [{"preset": "dolly_in", "duration_s": 1}]},
                  "duration": 1, "transition": "cut"}]
        plan = placeholder_plan(shots)
        errors, rows, sequences = shotplan.check(plan, self.dir)
        self.assertEqual(errors, [])
        catalog = shotplan.load_json(shotplan.CATALOG)
        fitted = shotrender.render_plan(plan, self.dir, os.path.join(self.dir, "fit"), rows, sequences, catalog,
                                        use_ffmpeg=False, fit_margin=0.06)
        self.assertEqual(fitted["shots"][0]["fit"]["margin"], 0.06)
        self.assertIn("scale", fitted["shots"][0]["fit"])
        planned = shotrender.render_plan(plan, self.dir, os.path.join(self.dir, "plan"), rows, sequences, catalog,
                                         use_ffmpeg=False, fit_margin=None)
        self.assertIsNone(planned["shots"][0]["fit"])


class ShotFitCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        shots = [{"id": "a", "image": "placeholder:a", "sequence": {"segments": [{"preset": "dolly_in", "duration_s": 1}]},
                  "duration": 1, "transition": "cut"}]
        self.plan_path = os.path.join(self.dir, "plan.json")
        with open(self.plan_path, "w", encoding="utf-8") as handle:
            json.dump(placeholder_plan(shots), handle)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = shotplan.main(list(argv))
        return code, err.getvalue()

    def test_render_no_fit_writes_null_fit_for_every_shot(self):
        outdir = os.path.join(self.dir, "out")
        code, _err = self.run_cli("render", self.plan_path, outdir, "--no-fit", "--depth", "fake")
        self.assertEqual(code, 0)
        with open(os.path.join(outdir, "manifest.json"), encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertIsNone(manifest["shots"][0]["fit"])

    def test_render_refuses_a_margin_outside_the_range(self):
        code, err = self.run_cli("render", self.plan_path, os.path.join(self.dir, "out2"), "--fit-margin", "0.5")
        self.assertEqual(code, 1)
        self.assertIn("--fit-margin", err)


if __name__ == "__main__":
    unittest.main()
