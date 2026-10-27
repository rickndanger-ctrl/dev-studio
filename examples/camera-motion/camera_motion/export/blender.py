"""Blender export: a self-contained bpy script that keys one camera over the frames.

Our world is Y-up (OpenGL). Blender is Z-up, so a world point (x, y, z) becomes
(x, -z, y). Mw applies that change of basis; camera local axes already match Blender
(-Z forward, +Y up), so the camera side needs no flip.
Blender's rotation_quaternion is (w, x, y, z); quat.mat_to_quat returns (x, y, z, w).
"""
from camera_motion.export.quat import make_continuous, mat_to_quat
from camera_motion.export.trajectory import pose_matrix

MW = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]

SENSOR_HEIGHT_MM = 24.0

_TEMPLATE = '''import bpy
import math

NAME = {name!r}
FPS = {fps!r}
WIDTH = {width!r}
HEIGHT = {height!r}
SENSOR_HEIGHT_MM = {sensor!r}
# One entry per frame: (location, rotation_quaternion as (w, x, y, z), vertical fov in degrees).
FRAMES = {frames}

scene = bpy.context.scene
scene.render.fps = FPS
scene.frame_start = 1
scene.frame_end = len(FRAMES)
scene.render.resolution_x = WIDTH
scene.render.resolution_y = HEIGHT
scene.render.resolution_percentage = 100

cam_data = bpy.data.cameras.new(NAME)
cam_data.sensor_fit = 'VERTICAL'
cam_data.sensor_height = SENSOR_HEIGHT_MM
cam_obj = bpy.data.objects.new(NAME, cam_data)
scene.collection.objects.link(cam_obj)
scene.camera = cam_obj
cam_obj.rotation_mode = 'QUATERNION'

for frame, (location, quat, fov) in enumerate(FRAMES, start=1):
    cam_obj.location = location
    cam_obj.rotation_quaternion = quat
    cam_data.angle_y = math.radians(fov)
    cam_obj.keyframe_insert(data_path='location', frame=frame)
    cam_obj.keyframe_insert(data_path='rotation_quaternion', frame=frame)
    cam_data.keyframe_insert(data_path='lens', frame=frame)
'''


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))]
            for i in range(len(a))]


def _blender_pose(frame):
    """(location, (x, y, z, w), fov) for one engine frame, in Blender's Z-up world."""
    c2w = pose_matrix(frame)
    rot = [row[:3] for row in c2w[:3]]
    rot_b = _mat_mul(MW, rot)
    pos = [c2w[i][3] for i in range(3)]
    loc_b = [sum(MW[i][k] * pos[k] for k in range(3)) for i in range(3)]
    return (tuple(loc_b), mat_to_quat(rot_b), float(frame["fov"]))


def to_blender(frames, fps=24, width=1024, height=576, name=""):
    """Return a self-contained bpy script that keys a camera over frames (one keyframe per frame).

    Quaternions are made continuous across frames, so a long move never takes the long way
    between keys, then written in Blender's (w, x, y, z) order.
    """
    poses = [_blender_pose(f) for f in frames]
    quats = make_continuous(q for _, q, _ in poses)
    rows = [(loc, (w, x, y, z), fov)
            for (loc, _, fov), (x, y, z, w) in zip(poses, quats)]
    literal = "[\n" + "".join(f"    {row!r},\n" for row in rows) + "]"
    return _TEMPLATE.format(
        name=name or "Camera",
        fps=int(fps),
        width=int(width),
        height=int(height),
        sensor=SENSOR_HEIGHT_MM,
        frames=literal,
    )
