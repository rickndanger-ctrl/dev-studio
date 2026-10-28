"""Tests for stillmove.mesh_pad: the frustum of every camera pose stays inside the padded mesh, the
padding is the fewest rings that do that (within one ring), pad UVs lie on the unit square's border,
and with no rings the padded grid is the plain grid. Pure geometry: no Blender, no model."""

import unittest

import numpy as np

from stillmove import blender_preview, cameras, mesh, mesh_pad

N = M = 64


def _scene():
    """A 64x64 camera path (dolly_in) and its 64x64 gradient mesh, plus the orbit after it."""
    cams = cameras.from_preset("dolly_in", 1.0, 8, N, N)
    rgb = np.zeros((N, N, 3), dtype=np.uint8)
    depth = mesh.make_depth("gradient", rgb)
    base = mesh.grid_vertices(cams.frames[0], depth, N, M)
    key = mesh._key_rows(cams)
    orbit = blender_preview.orbit_poses(key[-1][0])
    return cams, key, orbit, depth, base


def _all_poses(cams, key, orbit):
    """Every camera pose of the move and the orbit: (location, rotation, K)."""
    poses = [(loc, mesh_pad.rotation_from_quat(q), cams.frames[i].K) for i, (loc, q, _) in enumerate(key)]
    poses += [(loc, mesh_pad.rotation_from_quat(q), cams.frames[-1].K) for loc, q in orbit]
    return poses


def _hits_in_cam0(cams, key, orbit, base):
    """Pixel positions in camera 0 of every frustum corner ray's hit on the backdrop plane."""
    loc0 = key[0][0]
    rot0 = mesh_pad.rotation_from_quat(key[0][1])
    depth = mesh_pad.far_depth(base, loc0, rot0)
    pixels, rays_total = [], 0
    k0 = cams.frames[0].K
    for loc, rot, k in _all_poses(cams, key, orbit):
        rays = mesh_pad.frustum_rays(k, cams.width, cams.height, loc, rot)
        rays_total += len(rays)
        for hit in mesh_pad.backdrop_hits(rays, loc0, rot0, depth):
            px = mesh_pad.pixel_of(hit, k0, loc0, rot0)
            if px is not None:
                pixels.append(px)
    return pixels, rays_total


class FrustumStaysInsideTest(unittest.TestCase):
    def setUp(self):
        self.cams, self.key, self.orbit, self.depth, self.base = _scene()

    def test_every_corner_ray_of_every_frame_hits_inside_the_padded_mesh(self):
        pixels, rays_total = _hits_in_cam0(self.cams, self.key, self.orbit, self.base)
        rx, ry = mesh_pad.plan_rings(self.cams, self.key, self.base, N, M)
        self.assertEqual(len(pixels), rays_total, "every corner ray must meet the backdrop plane")
        self.assertTrue(mesh_pad.covers(pixels, self.cams.width, self.cams.height, N, M, rx, ry))

    def test_the_padding_is_needed_for_this_move(self):
        # A dolly plus an orbit reaches past the 64x64 grid, so some rings are needed.
        rx, ry = mesh_pad.plan_rings(self.cams, self.key, self.base, N, M)
        self.assertGreater(max(rx, ry), 0)

    def test_padding_is_within_one_ring_of_the_minimum(self):
        pixels, _ = _hits_in_cam0(self.cams, self.key, self.orbit, self.base)
        rx, ry = mesh_pad.plan_rings(self.cams, self.key, self.base, N, M)
        if rx > 0:
            self.assertFalse(mesh_pad.covers(pixels, self.cams.width, self.cams.height, N, M,
                                             rx - 1, ry, margin=mesh_pad.MARGIN),
                             "one ring fewer in x should not cover the margin")
        if ry > 0:
            self.assertFalse(mesh_pad.covers(pixels, self.cams.width, self.cams.height, N, M,
                                             rx, ry - 1, margin=mesh_pad.MARGIN),
                             "one ring fewer in y should not cover the margin")

    def test_padded_grid_covers_with_the_margin(self):
        pixels, _ = _hits_in_cam0(self.cams, self.key, self.orbit, self.base)
        rx, ry = mesh_pad.plan_rings(self.cams, self.key, self.base, N, M)
        self.assertTrue(mesh_pad.covers(pixels, self.cams.width, self.cams.height, N, M, rx, ry,
                                        margin=mesh_pad.MARGIN))


