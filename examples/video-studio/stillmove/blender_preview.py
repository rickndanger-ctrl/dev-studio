"""Optional Blender preview of a mesh: a video of the camera move, then a short orbit.

write_script writes preview_render.py next to scene.py. It is a bpy-only script that runs after
scene.py in one Blender session:

    blender -b --factory-startup --python scene.py --python preview_render.py

The script renders the camera move that scene.py keyed, then ORBIT_FRAMES more frames. The orbit
poses are computed here, in Python (orbit_poses), and embedded in the script as a literal list, so
the math exists once. The camera orbits its last position around the origin by ORBIT_DEGREES (a sine:
0, +10, 0, -10, 0 degrees) and stays aimed at the origin. The output is H.264 MP4, blender-preview.mp4,
in the mesh folder.

Without Blender, run_preview prints one line to stderr and does nothing; the mesh is already written.
"""

import math
import os
import shutil
import subprocess
import sys

from stillmove import _cm

_cm.import_camera_motion()

from camera_motion.export.quat import make_continuous, mat_to_quat  # noqa: E402

SCRIPT_NAME = "preview_render.py"
VIDEO_NAME = "blender-preview.mp4"
SCENE_NAME = "scene.py"
MAC_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
ORBIT_FRAMES = 48  # 2 s at 24 fps
ORBIT_DEGREES = 10.0
FPS = 24
LONG_SIDE = 1080
SKIPPED = "blender-preview skipped: Blender not found"

# Lighting and world, as module constants: the script carries them, the test reads them back.
# Area lights: location (Blender world, aimed at the origin), energy (W), size (m).
KEY_LIGHT = {"location": (-2.0, -4.0, 6.0), "energy": 400.0, "size": 4.0}  # higher and more frontal
FILL_LIGHT = {"location": (3.0, -2.0, 2.0), "energy": 120.0, "size": 5.0}  # weaker, from the right
RIM_LIGHT = {"location": (0.0, 4.0, 5.0), "energy": 40.0, "size": 6.0}  # faint, from behind and above
LIGHTS = (("KeyLight", KEY_LIGHT), ("FillLight", FILL_LIGHT), ("RimLight", RIM_LIGHT))
# The world is darker than the paper: pad stretch at the border reads as a soft vignette, not a seam.
WORLD_FRACTION = 0.35  # of the paper's mean colour

