"""Tests for stillmove.mesh: counts, UVs, flat plane geometry, the centre vertex on the frame-0 forward
ray (Blender coordinates), the scene script, the depth PNG mapping and the mesh CLI. Tiny images."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from stillmove import blender_preview as bp
from stillmove import cameras, cli, mesh, mesh_pad
from stillmove.depth import check_depth

W, H = 16, 9
GL_TO_CV = np.diag([1.0, -1.0, -1.0, 1.0])
MW = np.array(mesh.MW, dtype=np.float64)


def cams_dolly(seconds=0.5, fps=4):
    return cameras.from_preset("dolly_in", seconds, fps, W, H)


def frame0_c2w_gl(cams):
    """Frame 0's camera-to-world, OpenGL axes (the camera-motion world)."""
    return np.linalg.inv(cams.frames[0].w2c) @ GL_TO_CV


def frame0_origin_and_forward(cams):
    c2w = frame0_c2w_gl(cams)
    origin = MW @ c2w[:3, 3]
    forward = MW @ (-c2w[:3, 2])  # OpenGL cameras look down their -z axis
    return origin, forward


def read_obj(path):
    verts, uvs, faces = [], [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v":
                verts.append([float(x) for x in parts[1:4]])
            elif parts[0] == "vt":
                uvs.append([float(x) for x in parts[1:3]])
            elif parts[0] == "f":
                faces.append([int(p.split("/")[0]) for p in parts[1:]])
    return np.array(verts), np.array(uvs), faces


def padded_size(cams, grid, depth="fake"):
    """(n, m) of the padded grid write_mesh builds for this move: the grid plus the rings it needs."""
    n, m = grid
    rgb = np.zeros((cams.height, cams.width, 3), dtype=np.uint8)
    depth_map = mesh.make_depth(depth, rgb)
    base = mesh.grid_vertices(cams.frames[0], depth_map, n, m)
    rx, ry = mesh_pad.plan_rings(cams, mesh._key_rows(cams), base, n, m)
    return n + 2 * rx, m + 2 * ry, rx, ry


class MeshTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.image = os.path.join(self.dir, "still.png")
        Image.new("RGB", (W, H), (120, 80, 40)).save(self.image)
        self.out = os.path.join(self.dir, "out")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def build(self, grid, depth="fake", cams=None):
        cams = cams or cams_dolly()
        mesh.write_mesh(self.image, cams, depth, grid, self.out)
        return cams, read_obj(os.path.join(self.out, mesh.OBJ_NAME))

    def test_vertex_and_face_counts(self):
        cams, (verts, uvs, faces) = self.build((5, 4))
        n, m, _, _ = padded_size(cams, (5, 4))
        self.assertEqual(len(verts), n * m)
        self.assertEqual(len(uvs), n * m)
        self.assertEqual(len(faces), (n - 1) * (m - 1))
        self.assertTrue(all(len(face) == 4 for face in faces))

    def test_uvs_are_in_the_unit_square_and_run_top_row_to_bottom(self):
        _, (_, uvs, _) = self.build((5, 4))
        self.assertTrue(np.all(uvs >= 0.0) and np.all(uvs <= 1.0))
        self.assertEqual(tuple(uvs[0]), (0.0, 1.0))   # top-left
        self.assertEqual(tuple(uvs[-1]), (1.0, 0.0))  # bottom-right

    def test_plane_depth_is_a_flat_mesh_perpendicular_to_the_frame0_axis(self):
        cams, (verts, _, _) = self.build((5, 4), depth="fake")
        origin, forward = frame0_origin_and_forward(cams)
        along = (verts - origin) @ forward
        self.assertTrue(np.allclose(along, along[0], atol=1e-9), along)  # every vertex the same distance
        self.assertAlmostEqual(along[0], 5.0, places=6)  # the plane backend's depth
        # The surface normal is the frame-0 axis: two edges across the grid are both perpendicular to it.
        edge1 = verts[1] - verts[0]
        edge2 = verts[5] - verts[0]
        self.assertAlmostEqual(float(np.dot(edge1, forward)), 0.0, places=9)
        self.assertAlmostEqual(float(np.dot(edge2, forward)), 0.0, places=9)

    def test_centre_vertex_lies_on_the_frame0_forward_ray(self):
        cams, (verts, _, _) = self.build((5, 3), depth="fake")  # an odd grid has a centre point
        origin, forward = frame0_origin_and_forward(cams)
        n, _, rx, ry = padded_size(cams, (5, 3))
        centre = verts[(ry + 1) * n + (rx + 2)]  # i = 2 of 5 columns, j = 1 of 3 rows: the frame centre
        offset = centre - origin
        self.assertLess(np.linalg.norm(np.cross(offset, forward)), 1e-9)
        self.assertGreater(float(np.dot(offset, forward)), 0.0)  # in front of the camera

    def test_gradient_depth_changes_the_distances(self):
        cams, (verts, _, _) = self.build((5, 4), depth="gradient")
        origin, forward = frame0_origin_and_forward(cams)
        along = (verts - origin) @ forward
        self.assertGreater(along.max() - along.min(), 1.0)  # not flat: top and bottom differ

    def test_scene_compiles_keys_every_frame_and_imports_the_obj(self):
        cams, _ = self.build((5, 4))
        with open(os.path.join(self.out, mesh.SCENE_NAME), encoding="utf-8") as f:
            script = f.read()
        compile(script, mesh.SCENE_NAME, "exec")
        self.assertIn("wm.obj_import", script)
        self.assertIn("import_scene.obj", script)
        self.assertIn("forward_axis='Y'", script)
        frames = mesh.scene_frames(script)
        self.assertEqual(len(frames), len(cams.frames))
        self.assertEqual(len(frames), 3)  # 0.5 s at 4 fps: 3 frames

    def test_the_texture_and_mtl_point_at_the_copied_image(self):
        self.build((3, 3))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "texture.png")))
        with open(os.path.join(self.out, mesh.MTL_NAME), encoding="utf-8") as f:
            self.assertIn("map_Kd texture.png", f.read())

    def test_depth_png_maps_white_near_and_black_far(self):
        white = os.path.join(self.dir, "white.png")
        black = os.path.join(self.dir, "black.png")
        Image.new("L", (W, H), 255).save(white)
        Image.new("L", (W, H), 0).save(black)
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        near = mesh.make_depth(white, rgb, near=3.0, far=8.0)
        far = mesh.make_depth(black, rgb, near=3.0, far=8.0)
        self.assertAlmostEqual(float(near[0, 0]), 3.0, places=5)
        self.assertAlmostEqual(float(far[0, 0]), 8.0, places=5)

    def test_bad_depth_and_grid_are_refused(self):
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        with self.assertRaises(ValueError):
            mesh.make_depth("nope.jpg", rgb)
        with self.assertRaises(ValueError):
            mesh.parse_grid("1x4")
        with self.assertRaises(ValueError):
            mesh.parse_grid("wide")

    def test_cli_mesh_writes_the_files(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["mesh", self.image, "--depth", "fake", "--preset", "dolly_in", "--seconds", "0.5",
                             "--fps", "4", "--grid", "3x2", "-o", self.out])
        self.assertEqual(code, 0)
        for name in (mesh.OBJ_NAME, mesh.MTL_NAME, mesh.SCENE_NAME, "texture.png"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)

    def test_cli_mesh_needs_exactly_one_camera(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["mesh", self.image, "-o", self.out])  # neither --preset nor --cam
        self.assertEqual(code, 1)

    def test_smooth_depth_runs_once_per_mesh_for_backends_and_png(self):
        from stillmove import depth as depth_module

        spy = mock.Mock(wraps=depth_module.smooth_depth)
        with mock.patch.object(mesh, "smooth_depth", spy):
            mesh.write_mesh(self.image, cams_dolly(), "gradient", (3, 2), self.out)
            self.assertEqual(spy.call_count, 1)
            spy.reset_mock()
            png = os.path.join(self.dir, "depth.png")
            Image.new("L", (W, H), 200).save(png)
            mesh.write_mesh(self.image, cams_dolly(), png, (3, 2), self.out)
            self.assertEqual(spy.call_count, 1)

    def test_da2_small_is_an_accepted_depth_name(self):
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        try:
            depth = mesh.make_depth("da2-small", rgb)
        except ValueError as exc:  # an unknown name would be refused here
            self.fail(f"da2-small must be a known depth name: {exc}")
        except (FileNotFoundError, ImportError) as exc:  # known, but the model or onnxruntime is absent
            self.skipTest(f"da2-small needs the model and onnxruntime; skipped: {exc}")
        self.assertEqual(depth.shape, (H, W))

    def test_cli_parser_takes_da2_small(self):
        parser = cli._build_parser()
        args = parser.parse_args(["mesh", self.image, "--depth", "da2-small", "--preset", "dolly_in", "-o", self.out])
        self.assertEqual(args.depth, "da2-small")


class AutoGridTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_auto_grid_for_square_landscape_and_portrait(self):
        self.assertEqual(mesh.resolve_grid("auto", 1254, 1254), (256, 256))
        self.assertEqual(mesh.resolve_grid("auto", 1920, 1080), (256, 144))
        self.assertEqual(mesh.resolve_grid("AUTO", 1080, 1920), (144, 256))

    def test_short_side_is_at_least_two(self):
        self.assertEqual(mesh.resolve_grid("auto", 4000, 3), (256, 2))

    def test_explicit_grid_is_unchanged_by_the_image_size(self):
        self.assertEqual(mesh.resolve_grid("32x18", 1920, 1080), (32, 18))
        self.assertEqual(mesh.resolve_grid("32x18", 10, 10), (32, 18))
        with self.assertRaises(ValueError):
            mesh.resolve_grid("wide", 10, 10)

    def test_vertex_and_face_counts_follow_the_auto_grid(self):
        for size, (gw, gh) in (((96, 96), (256, 256)), ((160, 90), (256, 144)), ((90, 160), (144, 256))):
            with self.subTest(size=size):
                image = os.path.join(self.dir, "still-%dx%d.png" % size)
                Image.new("RGB", size, (120, 80, 40)).save(image)
                cams = cameras.from_preset("dolly_in", 0.5, 4, *size)
                out = os.path.join(self.dir, "out-%dx%d" % size)
                grid = mesh.resolve_grid("auto", *size)
                self.assertEqual(grid, (gw, gh))
                summary = mesh.write_mesh(image, cams, "fake", grid, out)
                pn, pm, _, _ = padded_size(cams, grid)
                verts, _, faces = read_obj(os.path.join(out, mesh.OBJ_NAME))
                self.assertEqual(len(verts), pn * pm)
                self.assertEqual(len(faces), (pn - 1) * (pm - 1))
                self.assertEqual(summary["vertices"], pn * pm)
                self.assertEqual(summary["faces"], (pn - 1) * (pm - 1))

    def test_cli_default_grid_is_auto(self):
        image = os.path.join(self.dir, "square.png")
        Image.new("RGB", (48, 48), (120, 80, 40)).save(image)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["mesh", image, "--preset", "dolly_in", "--seconds", "0.5", "--fps", "4",
                             "-o", os.path.join(self.dir, "out")])
        self.assertEqual(code, 0)
        cams = cameras.from_preset("dolly_in", 0.5, 4, 48, 48)
        pn, pm, _, _ = padded_size(cams, (256, 256))
        self.assertIn("wrote %d vertices" % (pn * pm), buf.getvalue())


class WriteMeshBackdropTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_backdrop_object_is_padded_like_the_main_object_and_mapped_to_its_png(self):
        w, h = 64, 48
        image = os.path.join(self.dir, "still.png")
        rgb = np.zeros((h, w, 3), dtype=np.uint8)
        rgb[:, : w // 2] = (220, 30, 30)
        rgb[:, w // 2:] = (30, 30, 220)
        Image.fromarray(rgb).save(image)
        depth_png = os.path.join(self.dir, "depth.png")
        gray = np.zeros((h, w), dtype=np.uint8)
        gray[:, : w // 2] = 255  # near on the left, far on the right: a depth cut
        Image.fromarray(gray).save(depth_png)
        cams = cameras.from_preset("dolly_in", 0.5, 4, w, h)
        out = os.path.join(self.dir, "out")
        summary = mesh.write_mesh(image, cams, depth_png, (16, 12), out, blender_preview=True)
        self.assertIn("backdrop.png", summary["files"])
        with open(os.path.join(out, mesh.OBJ_NAME), encoding="utf-8") as f:
            lines = f.read().splitlines()
        objects = [i for i, line in enumerate(lines) if line.startswith("o ")]
        self.assertEqual(len(objects), 2)
        counts = {}
        current = None
        for line in lines:
            if line.startswith("o "):
                current = line[2:]
                counts[current] = 0
            elif line.startswith("v ") and current:
                counts[current] += 1
        self.assertEqual(counts["still"], counts["backdrop"])  # padded alike: the same rings
        with open(os.path.join(out, bp.SCRIPT_NAME), encoding="utf-8") as f:
            script = f.read()
        self.assertIn("'backdrop': 'backdrop.png'", script)
        self.assertIn("'still': 'texture.png'", script)


if __name__ == "__main__":
    unittest.main()
