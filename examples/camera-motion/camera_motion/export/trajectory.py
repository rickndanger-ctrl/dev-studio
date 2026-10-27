"""Camera pose math for export: OpenGL convention, normalized pinhole intrinsics.

Camera axes: x right, y up, looks down -z. Angles in degrees, rotation is [yaw, pitch, roll].
R = Ry(yaw) @ Rx(pitch) @ Rz(roll). Matrices are nested lists.
"""
import math


def _rx(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def _ry(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def _rz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))]
            for i in range(len(a))]


def _transpose(m):
    return [[m[j][i] for j in range(len(m))] for i in range(len(m[0]))]


def rotation_matrix(rotation):
    """R = Ry(yaw) @ Rx(pitch) @ Rz(roll) for rotation [yaw, pitch, roll] in degrees."""
    yaw, pitch, roll = rotation
    return _matmul(_matmul(_ry(yaw), _rx(pitch)), _rz(roll))


def pose_matrix(frame):
    """Camera-to-world 4x4: [[R | position], [0 0 0 1]]."""
    r = rotation_matrix(frame["rotation"])
    p = frame["position"]
    return [
        [r[0][0], r[0][1], r[0][2], p[0]],
        [r[1][0], r[1][1], r[1][2], p[1]],
        [r[2][0], r[2][1], r[2][2], p[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def world_to_camera(frame):
    """World-to-camera 4x4: [[R^T | -R^T position], [0 0 0 1]]."""
    r = rotation_matrix(frame["rotation"])
    rt = _transpose(r)
    p = frame["position"]
    t = [-(rt[i][0] * p[0] + rt[i][1] * p[1] + rt[i][2] * p[2]) for i in range(3)]
    return [
        [rt[0][0], rt[0][1], rt[0][2], t[0]],
        [rt[1][0], rt[1][1], rt[1][2], t[1]],
        [rt[2][0], rt[2][1], rt[2][2], t[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def intrinsics(fov_deg, width, height):
    """Normalized pinhole intrinsics (fx, fy, cx, cy) for a vertical fov in degrees.

    fy = 0.5 / tan(fov / 2) in units of image height; fx = fy * height / width;
    cx = cy = 0.5.
    """
    fy = 0.5 / math.tan(math.radians(fov_deg) / 2.0)
    fx = fy * height / width
    return fx, fy, 0.5, 0.5


def K(fov_deg, width, height):
    """Normalized 3x3 intrinsics matrix."""
    fx, fy, cx, cy = intrinsics(fov_deg, width, height)
    return [[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]]


def to_realestate10k(frames, width=1024, height=576, fps=24, name=""):
    """RealEstate10K lines (OpenCV w2c); see export.formats."""
    from camera_motion.export.formats import to_realestate10k as _to_re10k
    return _to_re10k(frames, width=width, height=height, fps=fps, name=name)


def to_json(frames, width=1024, height=576, fps=24):
    """JSON trajectory document (OpenGL c2w/w2c); see export.formats."""
    from camera_motion.export.formats import to_json as _to_json
    return _to_json(frames, width=width, height=height, fps=fps)


if __name__ == "__main__":
    import sys
    from camera_motion.export.formats import main
    sys.exit(main(sys.argv[1:]))
