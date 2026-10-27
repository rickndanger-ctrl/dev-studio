"""Read camera paths back from RealEstate10K text, camera-motion JSON, or glTF 2.0.

Each reader returns {'fps': float, 'frames': [...]}, and each frame is in the engine format:
{t, position: [x, y, z], rotation: [yaw, pitch, roll] in degrees, fov in degrees}.

Rotation is R = Ry(yaw) @ Rx(pitch) @ Rz(roll), the inverse of export.trajectory.rotation_matrix.
yaw and roll are unwrapped across frames so they stay continuous with the previous frame.

Limits:
- glTF has no fov track in core glTF, so every frame gets the camera yfov, which is the
  first frame's fov. Animated fov (dolly_zoom) is not recovered.
- RealEstate10K has no fps. It is inferred from the timestamps, rounded to 3 decimals.
- At pitch +-90 degrees yaw and roll are not unique. roll is set to 0 there.
"""
import base64
import binascii
import json
import math
import struct

from camera_motion.export.quat import quat_to_mat

GLTF_FLOAT = 5126
GLTF_COMPONENTS = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}
GLTF_DATA_PREFIXES = ("data:application/octet-stream;base64,", "data:application/gltf-buffer;base64,")
GIMBAL_EPS = 1e-12


def _euler(r):
    """(yaw, pitch, roll) in degrees for R = Ry(yaw) @ Rx(pitch) @ Rz(roll)."""
    pitch = math.asin(max(-1.0, min(1.0, -r[1][2])))
    if abs(r[1][2]) >= 1.0 - GIMBAL_EPS:
        # Gimbal lock: only yaw and pitch are determined; the roll is taken as zero.
        yaw = math.atan2(-r[2][0], r[0][0])
        roll = 0.0
    else:
        yaw = math.atan2(r[0][2], r[2][2])
        roll = math.atan2(r[1][0], r[1][1])
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def _unwrap(values):
    """Shift each angle by multiples of 360 so it is within 180 of the previous one."""
    out = [values[0]]
    for v in values[1:]:
        prev = out[-1]
        out.append(prev + ((v - prev + 180.0) % 360.0 - 180.0))
    return out


def _frames_from_poses(poses):
    """poses: list of (t, position, R, fov). Returns engine frames with unwrapped angles."""
    eulers = [_euler(R) for _, _, R, _ in poses]
    yaws = _unwrap([e[0] for e in eulers])
    rolls = _unwrap([e[2] for e in eulers])
    frames = []
    for i, (t, position, _, fov) in enumerate(poses):
        frames.append({
            "t": t,
            "position": list(position),
            "rotation": [yaws[i], eulers[i][1], rolls[i]],
            "fov": fov,
        })
    return frames


def _fps_from_times(times):
    if len(times) < 2 or times[1] == times[0]:
        raise ValueError("cannot infer fps from fewer than two distinct timestamps")
    return round(1.0 / (times[1] - times[0]), 3)


def read_re10k(lines):
    """Parse RealEstate10K text lines. '#' header and blank lines are skipped."""
    poses = []
    for number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        parts = text.split()
        if len(parts) != 19:
            raise ValueError(f"RealEstate10K line {number} needs 19 fields, got {len(parts)}")
        try:
            values = [float(v) for v in parts]
        except ValueError:
            raise ValueError(f"RealEstate10K line {number} has a non-numeric field") from None
        t = values[0] / 1e6
        fy = values[2]
        if not fy > 0:
            raise ValueError(f"RealEstate10K line {number} has a non-positive fy")
        fov = math.degrees(2.0 * math.atan(0.5 / fy))
        # Undo the OpenCV flip: the same diag(1, -1, -1) applied to the top three rows.
        rows = [[s * v for v in values[7 + 4 * i:11 + 4 * i]] for i, s in enumerate((1.0, -1.0, -1.0))]
        m = [row[:3] for row in rows]  # the w2c block is R^T
        R = [[m[j][i] for j in range(3)] for i in range(3)]
        translation = [rows[i][3] for i in range(3)]  # w2c translation = -R^T p
        position = [-(R[i][0] * translation[0] + R[i][1] * translation[1] + R[i][2] * translation[2])
                    for i in range(3)]
        poses.append((t, position, R, fov))
    if not poses:
        raise ValueError("no RealEstate10K frames found")
    frames = _frames_from_poses(poses)
    return {"fps": _fps_from_times([p[0] for p in poses]), "frames": frames, "fov_track": True}


def read_json(doc):
    """Parse the camera-motion JSON document: {fps, width, height, frames: [{t, K, c2w, w2c}]}."""
    if not isinstance(doc, dict) or not isinstance(doc.get("frames"), list):
        raise ValueError("JSON camera file needs a 'frames' list")
    if not doc["frames"]:
        raise ValueError("JSON camera file has no frames")
    poses = []
    for i, entry in enumerate(doc["frames"]):
        try:
            c2w = entry["c2w"]
            K = entry["K"]
            t = float(entry["t"])
            if len(c2w) != 4 or any(len(row) != 4 for row in c2w):
                raise ValueError("c2w must be 4x4")
            if len(K) != 3 or any(len(row) != 3 for row in K):
                raise ValueError("K must be 3x3")
            fy = float(K[1][1])
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f"JSON frame {i} is malformed: {err}") from None
        if not fy > 0:
            raise ValueError(f"JSON frame {i} has a non-positive fy")
        R = [[float(c2w[r][c]) for c in range(3)] for r in range(3)]
        position = [float(c2w[r][3]) for r in range(3)]
        poses.append((t, position, R, math.degrees(2.0 * math.atan(0.5 / fy))))
    fps = float(doc.get("fps") or _fps_from_times([p[0] for p in poses]))
    return {"fps": fps, "frames": _frames_from_poses(poses), "fov_track": True}


