"""glTF 2.0 export of a camera path: one node with a perspective camera, LINEAR animation.

glTF is Y-up with the camera looking down -Z, the same as the engine. So translation is
the camera position and rotation is mat_to_quat of the camera's rotation matrix, with no
axis conversion. The buffer is one base64 data URI of little-endian float32 values:
times, translations (VEC3) and rotations (VEC4, x y z w).

Core glTF has no fov channel. The camera's yfov is the first frame's fov, and
asset.extras.note says so.
"""
import base64
import math
import struct

from camera_motion.export.quat import make_continuous, mat_to_quat
from camera_motion.export.trajectory import pose_matrix

FLOAT = 5126
NOTE = (
    "fov is animated in the source path, but core glTF has no fov channel: "
    "the camera yfov is the first frame's fov only."
)
DATA_URI_PREFIX = "data:application/octet-stream;base64,"


def _rotation_quat(frame):
    c2w = pose_matrix(frame)
    rotation = [row[:3] for row in c2w[:3]]
    return [float(v) for v in mat_to_quat(rotation)]


def _pack(values):
    return struct.pack("<%df" % len(values), *values)


def _bounds(values, width):
    columns = [values[i::width] for i in range(width)]
    return [min(c) for c in columns], [max(c) for c in columns]


def to_gltf(frames, fps=24, width=1024, height=576, name=""):
    """Return a glTF 2.0 document (a dict) for the camera frames."""
    if not frames:
        raise ValueError("need at least one frame")
    n = len(frames)
    times = [float(f["t"]) for f in frames]
    translations = []
    for frame in frames:
        translations.extend(float(v) for v in frame["position"])
    # Keep the sign continuous across keys so LINEAR takes the short way between them.
    quats = make_continuous(_rotation_quat(frame) for frame in frames)
    rotations = [float(v) for q in quats for v in q]

    time_bytes = _pack(times)
    trans_bytes = _pack(translations)
    rot_bytes = _pack(rotations)
    blob = time_bytes + trans_bytes + rot_bytes

    t_min, t_max = [min(times)], [max(times)]
    tr_min, tr_max = _bounds(translations, 3)
    rot_min, rot_max = _bounds(rotations, 4)

    first = frames[0]
    first_trans = [float(v) for v in first["position"]]
    first_rot = [float(v) for v in quats[0]]

    return {
        "asset": {
            "version": "2.0",
            "generator": "camera-motion",
            "extras": {"note": NOTE, "fps": fps},
        },
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{
            "name": name or "camera",
            "camera": 0,
            "translation": first_trans,
            "rotation": first_rot,
        }],
        "cameras": [{
            "type": "perspective",
            "perspective": {
                "yfov": math.radians(first["fov"]),
                "aspectRatio": width / height,
                "znear": 0.01,
            },
        }],
        "buffers": [{
            "byteLength": len(blob),
            "uri": DATA_URI_PREFIX + base64.b64encode(blob).decode("ascii"),
        }],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(time_bytes)},
            {"buffer": 0, "byteOffset": len(time_bytes), "byteLength": len(trans_bytes)},
            {"buffer": 0, "byteOffset": len(time_bytes) + len(trans_bytes), "byteLength": len(rot_bytes)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": FLOAT, "count": n, "type": "SCALAR",
             "min": t_min, "max": t_max},
            {"bufferView": 1, "componentType": FLOAT, "count": n, "type": "VEC3",
             "min": tr_min, "max": tr_max},
            {"bufferView": 2, "componentType": FLOAT, "count": n, "type": "VEC4",
             "min": rot_min, "max": rot_max},
        ],
        "animations": [{
            "channels": [
                {"sampler": 0, "target": {"node": 0, "path": "translation"}},
                {"sampler": 1, "target": {"node": 0, "path": "rotation"}},
            ],
            "samplers": [
                {"input": 0, "output": 1, "interpolation": "LINEAR"},
                {"input": 0, "output": 2, "interpolation": "LINEAR"},
            ],
        }],
    }