_TEMPLATE = '''"""Blender preview of a stillmove mesh. Runs after scene.py in one Blender session:

  blender -b --factory-startup --python scene.py --python preview_render.py

It renders the camera move that scene.py keyed, then __ORBIT__ more frames: the camera orbits its last
position around the origin by __DEG__ degrees (a sine: 0, +10, 0, -10, 0) and stays aimed at the
origin. bpy only. Written by stillmove.
"""

import os

import bpy
from mathutils import Vector

MOVE_FRAMES = __MOVE__
ORBIT_FRAMES = __ORBIT__
ORBIT_DEGREES = __DEG__
# (location, (w, x, y, z)) for each orbit frame, from blender_preview.orbit_poses (not computed here).
ORBIT = __POSES__
# Each mesh object's picture, by object name. The files sit in the mesh folder (MESH_DIR).
IMAGES = __IMAGES__
MESH_DIR = __MESH_DIR__
FPS = __FPS__
WIDTH = __W__
HEIGHT = __H__
OUTPUT = __OUT__
WORLD_RGB = __WORLD__
LIGHTS = __LIGHTS__
SAMPLES = 16

scene = bpy.context.scene
cam = scene.camera
if cam is None:
    raise SystemExit("preview_render: scene.py must set the camera")

# The factory scene brings a default cube, light and camera: remove them, keep the mesh and the camera.
for name in ("Cube", "Light", "Camera"):
    obj = bpy.data.objects.get(name)
    if obj is not None and obj is not cam:
        bpy.data.objects.remove(obj, do_unlink=True)


def textured_material(image, alpha=False):
    """A principled material: the image as base colour, roughness 0.6, no emission."""
    mat = bpy.data.materials.new("StillmoveTexture")
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new('ShaderNodeOutputMaterial')
    bsdf = tree.nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Roughness'].default_value = 0.6
    if 'Emission Strength' in bsdf.inputs:
        bsdf.inputs['Emission Strength'].default_value = 0.0
    tex = tree.nodes.new('ShaderNodeTexImage')
    tex.image = image
    tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
    tree.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    if alpha:  # a foreground: the picture's alpha (the matte) makes its edges soft
        tree.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
        if hasattr(mat, 'surface_render_method'):
            mat.surface_render_method = 'BLENDED'
        else:
            mat.blend_method = 'BLEND'
    return mat


for obj in list(bpy.data.objects):
    if obj.type != 'MESH':
        continue
    # Match by object name ('still', 'backdrop'), then by its material name; each gets its own picture.
    names = [obj.name] + [slot.material.name for slot in obj.material_slots if slot.material is not None]
    file = next((IMAGES[name] for name in names if name in IMAGES), None)
    if file is None:
        continue
    image = bpy.data.images.load(os.path.join(MESH_DIR, file), check_existing=True)
    obj.data.materials.clear()
    obj.data.materials.append(textured_material(image, alpha=(obj.name == 'foreground')))
    if obj.name == 'foreground':  # the logo casts no shadow: a shadow would show as a smear on the paper
        obj.visible_shadow = False

# The orbit: keys after the move, at the poses computed in Python.
cam.rotation_mode = 'QUATERNION'
for k, (location, quat) in enumerate(ORBIT, start=1):
    scene.frame_set(MOVE_FRAMES + k)
    cam.location = location
    cam.rotation_quaternion = quat
    cam.keyframe_insert(data_path='location', frame=MOVE_FRAMES + k)
    cam.keyframe_insert(data_path='rotation_quaternion', frame=MOVE_FRAMES + k)

scene.frame_start = 1
scene.frame_end = MOVE_FRAMES + ORBIT_FRAMES
scene.render.fps = FPS
scene.render.fps_base = 1.0
scene.render.resolution_x = WIDTH
scene.render.resolution_y = HEIGHT
scene.render.resolution_percentage = 100

# Eevee: the key and fill lights shape the relief. The name changed in Blender 4.2 (EEVEE_NEXT).
for engine in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        continue
else:
    raise SystemExit("preview_render: no Eevee engine in this Blender")
scene.eevee.taa_render_samples = SAMPLES

# World colour: darker than the paper (WORLD_FRACTION of its mean), so the border reads as a vignette.
world = bpy.data.worlds.new("StillmoveWorld")
world.use_nodes = False
world.color = WORLD_RGB
scene.world = world


def area_light(name, cfg):
    """A square area light at cfg['location'], aimed at the origin."""
    data = bpy.data.lights.new(name, 'AREA')
    data.energy = cfg["energy"]
    data.shape = 'SQUARE'
    data.size = cfg["size"]
    obj = bpy.data.objects.new(name, data)
    obj.location = cfg["location"]
    obj.rotation_mode = 'QUATERNION'
    # -Z (the light's facing) points from the light to the origin, with +Y up where it can be.
    obj.rotation_quaternion = Vector(cfg["location"]).to_track_quat('Z', 'Y')
    scene.collection.objects.link(obj)
    return obj


for light_name, light_cfg in LIGHTS:
    area_light(light_name, light_cfg)

scene.render.image_settings.media_type = 'VIDEO'  # Blender 4.3 and later: a movie, not an image
scene.render.image_settings.file_format = 'FFMPEG'
scene.render.ffmpeg.format = 'MPEG4'
scene.render.ffmpeg.codec = 'H264'
scene.render.filepath = OUTPUT
bpy.ops.render.render(animation=True)
'''


def preview_size(width, height):
    """The output size: the long side is LONG_SIDE, the short side keeps the aspect, both even."""
    if width >= height:
        w = LONG_SIDE
        h = 2 * round(LONG_SIDE * height / width / 2)
    else:
        h = LONG_SIDE
        w = 2 * round(LONG_SIDE * width / height / 2)
    return max(2, w), max(2, h)


def _rotate_z(vector, angle):
    x, y, z = vector
    c, s_ = math.cos(angle), math.sin(angle)
    return (c * x - s_ * y, s_ * x + c * y, z)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n == 0:
        raise ValueError("a zero vector has no direction")
    return (v[0] / n, v[1] / n, v[2] / n)


def aim_rotation(location):
    """3x3 rotation whose -Z axis points from location to the origin, with +Y as close to world +Z as
    it can be (Blender's to_track_quat('-Z', 'Y')). Columns are the camera's x, y, z in Blender world."""
    z = _unit(location)  # the camera's +Z points away from the origin, so -Z looks at it
    x = _cross((0.0, 0.0, 1.0), z)
    if math.sqrt(sum(c * c for c in x)) < 1e-9:  # the camera is straight above or below the origin
        x = _cross((0.0, 1.0, 0.0), z)
    x = _unit(x)
    y = _cross(z, x)
    return [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]


