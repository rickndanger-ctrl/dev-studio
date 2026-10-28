"""Still-to-mesh: a textured grid mesh of the still at its depth, and a Blender scene that keys
the camera along the same path.

Coordinates. A grid point of the image is back-projected with its depth and the frame-0 pixel
intrinsics, giving a point in camera 0 (OpenCV axes). frame 0's pose takes it into the camera-motion
world (OpenGL, Y up). camera_motion.export.blender.MW maps a world point (x, y, z) to Blender's
(x, -z, y). The OBJ is written in Blender coordinates and imported with forward Y and up Z, so
no axis change happens at import.

Grid. An n x m grid of vertices spans the frame corner to corner. Vertex (i, j) has index j*n + i.
Each cell (n-1) x (m-1) is one quad. UV: u = i/(n-1), v = 1 - j/(m-1), so the image's top row is v = 1.
"""

import ast
import math
import os
import shutil

import numpy as np
from PIL import Image

from stillmove import _cm
from stillmove import blender_preview as preview
from stillmove import mesh_layers, mesh_pad

_cm.import_camera_motion()

from camera_motion.export.blender import MW, SENSOR_HEIGHT_MM, _TEMPLATE  # noqa: E402
from camera_motion.export.quat import make_continuous, mat_to_quat  # noqa: E402

from stillmove import fitting  # noqa: E402
from stillmove.depth import BACKEND_NAMES, check_depth, get_backend, smooth_depth  # noqa: E402
from stillmove.render import _load_image  # noqa: E402

AUTO_GRID = "auto"
AUTO_LONG_SIDE = 256
DEFAULT_NEAR = 3.0
DEFAULT_FAR = 8.0
OBJ_NAME = "mesh.obj"
MTL_NAME = "mesh.mtl"
FOREGROUND_TEXTURE = "foreground.png"
SCENE_NAME = "scene.py"

_OBJ_IMPORT = '''import bpy

MESH_PATH = {mesh!r}

try:
    bpy.ops.wm.obj_import(filepath=MESH_PATH, forward_axis='Y', up_axis='Z')
except AttributeError:  # Blender before 4.0 has only the old importer
    bpy.ops.import_scene.obj(filepath=MESH_PATH, axis_forward='Y', axis_up='Z')

'''


def parse_grid(text):
    """'256x144' -> (256, 144). Both sides must be at least 2."""
    try:
        n, m = (int(part) for part in text.lower().split("x"))
    except ValueError:
        raise ValueError(f"--grid must look like 256x144, got {text!r}") from None
    if n < 2 or m < 2:
        raise ValueError("--grid needs at least 2 points on each side")
    return n, m


def resolve_grid(text, width, height):
    """The grid for an image of width x height.

    'auto' puts AUTO_LONG_SIDE vertices on the long side and gives the short side the image's
    aspect, at least 2 (1920x1080 -> 256x144, square -> 256x256, portrait -> 144x256). Anything else
    is an explicit WxH, parsed as parse_grid does.
    """
    if str(text).strip().lower() != AUTO_GRID:
        return parse_grid(text)
    if width < 1 or height < 1:
        raise ValueError("the image needs a size to pick an automatic grid")
    short = max(2, round(AUTO_LONG_SIDE * min(width, height) / max(width, height)))
    return (AUTO_LONG_SIDE, short) if width >= height else (short, AUTO_LONG_SIDE)


def make_depth(spec, rgb, near=DEFAULT_NEAR, far=DEFAULT_FAR):
    """Depth (HxW float32, > 0) for an image, smoothed the way render smooths it.

    spec is a depth backend name (plane, gradient, da2-small; fake is an alias for plane) or a
    grayscale .png (white near, black far: depth = far - (far - near) * g / 255). Either way the
    depth goes through smooth_depth once, the same as stillmove render.
    """
    height, width = rgb.shape[:2]
    name = "plane" if spec == "fake" else spec
    if name in BACKEND_NAMES:
        depth = check_depth(get_backend(name).depth(rgb), (height, width))
    else:
        if not str(spec).lower().endswith(".png"):
            raise ValueError(f"--depth must be one of {', '.join(BACKEND_NAMES + ('fake',))} or a .png file, got {spec!r}")
        if not (far > near > 0):
            raise ValueError("--near and --far need 0 < near < far")
        with Image.open(spec) as img:
            gray = img.convert("L").resize((width, height), Image.BILINEAR)
            g = np.asarray(gray, dtype=np.float64) / 255.0
        depth = check_depth((far - (far - near) * g).astype(np.float32), (height, width))
    return check_depth(smooth_depth(depth, rgb), (height, width))


