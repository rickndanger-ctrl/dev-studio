"""Tests for the matte mesh (write_mesh with matte=): the 'foreground' is one quad (4 vertices) at the
foreground depth, textured with foreground.png whose alpha is the matte; the 'backdrop' is the clean plate
on one plane (its vertices coplanar), padded like the grid. Uses the fake matte and a small picture: no
model, no Blender."""

import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from stillmove import cameras, mesh, mesh_layers
from stillmove.layers import Layers
from stillmove.matte import get_matte

W, H = 40, 30
GRID = (12, 9)


def _object_lines(path, name):
    """The 'v' and 'f' lines of object `name` in an OBJ, as parsed lists."""
    verts, faces, current = [], [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "o":
                current = parts[1]
            elif current == name and parts[0] == "v":
                verts.append([float(x) for x in parts[1:4]])
            elif current == name and parts[0] == "f":
                faces.append([int(tok.split("/")[0]) for tok in parts[1:]])
    return np.array(verts), faces


def _frame0_forward_and_origin(cams):
    """Camera 0's origin and forward direction in Blender coordinates."""
    c2w_gl = np.linalg.inv(cams.frames[0].w2c) @ np.diag([1.0, -1.0, -1.0, 1.0])
    return mesh.MW @ c2w_gl[:3, 3], mesh.MW @ (-c2w_gl[:3, 2])


class MatteMeshTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "still.png")
        self.rgb = np.zeros((H, W, 3), dtype=np.uint8)
        self.rgb[..., 0] = np.linspace(30, 200, W, dtype=np.uint8)[None, :]
        self.rgb[..., 1] = np.linspace(200, 40, H, dtype=np.uint8)[:, None]
        self.rgb[..., 2] = 90
        Image.fromarray(self.rgb).save(self.image)
        self.alpha = get_matte("fake").alpha(self.rgb)
        self.cams = cameras.from_preset("dolly_in", 0.5, 4, W, H)
        self.out = os.path.join(self.dir, "out")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self):
        summary = mesh.write_mesh(self.image, self.cams, "gradient", GRID, self.out, matte=get_matte("fake"))
        return summary, os.path.join(self.out, mesh.OBJ_NAME)

    def test_the_foreground_is_one_quad_of_four_vertices(self):
        _, obj = self.write()
        verts, faces = _object_lines(obj, "foreground")
        self.assertEqual(verts.shape, (4, 3))
        self.assertEqual(len(faces), 1)
        self.assertEqual(len(faces[0]), 4)

    def test_foreground_png_alpha_is_the_matte(self):
        self.write()
        with Image.open(os.path.join(self.out, mesh.FOREGROUND_TEXTURE)) as img:
            self.assertEqual(img.mode, "RGBA")
            png_alpha = np.asarray(img)[..., 3].astype(int)
        expected = np.rint(self.alpha.astype(np.float64) * 255).astype(int)
        self.assertLessEqual(int(np.abs(png_alpha - expected).max()), 1)

    def test_the_quad_sits_at_the_foreground_depth(self):
        _, obj = self.write()
        depth = mesh.make_depth("gradient", self.rgb)
        near = Layers(self.rgb, depth, self.alpha).near
        verts, _ = _object_lines(obj, "foreground")
        origin, forward = _frame0_forward_and_origin(self.cams)
        along = (verts - origin) @ forward
        self.assertLess(float(np.ptp(along)), 1e-6)
        self.assertAlmostEqual(float(along.mean()), near, places=5)

    def test_backdrop_vertices_are_coplanar(self):
        _, obj = self.write()
        verts, faces = _object_lines(obj, "backdrop")
        self.assertGreater(len(verts), 4)
        centred = verts - verts.mean(axis=0)
        normal = np.linalg.svd(centred, full_matrices=False)[2][-1]
        residual = np.abs(centred @ normal)
        self.assertLess(float(residual.max()), 1e-6)
        self.assertTrue(faces)

    def test_backdrop_texture_is_the_clean_paper_with_a_smooth_fill_under_the_foreground(self):
        self.write()
        depth = mesh.make_depth("gradient", self.rgb)
        layers = Layers(self.rgb, depth, self.alpha)
        with Image.open(os.path.join(self.out, mesh_layers.BACKDROP_TEXTURE)) as img:
            texture = np.asarray(img.convert("RGB")).astype(int)
        background = self.alpha < mesh_layers.BACKGROUND_ALPHA
        fg = self.alpha >= mesh_layers.FOREGROUND_ALPHA
        self.assertTrue(fg.any())
        # Outside the matte the picture is the plate itself.
        self.assertTrue(np.array_equal(texture[background], np.asarray(layers.plate).astype(int)[background]))
        # Under the matte the paper is a smooth fill: every value lies within the paper's own range.
        for c in range(3):
            paper = texture[..., c][background]
            self.assertTrue(np.all((texture[..., c][fg] >= paper.min()) & (texture[..., c][fg] <= paper.max())))

    def test_the_backdrop_plane_fit_is_exact_on_a_plane(self):
        # A flat background (depth 8 everywhere) fits a plane that gives depth 8 at every pixel.
        depth = np.full((H, W), 8.0, dtype=np.float32)
        alpha = np.zeros((H, W), dtype=np.float32)
        a, b, c = mesh_layers.background_plane(depth, alpha)
        qx, qy = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
        np.testing.assert_allclose(1.0 / (a * qx + b * qy + c), 8.0, rtol=1e-9)

    def test_without_a_matte_the_object_is_still_and_there_is_no_foreground(self):
        summary = mesh.write_mesh(self.image, self.cams, "gradient", GRID, os.path.join(self.dir, "plain"))
        with open(os.path.join(self.dir, "plain", mesh.OBJ_NAME), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("o still", text)
        self.assertNotIn("o foreground", text)
        self.assertGreater(summary["faces"], 0)


if __name__ == "__main__":
    unittest.main()