def orbit_poses(last_location):
    """The ORBIT_FRAMES poses of the orbit, in Blender coordinates.

    last_location: the camera's last position (Blender world). Frame k (1..ORBIT_FRAMES) moves it
    about the Z axis through the origin by ORBIT_DEGREES * sin(2 pi k / ORBIT_FRAMES) and aims the
    camera at the origin. Returns [(location, (w, x, y, z))], quaternions with no sign jumps.
    """
    base = tuple(float(c) for c in last_location)
    if len(base) != 3:
        raise ValueError("the last location needs three numbers")
    locations, matrices = [], []
    for k in range(1, ORBIT_FRAMES + 1):
        angle = math.radians(ORBIT_DEGREES * math.sin(2.0 * math.pi * k / ORBIT_FRAMES))
        loc = _rotate_z(base, angle)
        locations.append(loc)
        matrices.append(aim_rotation(loc))
    quats = make_continuous(mat_to_quat(m) for m in matrices)
    return [(loc, (float(w), float(x), float(y), float(z)))
            for loc, (x, y, z, w) in zip(locations, quats)]


def script_text(move_frames, width, height, output_path, orbit, world_rgb=(0.9, 0.9, 0.9), images=None):
    """The preview_render.py source for a move of move_frames camera frames, then the orbit poses.

    images maps each mesh object's name ('still', 'backdrop') to its picture's file name in the mesh
    folder (the folder of output_path). Default: the foreground texture only.
    """
    if move_frames < 1:
        raise ValueError("the move needs at least one frame")
    if len(orbit) != ORBIT_FRAMES:
        raise ValueError(f"the orbit needs {ORBIT_FRAMES} poses, got {len(orbit)}")
    images = {"still": "texture.png"} if images is None else dict(images)
    w, h = preview_size(width, height)
    poses = "[\n" + "".join(f"    {pose!r},\n" for pose in orbit) + "]"
    return (_TEMPLATE
            .replace("__POSES__", poses)
            .replace("__IMAGES__", repr({str(k): str(v) for k, v in sorted(images.items())}))
            .replace("__MESH_DIR__", repr(os.path.dirname(os.path.abspath(output_path))))
            .replace("__ORBIT__", str(ORBIT_FRAMES))
            .replace("__DEG__", repr(float(ORBIT_DEGREES)))
            .replace("__MOVE__", str(int(move_frames)))
            .replace("__FPS__", str(FPS))
            .replace("__W__", str(w))
            .replace("__H__", str(h))
            .replace("__OUT__", repr(os.path.abspath(output_path)))
            .replace("__WORLD__", repr(tuple(round(float(c) * WORLD_FRACTION, 6) for c in world_rgb)))
            .replace("__LIGHTS__", repr([(name, dict(cfg)) for name, cfg in LIGHTS])))


def paper_colour(image_path):
    """The mean colour of the picture's border (linear 0-1 is not applied: display values are used).

    Used as the world colour, so the background sits near the paper of the logo. Falls back to a
    light grey if the image cannot be read.
    """
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            pixels = [rgb.getpixel((x, y)) for x in range(w) for y in (0, h - 1)]
            pixels += [rgb.getpixel((x, y)) for y in range(h) for x in (0, w - 1)]
    except (OSError, ValueError):
        return (0.9, 0.9, 0.9)
    n = float(len(pixels))
    return tuple(sum(p[i] for p in pixels) / n / 255.0 for i in range(3))


def write_script(outdir, move_frames, width, height, orbit, world_rgb=(0.9, 0.9, 0.9), images=None):
    """Write preview_render.py into outdir (next to scene.py). Returns its path."""
    path = os.path.join(outdir, SCRIPT_NAME)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(script_text(move_frames, width, height, os.path.join(outdir, VIDEO_NAME), orbit, world_rgb,
                            images))
    return path


def find_blender():
    """The Blender executable, or None: `blender` on PATH, else the macOS app bundle."""
    found = shutil.which("blender")
    if found:
        return found
    if os.path.exists(MAC_BLENDER):
        return MAC_BLENDER
    return None


def blender_command(blender, outdir):
    """The command line: scene.py, then preview_render.py, in one background Blender session."""
    return [blender, "-b", "--factory-startup", "--python", SCENE_NAME, "--python", SCRIPT_NAME]


def run_preview(outdir):
    """Run Blender on the mesh folder if it is installed. Returns the MP4 path, or None when skipped.

    A Blender that is found but fails (or exits 0 without writing the MP4) raises RuntimeError with
    its last output line.
    """
    blender = find_blender()
    if blender is None:
        print(SKIPPED, file=sys.stderr)
        return None
    result = subprocess.run(blender_command(blender, outdir), cwd=outdir, capture_output=True, text=True)
    video = os.path.join(outdir, VIDEO_NAME)
    if result.returncode != 0 or not os.path.isfile(video):
        # Blender can exit 0 after a Python error in the script, so the file is the real check.
        lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
        reason = lines[-1] if lines else "blender exited with status %d" % result.returncode
        raise RuntimeError("blender preview failed: %s" % reason)
    return video