def _buffer_bytes(doc, index):
    buffers = doc.get("buffers")
    if not isinstance(buffers, list) or index >= len(buffers):
        raise ValueError(f"glTF buffer {index} is missing")
    buf = buffers[index]
    uri = buf.get("uri", "")
    prefix = next((p for p in GLTF_DATA_PREFIXES if uri.startswith(p)), None)
    if prefix is None:
        raise ValueError("glTF buffer must be an embedded base64 data URI")
    try:
        data = base64.b64decode(uri[len(prefix):], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("glTF buffer has invalid base64 data") from None
    if len(data) != buf.get("byteLength", len(data)):
        raise ValueError("glTF buffer length does not match byteLength")
    return data


def _accessor(doc, cache, index, kind):
    accessors = doc.get("accessors")
    if not isinstance(accessors, list) or index >= len(accessors):
        raise ValueError(f"glTF accessor {index} is missing")
    acc = accessors[index]
    if acc.get("componentType") != GLTF_FLOAT or acc.get("type") != kind:
        raise ValueError(f"glTF accessor {index} must be float {kind}")
    views = doc.get("bufferViews", [])
    if not isinstance(views, list) or acc.get("bufferView", -1) >= len(views):
        raise ValueError(f"glTF accessor {index} has no buffer view")
    view = views[acc["bufferView"]]
    buf_index = view.get("buffer", 0)
    if buf_index not in cache:
        cache[buf_index] = _buffer_bytes(doc, buf_index)
    data = cache[buf_index]
    comps = GLTF_COMPONENTS[kind]
    count = acc.get("count", 0)
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    if offset + count * comps * 4 > len(data):
        raise ValueError(f"glTF accessor {index} reads past the end of its buffer")
    flat = struct.unpack_from("<%df" % (count * comps), data, offset)
    return [tuple(flat[i * comps:(i + 1) * comps]) for i in range(count)]


def read_gltf(doc):
    """Parse a glTF 2.0 camera animation written by export.gltf (embedded float32 data)."""
    if not isinstance(doc, dict) or "asset" not in doc:
        raise ValueError("glTF document needs an 'asset' entry")
    animations = doc.get("animations")
    if not isinstance(animations, list) or not animations:
        raise ValueError("glTF document has no animations")
    channels = {}
    samplers = animations[0].get("samplers", [])
    for channel in animations[0].get("channels", []):
        target = channel.get("target", {})
        channels[target.get("path")] = (target.get("node"), samplers[channel["sampler"]])
    if "translation" not in channels or "rotation" not in channels:
        raise ValueError("glTF animation needs translation and rotation channels")

    node_index = channels["translation"][0]
    cache = {}
    times = _accessor(doc, cache, channels["translation"][1]["input"], "SCALAR")
    positions = _accessor(doc, cache, channels["translation"][1]["output"], "VEC3")
    quats = _accessor(doc, cache, channels["rotation"][1]["output"], "VEC4")
    if not (len(times) == len(positions) == len(quats)):
        raise ValueError("glTF keyframe counts do not match")

    nodes = doc.get("nodes", [])
    cameras = doc.get("cameras", [])
    try:
        cam_index = nodes[node_index]["camera"]
        yfov = float(cameras[cam_index]["perspective"]["yfov"])
    except (IndexError, KeyError, TypeError, ValueError):
        raise ValueError("glTF animated node has no perspective camera with yfov") from None
    fov = math.degrees(yfov)

    poses = []
    for t, pos, quat in zip(times, positions, quats):
        R = quat_to_mat(quat)
        poses.append((float(t[0]), list(pos), R, fov))
    extras = doc["asset"].get("extras", {}) if isinstance(doc["asset"], dict) else {}
    fps = float(extras.get("fps") or _fps_from_times([p[0] for p in poses]))
    # glTF carries one yfov for the whole animation, so it has no zoom track.
    return {"fps": fps, "frames": _frames_from_poses(poses), "fov_track": False}


def load(path):
    """Read a camera file, detecting the format from its content."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            doc = json.loads(text)
        except ValueError as err:
            raise ValueError(f"{path}: invalid JSON: {err}") from None
        if isinstance(doc, dict) and isinstance(doc.get("asset"), dict) and "version" in doc["asset"]:
            return read_gltf(doc)
        if isinstance(doc, dict) and "frames" in doc:
            return read_json(doc)
        raise ValueError(f"{path}: JSON is neither glTF (asset.version) nor camera-motion JSON (frames)")
    lines = text.splitlines()
    data = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if data and all(len(ln.split()) == 19 for ln in data):
        return read_re10k(lines)
    raise ValueError(f"{path}: not RealEstate10K text, camera-motion JSON or glTF")