class PadGeometryTest(unittest.TestCase):
    def setUp(self):
        self.cams, self.key, self.orbit, self.depth, self.base = _scene()

    def test_no_rings_gives_exactly_the_plain_grid(self):
        padded = mesh_pad.pad_vertices(self.cams.frames[0], self.depth, N, M, 0, 0)
        self.assertEqual(len(padded), N * M)
        self.assertTrue(np.allclose(np.array(padded), self.base, atol=1e-9))

    def test_pad_vertices_count_and_order(self):
        rx, ry = 2, 1
        padded = mesh_pad.pad_vertices(self.cams.frames[0], self.depth, N, M, rx, ry)
        self.assertEqual(len(padded), (N + 2 * rx) * (M + 2 * ry))
        inner = np.array(padded).reshape(M + 2 * ry, N + 2 * rx, 3)[ry:ry + M, rx:rx + N]
        self.assertTrue(np.allclose(inner.reshape(-1, 3), self.base, atol=1e-9))

    def test_pad_uvs_lie_on_the_unit_square_border(self):
        rx, ry = 3, 2
        uvs = np.array(mesh_pad.pad_uvs(N, M, rx, ry)).reshape(M + 2 * ry, N + 2 * rx, 2)
        pad = np.ones((M + 2 * ry, N + 2 * rx), dtype=bool)
        pad[ry:ry + M, rx:rx + N] = False  # the real grid
        for j, i in zip(*np.nonzero(pad)):
            u, v = uvs[j, i]
            self.assertTrue(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0)
            self.assertTrue(u in (0.0, 1.0) or v in (0.0, 1.0), (u, v))

    def test_pad_uvs_of_the_real_grid_are_unchanged(self):
        rx, ry = 2, 2
        uvs = np.array(mesh_pad.pad_uvs(N, M, rx, ry)).reshape(M + 2 * ry, N + 2 * rx, 2)
        inner = uvs[ry:ry + M, rx:rx + N].reshape(-1, 2)
        expected = [(i / (N - 1), 1.0 - j / (M - 1)) for j in range(M) for i in range(N)]
        self.assertTrue(np.allclose(inner, expected))

    def test_pad_depth_repeats_the_nearest_edge(self):
        rx = 2
        padded = np.array(mesh_pad.pad_vertices(self.cams.frames[0], self.depth, N, M, rx, 0))
        loc0 = self.key[0][0]
        forward = -np.array(mesh_pad.rotation_from_quat(self.key[0][1]))[:, 2]
        along = np.sum((padded - np.array(loc0)) * forward, axis=1).reshape(M, N + 2 * rx)  # no BLAS: see warp.py
        # Each left ring column is at the depth of the grid's first column, and each right ring at the last.
        for k in range(rx):
            self.assertTrue(np.allclose(along[:, k], along[:, rx]))
            self.assertTrue(np.allclose(along[:, N + rx + k], along[:, N + rx - 1]))

class PadKeepTest(unittest.TestCase):
    def test_ring_faces_repeat_the_nearest_edge_face(self):
        keep = np.ones((3, 4), dtype=bool)
        keep[0, 0] = False  # a cut in the top-left corner face
        padded = mesh_pad.pad_keep(keep, 1, 1)
        self.assertEqual(padded.shape, (5, 6))
        self.assertTrue(np.array_equal(padded[1:4, 1:5], keep))  # the grid's own faces are unchanged
        self.assertFalse(padded[0, 0])  # the ring corner beyond the cut repeats it
        self.assertTrue(padded[0, 5])
        self.assertTrue(padded[4, 0])

    def test_no_rings_keeps_the_mask(self):
        keep = np.array([[True, False], [True, True]])
        self.assertTrue(np.array_equal(mesh_pad.pad_keep(keep, 0, 0), keep))