def grid_vertices(cam0, depth, n, m):
    """The n*m grid points in Blender coordinates, shape (n*m, 3), from frame 0's pose and intrinsics."""
    height, width = depth.shape
    fx, fy = cam0.K[0, 0], cam0.K[1, 1]
    cx, cy = cam0.K[0, 2], cam0.K[1, 2]
    c2w = np.linalg.inv(cam0.w2c)  # camera 0 to the camera-motion world (OpenCV camera axes)
    out = np.zeros((n * m, 3), dtype=np.float64)
    for j in range(m):
        py = j * (height - 1) / (m - 1)
        for i in range(n):
            px = i * (width - 1) / (n - 1)
            z = float(depth[int(round(py)), int(round(px))])
            # The pixel centre is (px + 0.5, py + 0.5): the image centre is the centre of the grid.
            x_cam = (px + 0.5 - cx) * z / fx
            y_cam = (py + 0.5 - cy) * z / fy
            # inv(w2c) of the OpenCV pose takes a point in OpenCV camera axes to the camera-motion world
            # (the axis flip between OpenCV and OpenGL cancels inside it), so no flip is needed here.
            world = (c2w @ np.array([x_cam, y_cam, z, 1.0]))[:3]
            out[j * n + i] = np.array([MW[r][0] * world[0] + MW[r][1] * world[1] + MW[r][2] * world[2]
                                       for r in range(3)])
    return out


def _world_point(cam0, qx, qy, z):
    """The continuous pixel (qx, qy) at depth z, in Blender coordinates, placed as grid_vertices places a point."""
    fx, fy = cam0.K[0, 0], cam0.K[1, 1]
    cx, cy = cam0.K[0, 2], cam0.K[1, 2]
    c2w = np.linalg.inv(cam0.w2c)
    world = (c2w @ np.array([(qx - cx) * z / fx, (qy - cy) * z / fy, z, 1.0]))[:3]
    return tuple(sum(MW[r][k] * world[k] for k in range(3)) for r in range(3))


def _plane_vertices(cam0, plane, n, m, rx, ry, width, height):
    """The padded grid's vertices on the backdrop plane (1 / z = a qx + b qy + c). Every vertex lies on it,
    the rings included, so the backdrop is one flat surface."""
    sx, sy = mesh_pad._steps(width, height, n, m)
    a, b, c = plane
    out = []
    for jp in range(m + 2 * ry):
        qy = (jp - ry) * sy + 0.5
        for ip in range(n + 2 * rx):
            qx = (ip - rx) * sx + 0.5
            inverse = a * qx + b * qy + c
            if inverse <= 0:
                raise ValueError("the backdrop plane passes behind the camera at the mesh edge")
            out.append(_world_point(cam0, qx, qy, 1.0 / inverse))
    return out


def _foreground_quad(cam0, alpha, near, width, height):
    """Four corners of the matte's bounding box, at the foreground depth, with their UVs."""
    x0, y0, x1, y1 = mesh_layers.foreground_bounds(alpha)
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    verts = [_world_point(cam0, qx, qy, near) for qx, qy in corners]
    uvs = [(qx / width, 1.0 - qy / height) for qx, qy in corners]
    return verts, uvs


def _write_foreground_png(path, colour, alpha):
    """foreground.png: the matting colour in RGB and the matte in alpha."""
    rgb = np.clip(np.rint(colour), 0, 255).astype(np.uint8)
    a = np.clip(np.rint(np.asarray(alpha, dtype=np.float64) * 255.0), 0, 255).astype(np.uint8)
    Image.fromarray(np.dstack([rgb, a])).save(path)


