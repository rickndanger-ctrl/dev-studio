"""Tests for stillmove.mesh_layers: no face bridges a depth cut, the backdrop sits behind it with the
far side's depth and colour, a smooth surface loses no faces, the matte hook, and write_mesh's
backdrop object. Synthetic depth only: a near plane (3.0) on a far plane (8.0)."""

import os
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from stillmove import cameras, mesh, mesh_layers, mesh_pad

H, W = 30, 40
NEAR, FAR = 3.0, 8.0
NEAR_BOX = (slice(10, 20), slice(12, 28))  # rows, columns of the near plane
RED = (200, 30, 20)
BLUE = (20, 60, 200)


def two_planes():
    depth = np.full((H, W), FAR, dtype=np.float32)
    depth[NEAR_BOX] = NEAR
    rgb = np.empty((H, W, 3), dtype=np.uint8)
    rgb[:] = BLUE
    rgb[NEAR_BOX] = RED
    return depth, rgb


def near_region():
    mask = np.zeros((H, W), dtype=bool)
    mask[NEAR_BOX] = True
    return mask


class CutTests(unittest.TestCase):
    def test_zero_faces_bridge_the_planes(self):
        depth, _ = two_planes()
        n, m = 20, 15
        keep = mesh_layers.keep_faces(depth, n, m)
        g = mesh_layers.grid_depths(depth, n, m)
        self.assertLess(keep.sum(), keep.size)  # the cut drops some faces
        for j, i in zip(*np.nonzero(keep)):
            corners = [g[j, i], g[j, i + 1], g[j + 1, i + 1], g[j + 1, i]]
            self.assertEqual(max(corners) / min(corners) <= mesh_layers.RATIO_LIMIT, True)
            self.assertTrue(all(c == NEAR for c in corners) or all(c == FAR for c in corners),
                            "a kept face spans both planes: %r" % corners)

    def test_both_planes_keep_their_own_faces(self):
        depth, _ = two_planes()
        keep = mesh_layers.keep_faces(depth, 20, 15)
        self.assertTrue(keep.any())
        g = mesh_layers.grid_depths(depth, 20, 15)
        self.assertTrue(any(g[j, i] == NEAR for j, i in zip(*np.nonzero(keep))))
        self.assertTrue(any(g[j, i] == FAR for j, i in zip(*np.nonzero(keep))))

    def test_smooth_gradient_loses_no_faces(self):
        rows = np.linspace(4.0, 4.5, 64)[:, None] * np.ones((1, 64))
        keep = mesh_layers.keep_faces(rows, 8, 8)
        self.assertTrue(keep.all())
        self.assertIsNone(mesh_layers.backdrop(rows, np.zeros((64, 64, 3), dtype=np.uint8)))


class BackdropTests(unittest.TestCase):
    def test_backdrop_depth_is_the_far_plane_behind_the_near_plane(self):
        depth, rgb = two_planes()
        layer = mesh_layers.backdrop(depth, rgb)
        self.assertIsNotNone(layer)
        back_depth, _ = layer
        self.assertTrue(np.all(back_depth[near_region()] == FAR))
        self.assertTrue(np.all(back_depth == FAR))

    def test_backdrop_texture_behind_the_near_plane_is_the_far_colour(self):
        depth, rgb = two_planes()
        _, back_rgb = mesh_layers.backdrop(depth, rgb)
        self.assertEqual(back_rgb.dtype, np.uint8)
        self.assertTrue(np.all(back_rgb[near_region()] == np.array(BLUE, dtype=np.uint8)))
        self.assertTrue(np.array_equal(back_rgb[~near_region()], rgb[~near_region()]))

    def test_foreground_is_the_near_side_grown_by_the_flood(self):
        depth, _ = two_planes()
        mask = mesh_layers.foreground_mask(depth)
        self.assertTrue(mask[near_region()].all())
        self.assertFalse(mask[0, 0])
        self.assertGreater(mask.sum(), near_region().sum())  # grown past the cut edge

    def test_matte_hook_replaces_the_depth_cut(self):
        depth, _ = two_planes()
        matte = np.zeros((H, W), dtype=bool)
        matte[0:5, 0:5] = True
        self.assertTrue(np.array_equal(mesh_layers.foreground_mask(depth, matte), matte))
        with self.assertRaises(ValueError):
            mesh_layers.foreground_mask(depth, np.zeros((3, 3), dtype=bool))

    def test_a_mask_covering_the_whole_picture_has_no_backdrop(self):
        depth, rgb = two_planes()
        self.assertIsNone(mesh_layers.backdrop(depth, rgb, matte=np.ones((H, W), dtype=bool)))


class WriteMeshBackdropTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_mesh_adds_a_backdrop_object_and_drops_bridging_faces(self):
        depth, rgb = two_planes()
        image = os.path.join(self.dir, "still.png")
        Image.fromarray(rgb).save(image)
        gray = np.where(near_region(), 255, 0).astype(np.uint8)  # white near, black far
        depth_png = os.path.join(self.dir, "depth.png")
        Image.fromarray(gray).save(depth_png)
        cams = cameras.from_preset("dolly_in", 0.5, 4, W, H)
        out = os.path.join(self.dir, "out")
        n, m = 20, 15
        summary = mesh.write_mesh(image, cams, depth_png, (n, m), out, near=NEAR, far=FAR)
        with open(os.path.join(out, mesh.OBJ_NAME), encoding="utf-8") as f:
            obj = f.read()
        self.assertIn("o backdrop", obj)
        self.assertIn("usemtl backdrop", obj)
        # The grid is padded with rings (mesh_pad), so the bridging faces are judged on the padded grid.
        depth_map = mesh.make_depth(depth_png, rgb, NEAR, FAR)
        far_points = list(mesh.grid_vertices(cams.frames[0], depth_map, n, m))
        far_points += mesh.grid_vertices(cams.frames[0], mesh_layers.backdrop(depth_map, rgb)[0], n, m)
        rx, ry = mesh_pad.plan_rings(cams, mesh._key_rows(cams), far_points, n, m)
        padded_faces = (n + 2 * rx - 1) * (m + 2 * ry - 1)
        self.assertLess(summary["faces"], padded_faces)
        self.assertTrue(os.path.exists(os.path.join(out, mesh_layers.BACKDROP_TEXTURE)))
        with open(os.path.join(out, mesh.MTL_NAME), encoding="utf-8") as f:
            mtl = f.read()
        self.assertIn("newmtl backdrop", mtl)
        self.assertIn("map_Kd backdrop.png", mtl)
        self.assertIn(mesh_layers.BACKDROP_TEXTURE, summary["files"])

    def test_smooth_mesh_has_no_backdrop_object(self):
        image = os.path.join(self.dir, "still.png")
        Image.new("RGB", (W, H), (120, 80, 40)).save(image)
        cams = cameras.from_preset("dolly_in", 0.5, 4, W, H)
        out = os.path.join(self.dir, "out")
        mesh.write_mesh(image, cams, "plane", (4, 3), out)
        with open(os.path.join(out, mesh.OBJ_NAME), encoding="utf-8") as f:
            self.assertNotIn("o backdrop", f.read())
        self.assertFalse(os.path.exists(os.path.join(out, mesh_layers.BACKDROP_TEXTURE)))


if __name__ == "__main__":
    unittest.main()