class BackdropPaddingTest(unittest.TestCase):
    def test_a_deeper_backdrop_never_needs_fewer_rings(self):
        cams = cameras.from_preset("dolly_in", 1.0, 8, 64, 64)
        rgb = np.zeros((64, 64, 3), dtype=np.uint8)
        depth = mesh.make_depth("gradient", rgb)
        key = mesh._key_rows(cams)
        base = mesh.grid_vertices(cams.frames[0], depth, 64, 64)
        deeper = mesh.grid_vertices(cams.frames[0], depth * 2.5, 64, 64)
        alone = mesh_pad.plan_rings(cams, key, base, 64, 64)
        both = mesh_pad.plan_rings(cams, key, list(base) + list(deeper), 64, 64)
        self.assertGreaterEqual(both[0], alone[0])
        self.assertGreaterEqual(both[1], alone[1])
        self.assertGreater(both[0] + both[1], alone[0] + alone[1])


class TiltedSurfaceTest(unittest.TestCase):
    """A tilted gradient mesh (depth 3 at the bottom, 8 at the top): frame 0's corners and sampled
    frustum points at any depth in the mesh's range must land inside the padded grid."""

    def setUp(self):
        self.cams = cameras.from_preset("dolly_in", 1.0, 8, N, N)
        rgb = np.zeros((N, N, 3), dtype=np.uint8)
        self.depth = mesh.make_depth("gradient", rgb)
        self.base = mesh.grid_vertices(self.cams.frames[0], self.depth, N, N)
        self.key = mesh._key_rows(self.cams)
        self.orbit = blender_preview.orbit_poses(self.key[-1][0])
        self.rx, self.ry = mesh_pad.plan_rings(self.cams, self.key, list(self.base), N, N)
        self.loc0 = self.key[0][0]
        self.rot0 = mesh_pad.rotation_from_quat(self.key[0][1])
        self.forward = -np.array(self.rot0)[:, 2]
        depths = [float(np.dot(np.array(p) - np.array(self.loc0), self.forward)) for p in self.base]
        self.dmin, self.dmax = min(depths), max(depths)

    def test_frame0_corners_land_inside_the_padded_grid(self):
        k0 = self.cams.frames[0].K
        corners = [(0.0, 0.0), (N * 1.0, 0.0), (N * 1.0, N * 1.0), (0.0, N * 1.0)]
        pixels = []
        for origin, direction in mesh_pad.frustum_rays(k0, self.cams.width, self.cams.height, self.loc0, self.rot0):
            for depth in (self.dmin, self.dmax):
                den = float(np.dot(direction, self.forward))
                t = (depth - float(np.dot(np.array(origin) - np.array(self.loc0), self.forward))) / den
                pixels.append(mesh_pad.pixel_of(tuple(np.array(origin) + t * np.array(direction)), k0,
                                                self.loc0, self.rot0))
        self.assertEqual(len(pixels), 8)
        self.assertTrue(mesh_pad.covers(pixels, self.cams.width, self.cams.height, N, N,
                                        self.rx, self.ry, margin=mesh_pad.MARGIN))

    def test_sampled_frustum_points_at_any_depth_land_inside(self):
        rng = np.random.default_rng(7)
        k0 = self.cams.frames[0].K
        poses = [(loc, mesh_pad.rotation_from_quat(q), self.cams.frames[i].K)
                 for i, (loc, q, _) in enumerate(self.key)]
        poses += [(loc, mesh_pad.rotation_from_quat(q), self.cams.frames[-1].K) for loc, q in self.orbit]
        pixels = []
        for loc, rot, k in poses:
            rot = np.array(rot)
            for _ in range(60):
                u = rng.uniform(0, self.cams.width)
                v = rng.uniform(0, self.cams.height)
                local = np.array([(u - k[0][2]) / k[0][0], -(v - k[1][2]) / k[1][1], -1.0])
                direction = rot @ local
                den = float(np.dot(direction, self.forward))
                if abs(den) < 1e-9:
                    continue
                depth = rng.uniform(self.dmin, self.dmax)
                t = (depth - float(np.dot(np.array(loc) - np.array(self.loc0), self.forward))) / den
                if t <= 0:
                    continue
                point = np.array(loc) + t * direction
                px = mesh_pad.pixel_of(tuple(point), k0, self.loc0, self.rot0)
                if px is not None:
                    pixels.append(px)
        self.assertGreater(len(pixels), 1000)
        self.assertTrue(mesh_pad.covers(pixels, self.cams.width, self.cams.height, N, N, self.rx, self.ry))


if __name__ == "__main__":
    unittest.main()