def _write_matte_mesh(image_path, cams, depth, rgb, matte, grid, outdir, blender_preview, fit_record=None):
    """The matte mesh. 'backdrop': the clean plate on one plane fitted to the background, padded like the
    grid. 'foreground': one quad over the matte's bounding box at the foreground depth, textured with
    foreground.png (the matting colour and the matte as alpha). No per-cell faces, so no stair-steps."""
    from stillmove.layers import Layers, _foreground_colour

    n0, m0 = grid
    height, width = depth.shape
    cam0 = cams.frames[0]
    alpha = np.asarray(matte.alpha(rgb), dtype=np.float32)
    layers = Layers(rgb, depth, alpha)
    plate = mesh_layers.smooth_plate(layers.plate, layers.alpha)  # the clean paper, no refill patches
    plane = mesh_layers.background_plane(depth, layers.alpha)
    key_rows = _key_rows(cams)
    far_points = _plane_vertices(cam0, plane, n0, m0, 0, 0, width, height)
    rx, ry = mesh_pad.plan_rings(cams, key_rows, far_points, n0, m0)
    n, m = n0 + 2 * rx, m0 + 2 * ry
    verts = _plane_vertices(cam0, plane, n0, m0, rx, ry, width, height)
    uv = mesh_pad.pad_uvs(n0, m0, rx, ry)
    fg_depth = mesh_layers.foreground_depth(plane, layers.alpha, layers.near)
    quad, quad_uv = _foreground_quad(cam0, layers.alpha, fg_depth, width, height)
    colour = _foreground_colour(rgb, layers.alpha, layers.plate)  # the matting colour F

    os.makedirs(outdir, exist_ok=True)
    ext = os.path.splitext(image_path)[1].lower() or ".png"
    shutil.copyfile(image_path, os.path.join(outdir, "texture" + ext))
    Image.fromarray(plate).save(os.path.join(outdir, mesh_layers.BACKDROP_TEXTURE))
    _write_foreground_png(os.path.join(outdir, FOREGROUND_TEXTURE), colour, layers.alpha)

    lines = [f"mtllib {MTL_NAME}", "o backdrop", "usemtl backdrop"]
    lines += [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in verts]
    lines += [f"vt {u:.9g} {v:.9g}" for u, v in uv]
    faces = 0
    for j in range(m - 1):
        for i in range(n - 1):
            a, b = j * n + i, j * n + i + 1
            c, d = (j + 1) * n + i + 1, (j + 1) * n + i
            lines.append("f " + " ".join(f"{k + 1}/{k + 1}" for k in (a, b, c, d)))
            faces += 1
    base, uv_base = len(verts), len(uv)
    lines += ["o foreground", "usemtl foreground"]
    lines += [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in quad]
    lines += [f"vt {u:.9g} {v:.9g}" for u, v in quad_uv]
    lines.append("f " + " ".join(f"{base + k + 1}/{uv_base + k + 1}" for k in range(4)))
    faces += 1
    with open(os.path.join(outdir, OBJ_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    mtl = (f"newmtl backdrop\nKd 1.0 1.0 1.0\nmap_Kd {mesh_layers.BACKDROP_TEXTURE}\n"
           f"newmtl foreground\nKd 1.0 1.0 1.0\nmap_Kd {FOREGROUND_TEXTURE}\n")
    with open(os.path.join(outdir, MTL_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write(mtl)
    with open(os.path.join(outdir, SCENE_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write(key_script(cams, name="still", mesh_path=os.path.abspath(os.path.join(outdir, OBJ_NAME))))
    files = [OBJ_NAME, MTL_NAME, "texture" + ext, mesh_layers.BACKDROP_TEXTURE, FOREGROUND_TEXTURE, SCENE_NAME]
    if blender_preview:
        orbit = preview.orbit_poses(key_rows[-1][0])
        images = {"backdrop": mesh_layers.BACKDROP_TEXTURE, "foreground": FOREGROUND_TEXTURE}
        preview.write_script(outdir, len(cams.frames), cams.width, cams.height, orbit,
                             preview.paper_colour(image_path), images)
        files.append(preview.SCRIPT_NAME)
    return {"vertices": len(verts) + len(quad), "faces": faces, "files": files, "fit": fit_record}


def _fov_degrees(K, height):
    return 2.0 * math.degrees(math.atan(height / (2.0 * K[1, 1])))


def _key_rows(cams):
    """(location, (w, x, y, z), fov) per frame, as camera_motion.export.blender writes them."""
    gl_to_cv = np.diag([1.0, -1.0, -1.0, 1.0])
    poses = []
    for frame in cams.frames:
        c2w_cv = np.linalg.inv(frame.w2c)
        # The same camera with OpenGL axes (camera-motion's convention). Plain floats: numpy 2 prints
        # np.float64 as 'np.float64(...)', which is not a literal the scene script can read back.
        c2w_gl = (c2w_cv @ gl_to_cv).tolist()
        rot_b = [[float(sum(MW[i][k] * c2w_gl[k][j] for k in range(3))) for j in range(3)] for i in range(3)]
        loc_b = tuple(float(sum(MW[i][k] * c2w_gl[k][3] for k in range(3))) for i in range(3))
        poses.append((loc_b, mat_to_quat(rot_b), _fov_degrees(frame.K, cams.height)))
    quats = make_continuous(q for _, q, _ in poses)
    return [(loc, (w, x, y, z), fov) for (loc, _, fov), (x, y, z, w) in zip(poses, quats)]


def key_script(cams, name="still", mesh_path=None):
    """The Blender script: import the mesh, then key the camera over every frame.

    The keying block is camera_motion.export.blender's own template; only the frame rows are ours.
    """
    rows = _key_rows(cams)
    literal = "[\n" + "".join(f"    {row!r},\n" for row in rows) + "]"
    keyed = _TEMPLATE.format(name=name, fps=int(cams.fps), width=int(cams.width), height=int(cams.height),
                             sensor=SENSOR_HEIGHT_MM, frames=literal)
    return _OBJ_IMPORT.format(mesh=mesh_path or OBJ_NAME) + keyed


def write_mesh(image_path, cams, depth_spec, grid, outdir, near=DEFAULT_NEAR, far=DEFAULT_FAR, name="still",
               blender_preview=False, matte=None, fit_margin=None):
    """Write mesh.obj, mesh.mtl, the copied image (texture) and scene.py into outdir.

    The image is read at the camera size, as stillmove renders it. The texture is a copy of the
    file itself. With blender_preview, preview_render.py is written next to scene.py as well.
    fit_margin None keys the camera as given; a margin keys the fitted camera (stillmove.fitting),
    with the subject taken from the depth foreground (or, with a matte, its foreground at that depth).

    With a matte (an object with alpha(rgb)) the mesh is the matte mesh: see _write_matte_mesh.
    Without a matte, the object is 'still' and the cut is the depth-ratio cut.
    Returns a summary dict: vertices, faces, the file names written, and fit (the record, or None).
    """
    n, m = grid
    rgb = _load_image(image_path, cams.width, cams.height)
    depth = make_depth(depth_spec, rgb, near, far)
    from stillmove import mesh_layers

    fit_alpha = fit_near = None
    if matte is not None:
        from stillmove.layers import Layers

        fit_alpha = np.asarray(matte.alpha(rgb), dtype=np.float32)
        fit_near = Layers(rgb, depth, fit_alpha).near  # the subject: the matte's foreground, at its depth
    cams, fit_record = fitting.fitted(cams, depth, alpha=fit_alpha, fg_depth=fit_near, margin=fit_margin)
    if matte is not None:
        return _write_matte_mesh(image_path, cams, depth, rgb, matte, grid, outdir, blender_preview, fit_record)
    n0, m0 = n, m  # the grid as the picture sees it; the padded grid is n x m below
    main_name = "still"
    layer = mesh_layers.backdrop(depth, rgb)  # (back_depth, back_rgb) when there is a cut, else None
    main_depth = depth
    main_keep = mesh_layers.keep_faces(depth, n0, m0)  # no face bridges a depth cut
    far_points = list(grid_vertices(cams.frames[0], main_depth, n0, m0))
    if layer is not None:
        far_points += grid_vertices(cams.frames[0], layer[0], n0, m0)  # the backdrop can be the farthest layer
    key_rows = _key_rows(cams)
    rx, ry = mesh_pad.plan_rings(cams, key_rows, far_points, n0, m0)
    verts = mesh_pad.pad_vertices(cams.frames[0], main_depth, n0, m0, rx, ry)
    uv = mesh_pad.pad_uvs(n0, m0, rx, ry)
    n, m = n0 + 2 * rx, m0 + 2 * ry  # the face loop below runs over the padded grid
    orbit = preview.orbit_poses(key_rows[-1][0])

    os.makedirs(outdir, exist_ok=True)
    ext = os.path.splitext(image_path)[1].lower() or ".png"
    texture = "texture" + ext
    shutil.copyfile(image_path, os.path.join(outdir, texture))

    lines = [f"mtllib {MTL_NAME}", f"o {main_name}", f"usemtl {main_name}"]
    lines += [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in verts]
    lines += [f"vt {u:.9g} {v:.9g}" for u, v in uv]
    keep = mesh_pad.pad_keep(main_keep, rx, ry)
    faces = 0
    for j in range(m - 1):
        for i in range(n - 1):
            if not keep[j, i]:
                continue
            a, b = j * n + i, j * n + i + 1
            c, d = (j + 1) * n + i + 1, (j + 1) * n + i
            idx = [a, b, c, d]
            lines.append("f " + " ".join(f"{k + 1}/{k + 1}" for k in idx))
            faces += 1
    mtl = [f"newmtl {main_name}\nKd 1.0 1.0 1.0\nmap_Kd {texture}\n"]

    # The backdrop: a second object behind the cut, with its own texture and material.
    if layer is not None:
        back_depth, back_rgb = layer
        Image.fromarray(back_rgb).save(os.path.join(outdir, mesh_layers.BACKDROP_TEXTURE))
        back_verts = mesh_pad.pad_vertices(cams.frames[0], back_depth, n0, m0, rx, ry)
        base = len(verts)  # the backdrop's vertices follow the main object's in the OBJ
        lines += ["o backdrop", f"usemtl {mesh_layers.BACKDROP_MATERIAL}"]
        lines += [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in back_verts]
        for j in range(m - 1):
            for i in range(n - 1):  # the backdrop is whole: every face, shared uv with the main object
                a, b = j * n + i, j * n + i + 1
                c, d = (j + 1) * n + i + 1, (j + 1) * n + i
                lines.append("f " + " ".join(f"{k + base + 1}/{k + 1}" for k in (a, b, c, d)))
        mtl.append(f"newmtl {mesh_layers.BACKDROP_MATERIAL}\nKd 1.0 1.0 1.0\n"
                   f"map_Kd {mesh_layers.BACKDROP_TEXTURE}\n")
    with open(os.path.join(outdir, OBJ_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    with open(os.path.join(outdir, MTL_NAME), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(mtl))

    scene_path = os.path.join(outdir, SCENE_NAME)
    with open(scene_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(key_script(cams, name=name, mesh_path=os.path.abspath(os.path.join(outdir, OBJ_NAME))))
    files = [OBJ_NAME, MTL_NAME, texture, SCENE_NAME]
    if layer is not None:
        files.append(mesh_layers.BACKDROP_TEXTURE)
    if blender_preview:
        images = {main_name: texture}
        if layer is not None:
            images["backdrop"] = mesh_layers.BACKDROP_TEXTURE
        preview.write_script(outdir, len(cams.frames), cams.width, cams.height, orbit,
                             preview.paper_colour(image_path), images)
        files.append(preview.SCRIPT_NAME)
    return {"vertices": len(verts), "faces": faces, "files": files, "fit": fit_record}


def scene_frames(script_text):
    """The FRAMES literal of a scene script, parsed (for tests and checks)."""
    tree = ast.parse(script_text)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "FRAMES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("no FRAMES in the scene script")
