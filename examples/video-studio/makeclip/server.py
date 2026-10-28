"""Make a clip: a one-page tool to pick or drop a picture, pick a move, render, watch and download.

The contract is API.md next to this file. Stdlib only: the render runs in a subprocess under
render_python (this server's own Python by default, MAKECLIP_RENDER_PYTHON overrides it), never inside
this server. Binds to 127.0.0.1 only.

  makeclip [--port 8820]          or: python3 -m makeclip [--port 8820]
                                  (the folders are ~/Pictures/stillmove-input and -output; plan files for
                                  "Make a promo" are read from ~/Pictures, never written)
"""

import argparse
import datetime
import errno
import json
import math
import os
import re
import shutil
import socketserver
import struct
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO_STUDIO = os.path.dirname(HERE)

from stillmove._cm import import_camera_motion  # noqa: E402  (finds the sibling camera-motion checkout)

import_camera_motion()

import camera_motion  # noqa: E402
from camera_motion.engine.camera_path import path as camera_path  # noqa: E402
from camera_motion.export.formats import to_json  # noqa: E402
from camera_motion.presets.validate import load_json  # noqa: E402

import shotplan as shotplan_checker  # noqa: E402  (its check validates a promo plan before it renders)
from stillmove import sizes as stillmove_sizes  # noqa: E402  (output shapes; read at call time)

CAMERA_MOTION = os.path.dirname(os.path.dirname(os.path.abspath(camera_motion.__file__)))  # the folder holding camera_motion/
RENDER_PYTHON_ENV = "MAKECLIP_RENDER_PYTHON"

HOST = "127.0.0.1"
DEFAULT_PORT = 8820
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
REQUIRED_HEADER = ("X-Requested-With", "makeclip")
MAX_JSON = 64 * 1024
MAX_UPLOAD = 25 * 1024 * 1024
FPS = 24
SMOOTH = (0, 2, 4)  # 0 is off; N renders at fps / N, then interpolates N times (stillmove render --smooth)
CLIP_LIMIT = 20
ISNET_MODEL = os.path.join(os.path.expanduser("~"), "models", "isnet-general-use", "isnet-general-use.onnx")
DEPTH_MODEL = os.path.join(os.path.expanduser("~"), "models", "depth-anything-v2-small", "model.onnx")
RIFE_MODEL = os.path.join(os.path.expanduser("~"), "models", "rife", "rife_ifnet_midpoint.onnx")
ESRGAN_MODEL = os.path.join(os.path.expanduser("~"), "models", "realesrgan", "realesr-general-x4v3.onnx")
# "every" renders each of these into its own folder (stillmove render --sizes), plus sizes.zip.
EVERY = "every"
EVERY_SIZES = ("square", "wide", "vertical")
CLIP_TYPES = {"mp4": "video/mp4", "gif": "image/gif", "zip": "application/zip"}
SECONDS = {"min": 2, "max": 8, "default": 4}
# preset id -> (param, unit, min, max, default). Orbits keep the current distance (no radius).
RANGES = {
    "dolly_in": ("distance", "scene units", 0.1, 0.6, 0.4),
    "dolly_out": ("distance", "scene units", 0.1, 0.6, 0.4),
    "dolly_left": ("distance", "scene units", 0.1, 0.4, 0.25),
    "dolly_right": ("distance", "scene units", 0.1, 0.4, 0.25),
    "dolly_zoom_in": ("distance", "scene units", 0.1, 0.5, 0.3),
    "dolly_zoom_out": ("distance", "scene units", 0.1, 0.5, 0.3),
    "crane_up": ("distance", "scene units", 0.1, 0.4, 0.25),
    "crane_down": ("distance", "scene units", 0.1, 0.4, 0.25),
    "orbit_left": ("angle", "degrees", 2, 12, 8),
    "orbit_right": ("angle", "degrees", 2, 12, 8),
    "pan_left": ("angle", "degrees", 2, 10, 6),
    "pan_right": ("angle", "degrees", 2, 10, 6),
    "tilt_up": ("angle", "degrees", 2, 8, 5),
    "tilt_down": ("angle", "degrees", 2, 8, 5),
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CLIP_DIR = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-\d{8}-\d{6}$")
JSON_TYPE = "application/json"
UPLOAD_TYPES = {"image/png": "png", "image/jpeg": "jpg"}
PNG_SIG = b"\x89PNG\r\n\x1a\n"
JPEG_SIG = b"\xff\xd8\xff"
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
# Make a promo (PROMO.md, part B): 2 to 6 pictures in one plan; plan files are read from PLANS_ROOT,
# which is never written to; plan files are looked for at most PLAN_FOLDERS folders deep.
PROMO_MIN, PROMO_MAX = 2, 6
PLAN_FOLDERS = 3
PLAN_MAX = 1024 * 1024


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def slug(text):
    """a-z0-9 and '-' only, at most 40 characters; 'picture' when nothing is left."""
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40].strip("-")
    return out or "picture"


def _stamp(now=None):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def fit_notes(clip_dir):
    """The keep-in-frame notes a finished render wrote into its manifest, in shot order. Empty when
    the subject never needed the move shortened, or the manifest is missing or unreadable."""
    try:
        manifest = load_json(os.path.join(clip_dir, "manifest.json"))
    except (OSError, ValueError):
        return []
    if not isinstance(manifest, dict):
        return []
    if isinstance(manifest.get("shots"), list):  # a promo: one record per shot
        records = [shot.get("fit") for shot in manifest["shots"] if isinstance(shot, dict)]
    else:  # a clip: one record for the whole move
        records = [manifest.get("fit")]
    return [record["note"] for record in records if isinstance(record, dict) and record.get("note")]


def image_size(path):
    """(width, height) of a PNG or JPEG file from its header, or None when it cannot be read. Stdlib only."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if head.startswith(PNG_SIG) and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if not head.startswith(JPEG_SIG):
                return None
            f.seek(2)
            while True:
                byte = f.read(1)
                if not byte:
                    return None
                if byte != b"\xff":
                    continue
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if not marker or marker in (b"\x01", b"\xd8") or b"\xd0" <= marker <= b"\xd7":
                    continue
                length = struct.unpack(">H", f.read(2))[0]
                if b"\xc0" <= marker <= b"\xcf" and marker not in (b"\xc4", b"\xc8", b"\xcc"):
                    height, width = struct.unpack(">HH", f.read(5)[1:5])
                    return width, height
                f.seek(length - 2, 1)
    except (OSError, struct.error):
        return None


def upscale_factor(picture, outputs, aspect="crop"):
    """2 or 4 (the smallest that reaches the output, never more than 4), or None when the picture is big enough.

    picture is (width, height); outputs is a list of (width, height). Crop-to-fill magnifies the picture
    by the larger ratio, pad by the smaller one. With several outputs (Every size) the largest need counts.
    """
    if not picture or not picture[0] or not picture[1]:
        return None
    pick = max if aspect == "crop" else min
    need = max(pick(ow / picture[0], oh / picture[1]) for ow, oh in outputs)
    if need <= 1:
        return None
    return 2 if need <= 2 else 4


UPLOAD_STAMP = re.compile(r"-\d{8}-\d{6}$")


def clip_name(image_name, stamp, taken):
    """The clip's folder name: the picture's slug without its upload time stamp, then this clip's stamp.

    A name already in use (taken(name) is true) gets -2, -3, ... instead of waiting for a later time.
    """
    stem = UPLOAD_STAMP.sub("", slug(os.path.splitext(image_name)[0]))
    base = "%s-%s" % (stem, stamp)
    name, number = base, 2
    while taken(name):
        name = "%s-%d" % (base, number)
        number += 1
    return name


def render_python_problem(python):
    """None when `python` can import numpy and Pillow, else one line saying why clips will fail."""
    try:
        done = subprocess.run([python, "-c", "import numpy, PIL"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return "the render python %s cannot run" % python
    if done.returncode != 0:
        return "the render python %s lacks numpy or Pillow; clips will fail until it has both" % python
    return None


def _inside(root, path):
    """True if the real location of path (symlinks resolved) is root or inside it."""
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


def _page_default():
    try:
        with open(os.path.join(HERE, "app.html"), "rb") as f:
            return f.read()
    except OSError:
        return None


class Studio:
    """The pictures, the options, the one background render, and the recent clips.

    Every folder and every dependency can be replaced: tests pass temporary folders and a renderer.
    renderer(command, env, clip_dir, frames) -> (returncode, last stderr line)."""

    def __init__(self, input_root, output_root, render_python, renderer=None, page=None, plans_root=None,
                 has_ffmpeg=None):
        self.input_root = os.path.realpath(input_root)
        self.output_root = os.path.realpath(output_root)
        # Read only: plan files and the pictures they name are read from here, never written.
        self.plans_root = os.path.realpath(plans_root or os.path.join(os.path.expanduser("~"), "Pictures"))
        os.makedirs(self.input_root, exist_ok=True)
        os.makedirs(self.output_root, exist_ok=True)
        self.render_python = render_python
        # Without ffmpeg there is no MP4: clips are GIF only, and the page says how to get an MP4.
        self.has_ffmpeg = shutil.which("ffmpeg") is not None if has_ffmpeg is None else bool(has_ffmpeg)
        self.renderer = renderer or _render_subprocess
        self.page = page if page is not None else _page_default()
        self.catalog = {p["id"]: p for p in load_json(os.path.join(CAMERA_MOTION, "camera_motion", "presets",
                                                                  "catalog.json"))}
        self._lock = threading.Lock()
        self._onnx = None
        self.job = None  # {"id", "state", "message", "clip", "frames", "clip_dir"}

    # --- options and availability -------------------------------------------------------

    def _onnx_ok(self):
        if self._onnx is None:
            try:
                done = subprocess.run([self.render_python, "-c", "import onnxruntime"],
                                      capture_output=True, timeout=30)
                self._onnx = done.returncode == 0
            except (OSError, subprocess.SubprocessError):
                self._onnx = False
        return self._onnx

    def interp_backend(self):
        """The interpolation Smooth uses: rife when its model file and onnxruntime are here, else linear."""
        return "rife" if os.path.isfile(RIFE_MODEL) and self._onnx_ok() else "linear"

    def plan_upscale(self, source, req):
        """{factor, method} for Enhance small pictures, or None when it is off or the picture is big enough."""
        if not req["enhance"]:
            return None
        names = list(stillmove_sizes.SIZES) if req["size"] == EVERY else [req["size"]]
        factor = upscale_factor(image_size(source), [stillmove_sizes.SIZES[n] for n in names], req["aspect"])
        if factor is None:
            return None
        method = "esrgan" if os.path.isfile(ESRGAN_MODEL) and self._onnx_ok() else "lanczos"
        return {"factor": factor, "method": method}

    def options(self):
        presets = []
        for pid, (param, unit, lo, hi, default) in RANGES.items():
            cat = self.catalog.get(pid, {})
            presets.append({"id": pid, "name": cat.get("name", pid), "param": param, "unit": unit,
                            "min": lo, "max": hi, "default": default,
                            "help": cat.get("description", "")})
        real_depth = os.path.isfile(DEPTH_MODEL)
        layers = os.path.isfile(ISNET_MODEL) and self._onnx_ok()
        return {
            "presets": presets,
            "durations": dict(SECONDS),
            "sizes": [{"id": k, "w": w, "h": h} for k, (w, h) in stillmove_sizes.SIZES.items()],
            "depth": {"simple": True, "real": real_depth},
            "layers": layers,
        }

    # --- pictures -----------------------------------------------------------------------

    def images(self):
        found = []
        for name in os.listdir(self.input_root):
            full = os.path.join(self.input_root, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in IMAGE_EXT and os.path.isfile(full) and _inside(self.input_root, full):
                found.append((os.path.getmtime(full), name))
        rows = []
        for _, name in sorted(found, reverse=True):
            size = image_size(os.path.join(self.input_root, name))
            rows.append({"name": name, "url": "/inputs/" + name,
                         "width": size[0] if size else None, "height": size[1] if size else None})
        return rows

    def input_file(self, name):
        """The real path of an input picture, or raise: 400 for a bad name, 404 if missing or outside."""
        if not isinstance(name, str) or not SAFE_NAME.match(name) or ".." in name:
            raise ApiError(400, "picture names are letters, digits, '.', '_' and '-'")
        full = os.path.join(self.input_root, name)
        if not os.path.isfile(full) or not _inside(self.input_root, full):
            raise ApiError(404, "no such picture")
        return os.path.realpath(full)

    def save_upload(self, name, content_type, body):
        ext = UPLOAD_TYPES[content_type]
        if ext == "png" and not body.startswith(PNG_SIG):
            raise ApiError(400, "the picture is not a PNG (the first bytes are wrong)")
        if ext == "jpg" and not body.startswith(JPEG_SIG):
            raise ApiError(400, "the picture is not a JPEG (the first bytes are wrong)")
        stamp = _stamp()
        while True:
            saved = "%s-%s.%s" % (slug(name), stamp, ext)
            full = os.path.join(self.input_root, saved)
            if not os.path.exists(full):
                break
            stamp = _stamp(time.time() + 1)
        with open(full, "wb") as f:
            f.write(body)
        size = image_size(full)
        return {"name": saved, "url": "/inputs/" + saved,
                "width": size[0] if size else None, "height": size[1] if size else None}

    # --- clips --------------------------------------------------------------------------

    def clip_file(self, clip, filename):
        if not isinstance(clip, str) or not CLIP_DIR.match(clip):
            raise ApiError(400, "no such clip")
        full = os.path.join(self.output_root, clip, filename)
        if not os.path.isfile(full) or not _inside(self.output_root, full):
            raise ApiError(404, "no such clip file")
        return os.path.realpath(full)

    def clips(self):
        rows = []
        for name in os.listdir(self.output_root):
            if not CLIP_DIR.match(name):
                continue
            folder = os.path.join(self.output_root, name)
            meta_path = os.path.join(folder, "clip.json")
            has_mp4 = os.path.isfile(os.path.join(folder, "preview.mp4"))
            has_gif = os.path.isfile(os.path.join(folder, "preview.gif"))
            files = every_files(name, folder)
            if not (os.path.isfile(meta_path) and (has_mp4 or has_gif or files)):
                continue
            try:
                meta = load_json(meta_path)
            except (OSError, ValueError):
                continue
            gif = os.path.join(folder, "preview.gif")
            rows.append((meta.get("made", ""), {
                "name": name,
                "made": meta.get("made"),
                "seconds": meta.get("seconds"),
                "size": meta.get("size"),
                "preset": meta.get("preset"),
                "kind": meta.get("kind", "clip"),
                "shots": meta.get("shots"),
                "mp4": "/clips/%s/preview.mp4" % name if has_mp4 else None,
                "gif": "/clips/%s/preview.gif" % name if os.path.isfile(gif) else None,
                "notes": fit_notes(folder),
                "files": files,
                "zip": "/clips/%s/sizes.zip" % name if os.path.isfile(os.path.join(folder, "sizes.zip")) else None,
            }))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in rows[:CLIP_LIMIT]]

    # --- render -------------------------------------------------------------------------

    def _validate(self, body):
        if not isinstance(body, dict):
            raise ApiError(400, "the body must be a JSON object")
        preset = body.get("preset")
        if preset not in RANGES:
            raise ApiError(400, "unknown move")
        param, unit, lo, hi, _default = RANGES[preset]
        amount = body.get("amount")
        if not _is_number(amount) or not (lo <= amount <= hi):
            raise ApiError(400, "amount must be a number from %s to %s %s for this move" % (lo, hi, unit))
        seconds = body.get("seconds")
        if not _is_number(seconds) or not (SECONDS["min"] <= seconds <= SECONDS["max"]):
            raise ApiError(400, "seconds must be a number from %s to %s" % (SECONDS["min"], SECONDS["max"]))
        size = body.get("size")
        if size != EVERY and size not in stillmove_sizes.SIZES:
            raise ApiError(400, "size must be square, wide or vertical")
        layers = body.get("layers", False)
        if not isinstance(layers, bool):
            raise ApiError(400, "layers must be true or false")
        depth = body.get("depth", "simple")
        if depth not in ("simple", "real"):
            raise ApiError(400, "depth must be simple or real")
        aspect = body.get("aspect", "crop")
        if aspect not in ASPECTS:
            raise ApiError(400, "aspect must be crop or pad")
        smooth = body.get("smooth", 0)
        if isinstance(smooth, bool) or not isinstance(smooth, int) or smooth not in SMOOTH:
            raise ApiError(400, "smooth must be 0 (off), 2 or 4")
        enhance = body.get("enhance", False)
        if not isinstance(enhance, bool):
            raise ApiError(400, "enhance must be true or false")
        if smooth and (FPS % smooth or round(seconds * FPS) % smooth):
            raise ApiError(400, "smooth %d needs seconds times fps to be a multiple of %d" % (smooth, smooth))
        image = body.get("image")
        if not isinstance(image, str):
            raise ApiError(400, "image is required")
        if depth == "real" and not os.path.isfile(DEPTH_MODEL):
            raise ApiError(400, "the real depth model is not installed")
        if layers and not (os.path.isfile(ISNET_MODEL) and self._onnx_ok()):
            raise ApiError(400, "layers need the isnet model and onnxruntime in the render python")
        return {"image": image, "preset": preset, "amount": float(amount), "seconds": float(seconds),
                "size": size, "layers": layers, "depth": depth, "param": param, "aspect": aspect,
                "smooth": smooth, "enhance": enhance}

    def start(self, body):
        req = self._validate(body)
        req["interp"] = self.interp_backend() if req["smooth"] else None
        source = self.input_file(req["image"])
        req["upscale"] = self.plan_upscale(source, req)
        # Every size is one camera file, so it is built at the square frame; the render re-targets it per size.
        camera_size = "square" if req["size"] == EVERY else req["size"]
        width, height = stillmove_sizes.SIZES[camera_size]
        catalog_preset = json_copy(self.catalog[req["preset"]])
        sign = -1.0 if catalog_preset["params"].get(req["param"], 1.0) < 0 else 1.0
        params = dict(catalog_preset["params"])
        params[req["param"]] = sign * req["amount"]
        if catalog_preset["type"] == "orbit":
            params.pop("radius", None)  # orbits keep the current distance
        catalog_preset["params"] = params
        frames = camera_path(catalog_preset, duration_s=req["seconds"], fps=FPS)
        cam = to_json(frames, width=width, height=height, fps=FPS)
        expected = len(frames)

        with self._lock:
            if self.job is not None and self.job["state"] == "running":
                raise ApiError(409, "a clip is already rendering")
            clip = clip_name(req["image"], _stamp(),
                             lambda name: os.path.exists(os.path.join(self.output_root, name)))
            clip_dir = os.path.join(self.output_root, clip)
            os.makedirs(clip_dir)
            cam_path = os.path.join(clip_dir, "camera.json")
            with open(cam_path, "w", encoding="utf-8") as f:
                json.dump(cam, f)
            made = datetime.datetime.now().isoformat(timespec="seconds")
            meta = {"name": clip, "made": made, "image": req["image"], "preset": req["preset"],
                    "amount": req["amount"], "seconds": req["seconds"], "size": req["size"], "fps": FPS,
                    "layers": req["layers"], "depth": req["depth"], "smooth": req["smooth"],
                    "interp": req["interp"], "upscale": req["upscale"], "frames": expected}
            with open(os.path.join(clip_dir, "clip.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
                f.write("\n")
            command = self._command(source, cam_path, clip_dir, req)
            job_id = uuid.uuid4().hex
            self.job = {"id": job_id, "state": "running", "message": "rendering", "clip": clip,
                        "frames": expected, "clip_dir": clip_dir, "every": req["size"] == EVERY,
                        "smooth": {"factor": req["smooth"], "backend": req["interp"]} if req["smooth"] else None,
                        "upscale": req["upscale"]}
        threading.Thread(target=self._run, args=(job_id, command, clip_dir, expected, req["size"] == EVERY),
                         daemon=True).start()
        return job_id

    def _command(self, source, cam_path, clip_dir, req):
        cmd = [self.render_python, "-m", "stillmove.cli", "render", source, "--cam", cam_path,
               "--backend", "da2-small" if req["depth"] == "real" else "plane", "-o", clip_dir]
        if self.has_ffmpeg:
            cmd.insert(cmd.index("-o"), "--mp4")
        if req.get("size") == EVERY:
            index = cmd.index("-o")
            cmd[index:index] = ["--sizes", ",".join(EVERY_SIZES)]
        else:
            # Without these the render follows the picture's size, not the chosen square, wide or vertical.
            width, height = stillmove_sizes.SIZES[req["size"]]
            cmd += ["--width", str(width), "--height", str(height)]
        cmd += ["--aspect", req.get("aspect", "crop")]
        if req.get("upscale"):
            cmd += ["--upscale", str(req["upscale"]["factor"]), "--upscale-backend", req["upscale"]["method"]]
        if req.get("smooth"):
            cmd += ["--smooth", str(req["smooth"]), "--interp-backend", req.get("interp") or "linear"]
        if req["layers"]:
            cmd += ["--layers", "--matte", "isnet"]
            if req["depth"] == "simple":
                cmd += ["--fg-depth", "4.6"]
        return cmd

    def _run(self, job_id, command, clip_dir, expected, every=False):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([VIDEO_STUDIO, CAMERA_MOTION, env.get("PYTHONPATH", "")])
        try:
            code, err = self.renderer(command, env, clip_dir, expected)
            if every:
                state = _every_state(code, err, clip_dir)
            else:
                state = _single_state(code, err, clip_dir, self.has_ffmpeg)
        except Exception as exc:  # any failure is the job's error message
            state = ("error", str(exc) or type(exc).__name__)
        with self._lock:
            if self.job is not None and self.job["id"] == job_id:
                self.job.update(state=state[0], message=state[1])

    def status(self):
        with self._lock:
            job = dict(self.job) if self.job is not None else None
        if job is not None and job.get("kind") == "promo":
            return self._promo_status(job)
        return self._clip_status(job)

    def _clip_status(self, job):
        if job is None:
            return {"state": "idle", "frame": 0, "frames": 0, "message": "", "clip": None}
        frame = 0
        frames_dir = os.path.join(job["clip_dir"], "frames")
        if os.path.isdir(frames_dir):
            frame = min(len(os.listdir(frames_dir)), job["frames"])
        notes = fit_notes(job["clip_dir"]) if job["state"] == "done" else []
        if job["state"] == "done":
            frame = job["frames"]
        status = {"state": job["state"], "frame": frame, "frames": job["frames"], "message": job["message"],
                  "clip": job["clip"], "notes": notes, "smooth": job.get("smooth"),
                  "upscale": job.get("upscale")}
        if job.get("every") and job["state"] == "done":
            status["files"] = every_files(os.path.basename(job["clip_dir"]), job["clip_dir"])
            status["zip"] = "/clips/%s/sizes.zip" % os.path.basename(job["clip_dir"])
        return status

    # --- make a promo: two to six pictures in one plan --------------------------------------

    def _plan_image(self, plan_dir, image):
        """The real path of a picture a plan names (relative to the plan's folder), or None."""
        if not isinstance(image, str) or not image or image.startswith("placeholder:"):
            return None
        if os.path.isabs(image):
            return None
        full = os.path.join(plan_dir, image)
        if os.path.splitext(full)[1].lower() not in IMAGE_EXT or not os.path.isfile(full):
            return None
        if not _inside(self.plans_root, full):
            return None
        return os.path.realpath(full)

    def promo_plans(self):
        """Plan files under PLANS_ROOT, at most PLAN_FOLDERS folders deep. Read only."""
        root = self.plans_root
        if not os.path.isdir(root):
            return []
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            if depth >= PLAN_FOLDERS:
                dirnames[:] = []  # no folder below this one is looked in
            for name in filenames:
                full = os.path.join(dirpath, name)
                if not name.endswith(".json") or not _inside(root, full) or os.path.getsize(full) > PLAN_MAX:
                    continue
                try:
                    data = load_json(full)
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict) and isinstance(data.get("shots"), list):
                    found.append({"path": os.path.relpath(full, root).replace(os.sep, "/"),
                                  "name": str(data.get("name") or os.path.splitext(name)[0]),
                                  "shots": len(data["shots"])})
        return sorted(found, key=lambda item: item["path"])

    def promo_path(self, rel):
        """The real path of a plan the owner picked. It must be a .json file inside PLANS_ROOT."""
        if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in rel.split("/"):
            raise ApiError(400, "that plan is not inside the Pictures folder")
        if not rel.endswith(".json"):
            raise ApiError(400, "a plan is a .json file")
        full = os.path.join(self.plans_root, rel)
        if not _inside(self.plans_root, full):
            raise ApiError(400, "that plan is not inside the Pictures folder")
        if not os.path.isfile(full):
            raise ApiError(404, "no such plan")
        return os.path.realpath(full)

    def promo_load(self, rel):
        """The shots of a plan for the form, and the pictures it names that are not on disk."""
        full = self.promo_path(rel)
        if os.path.getsize(full) > PLAN_MAX:
            raise ApiError(400, "that plan file is too large")
        try:
            plan = load_json(full)
        except (OSError, ValueError):
            raise ApiError(400, "that file is not a valid plan (it is not JSON)")
        if not isinstance(plan, dict) or not isinstance(plan.get("shots"), list):
            raise ApiError(400, "that file has no list of shots")
        plan_dir = os.path.dirname(full)
        missing, shots = [], []
        for shot in plan["shots"]:
            shots.append(self._loaded_shot(plan_dir, shot, missing))
        return {"size": _size_of(plan.get("width"), plan.get("height")), "shots": shots, "missing": missing}

    def _loaded_shot(self, plan_dir, shot, missing):
        image = shot.get("image") if isinstance(shot, dict) else None
        image = image if isinstance(image, str) else ""
        found = self._plan_image(plan_dir, image) is not None
        if image and not found and image not in missing:
            missing.append(image)
        row = {"image": image, "found": found, "preset": None, "amount": None, "seconds": None,
               "transition": None, "crossfade": None, "layers": False, "editable": False}
        read = _editable_shot(shot, self.catalog) if isinstance(shot, dict) else None
        if read is not None:
            row.update(read, editable=True)
        else:
            # Kept as the plan has it, so the render can use it verbatim (PROMO.md: still renderable).
            row["raw"] = self._raw_shot(plan_dir, shot)
        return row

    def _raw_shot(self, plan_dir, shot):
        """The shot's own sequence, as the plan has it, or None when it cannot be read. A sequence
        named by a file is read here, and the file must be inside PLANS_ROOT."""
        if not isinstance(shot, dict):
            return None
        seq = shot.get("sequence")
        if isinstance(seq, str):
            full = os.path.join(plan_dir, seq)
            if os.path.isabs(seq) or ".." in seq.split("/") or not os.path.isfile(full) or not _inside(self.plans_root, full):
                return None
            try:
                loaded = load_json(full)
            except (OSError, ValueError):
                return None
            if not isinstance(loaded, dict) or not isinstance(loaded.get("segments"), list):
                return None
            seq = {"segments": loaded["segments"]}
        if not isinstance(seq, dict) or not isinstance(seq.get("segments"), list):
            return None
        return {"sequence": seq, "duration": shot.get("duration"), "transition": shot.get("transition", "cut"),
                "layers": shot.get("layers", False)}

    def _raw_promo_shot(self, index, raw, plan_dir):
        """A shot kept from a loaded plan: its picture, and its sequence exactly as the plan has it.
        shotplan's check validates the sequence once the whole plan is written."""
        n = index + 1
        if plan_dir is None:
            raise ApiError(400, "shot %d came from a plan: load that plan first" % n)
        image = raw.get("image")
        resolved = self._plan_image(plan_dir, image)
        if resolved is None:
            raise ApiError(400, "Still needed: %s" % (image or "a picture for shot %d" % n))
        kept = raw.get("raw")
        if not isinstance(kept, dict) or not isinstance(kept.get("sequence"), dict) \
                or not isinstance(kept["sequence"].get("segments"), list):
            raise ApiError(400, "shot %d: its sequence is missing from the plan" % n)
        duration = kept.get("duration")
        if not _is_number(duration):
            raise ApiError(400, "shot %d: its duration is missing from the plan" % n)
        transition = kept.get("transition", "cut")
        crossfade = None
        if transition == "cut":
            kind = "cut"
        elif isinstance(transition, dict) and set(transition) == {"crossfade"} and _is_number(transition["crossfade"]) \
                and transition["crossfade"] > 0:
            kind, crossfade = "crossfade", float(transition["crossfade"])
        else:
            raise ApiError(400, "shot %d: its transition is not a cut or a crossfade" % n)
        layers = kept.get("layers", False)
        if not isinstance(layers, bool):
            raise ApiError(400, "shot %d: layers must be true or false" % n)
        return {"resolved": resolved, "ext": os.path.splitext(resolved)[1].lower(), "preset": None,
                "param": None, "amount": None, "seconds": float(duration), "transition": kind,
                "crossfade": crossfade, "layers": layers, "raw_sequence": kept["sequence"],
                "raw_transition": transition}

    def _promo_shot(self, index, raw, plan_dir):
        """One shot of a promo request, checked. plan_dir is set when the shots come from a plan."""
        n = index + 1
        if not isinstance(raw, dict):
            raise ApiError(400, "shot %d must be an object" % n)
        if "raw" in raw:
            return self._raw_promo_shot(index, raw, plan_dir)
        image = raw.get("image")
        if plan_dir is not None:
            resolved = self._plan_image(plan_dir, image)
            if resolved is None:
                raise ApiError(400, "Still needed: %s" % (image or "a picture for shot %d" % n))
        else:
            try:
                resolved = self.input_file(image)
            except ApiError:
                raise ApiError(400, "shot %d: the picture %s is missing" % (n, image))
        preset = raw.get("preset")
        if preset not in RANGES:
            raise ApiError(400, "shot %d: unknown move" % n)
        param, unit, lo, hi, _default = RANGES[preset]
        amount = raw.get("amount")
        if not _is_number(amount) or not (lo <= amount <= hi):
            raise ApiError(400, "shot %d: amount must be a number from %s to %s %s" % (n, lo, hi, unit))
        seconds = raw.get("seconds")
        if not _is_number(seconds) or not (SECONDS["min"] <= seconds <= SECONDS["max"]):
            raise ApiError(400, "shot %d: seconds must be a number from %s to %s"
                           % (n, SECONDS["min"], SECONDS["max"]))
        transition = raw.get("transition")
        if transition not in ("cut", "crossfade"):
            raise ApiError(400, 'shot %d: transition must be "cut" or "crossfade"' % n)
        crossfade = None
        if transition == "crossfade":
            crossfade = raw.get("crossfade")
            if not _is_number(crossfade) or crossfade <= 0:
                raise ApiError(400, "shot %d: the crossfade must be a number of seconds above 0" % n)
            crossfade = float(crossfade)
        layers = raw.get("layers", False)
        if not isinstance(layers, bool):
            raise ApiError(400, "shot %d: layers must be true or false" % n)
        return {"resolved": resolved, "ext": os.path.splitext(resolved)[1].lower(), "preset": preset,
                "param": param, "amount": float(amount), "seconds": float(seconds),
                "transition": transition, "crossfade": crossfade, "layers": layers}

    def promo_start(self, body):
        """Check a promo form (or a loaded plan's shots), copy the pictures, write plan.json, render."""
        if not isinstance(body, dict):
            raise ApiError(400, "the body must be a JSON object")
        raw_shots = body.get("shots")
        if not isinstance(raw_shots, list) or not PROMO_MIN <= len(raw_shots) <= PROMO_MAX:
            raise ApiError(400, "a promo needs 2 to 6 shots")
        aspect = body.get("aspect", "crop")
        if aspect not in ASPECTS:
            raise ApiError(400, "aspect must be crop or pad")
        size = body.get("size")
        if size not in stillmove_sizes.SIZES:
            raise ApiError(400, "size must be square, wide or vertical")
        source = body.get("source")
        plan_dir = None
        if source is not None:
            plan_dir = os.path.dirname(self.promo_path(source))
        shots = [self._promo_shot(index, raw, plan_dir) for index, raw in enumerate(raw_shots)]
        for index, shot in enumerate(shots):
            if shot["transition"] != "crossfade":
                continue
            if index == len(shots) - 1:
                raise ApiError(400, "the last shot is always a cut")
            nxt = shots[index + 1]
            if not (shot["crossfade"] < shot["seconds"] and shot["crossfade"] < nxt["seconds"]):
                raise ApiError(400, "shot %d: the crossfade must be shorter than this shot and the next one"
                               % (index + 1))
        if any(s["layers"] for s in shots) and not (os.path.isfile(ISNET_MODEL) and self._onnx_ok()):
            raise ApiError(400, "layers need the isnet model and onnxruntime in the render python")

        total_seconds = round(sum(s["seconds"] for s in shots) - sum(s["crossfade"] or 0 for s in shots), 3)
        total_frames = round(total_seconds * FPS)
        per_shot = [round(s["seconds"] * FPS) + 1 for s in shots]
        with self._lock:
            if self.job is not None and self.job["state"] == "running":
                raise ApiError(409, "a clip is already rendering")
            stamp = _stamp()
            clip = "promo-" + stamp
            while os.path.exists(os.path.join(self.output_root, clip)):
                stamp = _stamp(time.time() + 1)
                clip = "promo-" + stamp
            clip_dir = os.path.join(self.output_root, clip)
            os.makedirs(os.path.join(clip_dir, "images"))
            for index, shot in enumerate(shots):
                shutil.copyfile(shot["resolved"], os.path.join(clip_dir, "images", shot_file(index, shot["ext"])))
            plan = build_promo_plan(shots, size, self.catalog)
            plan_path = os.path.join(clip_dir, "plan.json")
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=2)
                f.write("\n")
            # The whole plan goes through shotplan's own check before any render starts (this covers
            # the sequences kept from a loaded plan too). On an error nothing is left behind.
            errors, _rows, _sequences = shotplan_checker.check(plan, clip_dir)
            if errors:
                shutil.rmtree(clip_dir, ignore_errors=True)
                raise ApiError(400, errors[0])
            meta = {"name": clip, "made": datetime.datetime.now().isoformat(timespec="seconds"), "kind": "promo",
                    "size": size, "fps": FPS, "shots": len(shots), "seconds": total_seconds,
                    "frames": total_frames, "images": [shot_file(i, s["ext"]) for i, s in enumerate(shots)]}
            with open(os.path.join(clip_dir, "clip.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
                f.write("\n")
            command = promo_command(self.render_python, plan_path, clip_dir, any(s["layers"] for s in shots),
                                    mp4=self.has_ffmpeg, aspect=aspect)
            job_id = uuid.uuid4().hex
            self.job = {"id": job_id, "state": "running", "message": "rendering", "clip": clip,
                        "frames": total_frames, "clip_dir": clip_dir, "kind": "promo",
                        "shot_ids": [shot["id"] for shot in plan["shots"]], "per_shot": per_shot}
        threading.Thread(target=self._run, args=(job_id, command, clip_dir, total_frames), daemon=True).start()
        return job_id

    def _promo_status(self, job):
        """Progress for a promo: 'shot N of M, frame X of Y', then 'joining, frame X of Y'."""
        shots = len(job["shot_ids"])
        reply = {"state": job["state"], "frame": 0, "frames": job["frames"], "message": job["message"],
                 "clip": job["clip"], "kind": "promo", "shot": 1, "shots": shots, "stage": "shots"}
        if job["state"] == "done":
            reply.update(stage="joining", shot=shots, frame=job["frames"], notes=fit_notes(job["clip_dir"]))
            return reply
        clip_dir = job["clip_dir"]
        started = None
        for index, sid in enumerate(job["shot_ids"]):
            if os.path.isdir(os.path.join(clip_dir, "shots", sid, "frames")):
                started = index
        if os.path.isdir(os.path.join(clip_dir, "frames")):
            written = len(os.listdir(os.path.join(clip_dir, "frames")))
            reply.update(stage="joining", shot=shots, frame=min(written, job["frames"]))
            return reply
        if started is None:
            # Before the first shot writes a frame, report that shot's own total, not the whole promo's.
            reply["frames"] = job["per_shot"][0]
            return reply
        sid = job["shot_ids"][started]
        written = len(os.listdir(os.path.join(clip_dir, "shots", sid, "frames")))
        expected = job["per_shot"][started]
        reply.update(shot=started + 1, frame=min(written, expected), frames=expected)
        return reply

    def close(self):
        pass


def shot_file(index, ext):
    """The name a promo's picture gets inside its clip folder: shot-1.jpg, shot-2.png, ..."""
    return "shot-%d%s" % (index + 1, ext)


def _sign(preset, catalog):
    """The catalog's sign for the move's one parameter. The form's amount is always positive."""
    value = catalog[preset]["params"].get(RANGES[preset][0], 1.0)
    return -1.0 if value < 0 else 1.0


def build_promo_plan(shots, size, catalog):
    """The shotplan plan.json for a promo: one inline segment per shot, pictures in images/."""
    width, height = stillmove_sizes.SIZES[size]
    rows = []
    for index, shot in enumerate(shots):
        if shot.get("raw_sequence") is not None:
            # A shot kept from a loaded plan: its sequence and transition are written as the plan has them.
            sequence, transition = shot["raw_sequence"], shot["raw_transition"]
        else:
            segment = {"preset": shot["preset"], "duration_s": shot["seconds"],
                       "params": {shot["param"]: _sign(shot["preset"], catalog) * shot["amount"]}}
            sequence = {"segments": [segment]}
            transition = {"crossfade": shot["crossfade"]} if shot["transition"] == "crossfade" else "cut"
        row = {"id": "shot_%d" % (index + 1), "image": "images/" + shot_file(index, shot["ext"]),
               "sequence": sequence, "duration": shot["seconds"], "transition": transition}
        if shot["layers"]:
            row["layers"] = True
        rows.append(row)
    return {"id": "promo", "name": "Promo", "fps": FPS, "width": width, "height": height, "shots": rows}


def _editable_shot(shot, catalog):
    """The form values of a shot the form can show and change, or None (the shot is shown read only):
    one inline segment of a known move, its sign and range as the form has them, a duration that
    matches the segment, and a cut or a crossfade."""
    seq = shot.get("sequence")
    if not isinstance(seq, dict) or set(seq) != {"segments"}:
        return None
    segments = seq.get("segments")
    if not isinstance(segments, list) or len(segments) != 1 or not isinstance(segments[0], dict):
        return None
    seg = segments[0]
    preset = seg.get("preset")
    if preset not in RANGES or set(seg) - {"preset", "duration_s", "params"}:
        return None
    param, unit, lo, hi, _default = RANGES[preset]
    params = seg.get("params", {})
    if not isinstance(params, dict) or set(params) - {param, "frame"}:
        return None
    value = params.get(param)
    if not _is_number(value) or value == 0 or (-1.0 if value < 0 else 1.0) != _sign(preset, catalog):
        return None
    amount = abs(float(value))
    if not (lo <= amount <= hi):
        return None
    seconds = seg.get("duration_s")
    if not _is_number(seconds) or not (SECONDS["min"] <= seconds <= SECONDS["max"]):
        return None
    duration = shot.get("duration")
    if not _is_number(duration) or abs(duration - seconds) > 1e-9:
        return None
    tr = shot.get("transition", "cut")
    if tr == "cut":
        transition, crossfade = "cut", None
    elif isinstance(tr, dict) and set(tr) == {"crossfade"} and _is_number(tr["crossfade"]) and tr["crossfade"] > 0:
        transition, crossfade = "crossfade", float(tr["crossfade"])
    else:
        return None
    layers = shot.get("layers", False)
    if not isinstance(layers, bool):
        return None
    return {"preset": preset, "amount": amount, "seconds": float(seconds), "transition": transition,
            "crossfade": crossfade, "layers": layers}


def _size_of(width, height):
    for name, (w, h) in stillmove_sizes.SIZES.items():
        if (width, height) == (w, h):
            return name
    return None


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def json_copy(value):
    return json.loads(json.dumps(value))


def _render_subprocess(command, env, clip_dir, frames):
    """The real render: stillmove under the render python. Returns (returncode, last stderr line)."""
    done = subprocess.run(command, capture_output=True, text=True, env=env, timeout=3600, cwd=VIDEO_STUDIO)
    lines = [line for line in (done.stderr or "").splitlines() if line.strip()]
    return done.returncode, (lines[-1] if lines else "")


def _host_ok(value):
    name = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    return name in ALLOWED_HOSTS


class MakeClipHandler(BaseHTTPRequestHandler):
    studio = None  # set by make_server

    def log_message(self, format, *args):
        pass

    def _send(self, status, content_type, body, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value, status=200):
        self._send(status, "application/json", json.dumps(value).encode("utf-8"))

    def _guard(self, fn):
        try:
            if not _host_ok(self.headers.get("Host", "")):
                raise ApiError(403, "only 127.0.0.1 and localhost are served")
            origin = self.headers.get("Origin")
            if origin is not None and urlsplit(origin).hostname not in ALLOWED_HOSTS:
                raise ApiError(403, "cross-origin requests are refused")
            fn()
        except ApiError as err:
            self._json({"error": str(err)}, err.status)
        except Exception:
            self._json({"error": "internal error"}, 500)

    def do_GET(self):
        self._guard(lambda: self._get(urlsplit(self.path)))

    def do_POST(self):
        self._guard(lambda: self._post(urlsplit(self.path)))

    def do_PUT(self):
        self._json({"error": "method not allowed"}, 405)

    do_DELETE = do_PATCH = do_PUT

    def _get(self, url):
        studio = self.studio
        route = url.path
        if route == "/":
            if studio.page is None:
                raise ApiError(503, "the page is not built yet")
            return self._send(200, "text/html; charset=utf-8", studio.page)
        if route == "/api/options":
            return self._json(studio.options())
        if route == "/api/images":
            return self._json(studio.images())
        if route == "/api/render/status":
            return self._json(studio.status())
        if route == "/api/clips":
            return self._json(studio.clips())
        if route == "/api/promo/plans":
            return self._json(studio.promo_plans())
        match = re.fullmatch(r"/inputs/([^/]+)", route)
        if match:
            full = studio.input_file(match.group(1))
            ctype = IMAGE_EXT.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
            with open(full, "rb") as f:
                return self._send(200, ctype, f.read())
        match = re.fullmatch(r"/clips/([^/]+)/((?:(?:square|wide|vertical)/)?preview\.(?:mp4|gif)|sizes\.zip)",
                             route)
        if match:
            full = studio.clip_file(match.group(1), match.group(2))
            ctype = CLIP_TYPES[full.rsplit(".", 1)[1]]
            headers = []
            if parse_qs(url.query).get("download") == ["1"]:
                headers.append(("Content-Disposition", 'attachment; filename="%s-%s"'
                                % (match.group(1), match.group(2).replace("/", "-"))))
            with open(full, "rb") as f:
                return self._send(200, ctype, f.read(), headers)
        raise ApiError(404, "no such page")

    def _post(self, url):
        if self.headers.get(REQUIRED_HEADER[0]) != REQUIRED_HEADER[1]:
            raise ApiError(403, "POST needs the X-Requested-With: makeclip header")
        studio = self.studio
        route = url.path
        if route == "/api/upload":
            return self._upload(parse_qs(url.query))
        if route == "/api/render":
            body = self._json_body()
            job = studio.start(body)
            return self._json({"job": job}, 202)
        if route == "/api/promo/load":
            body = self._json_body()
            if not isinstance(body, dict):
                raise ApiError(400, "the body must be a JSON object")
            return self._json(studio.promo_load(body.get("path")))
        if route == "/api/promo/render":
            body = self._json_body()
            job = studio.promo_start(body)
            return self._json({"job": job}, 202)
        raise ApiError(404, "no such page")

    def _length(self, limit, too_big):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(411, "Content-Length is required") from None
        if length < 0:
            raise ApiError(400, "bad Content-Length")
        if length > limit:
            self.close_connection = True  # the unread body must not be read as the next request
            raise ApiError(413, too_big)
        return length

    def _json_body(self):
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != JSON_TYPE:
            raise ApiError(415, "POST bodies must be application/json")
        length = self._length(MAX_JSON, "the request body is larger than 64 KB")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "the body must be JSON") from None

    def _upload(self, query):
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type not in UPLOAD_TYPES:
            raise ApiError(415, "pictures must be image/png or image/jpeg")
        name = query.get("name", [""])[0]
        if not name:
            raise ApiError(400, "name is required")
        length = self._length(MAX_UPLOAD, "the picture is larger than 25 MB")
        body = self.rfile.read(length)
        return self._json(self.studio.save_upload(name, content_type, body), 200)


class MakeClipServer(ThreadingHTTPServer):
    allow_reuse_address = False  # a second server on the same port must fail, not share it
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind asks getfqdn(), which can block on DNS. We only need the address.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def make_server(studio, port=DEFAULT_PORT):
    """A MakeClipServer on 127.0.0.1:port serving studio. Port 0 picks a free port."""
    handler = type("Handler", (MakeClipHandler,), {"studio": studio})
    return MakeClipServer((HOST, port), handler)


ASPECTS = ("crop", "pad")  # how a picture whose shape differs from the frame is fitted (see stillmove.render)


def _single_state(code, err, clip_dir, has_ffmpeg):
    has_mp4 = os.path.isfile(os.path.join(clip_dir, "preview.mp4"))
    has_gif = os.path.isfile(os.path.join(clip_dir, "preview.gif"))
    if code == 0 and has_mp4:
        return ("done", "clip ready")
    if code == 0 and has_gif and not has_ffmpeg:
        return ("done", "clip ready as a GIF: install ffmpeg to get an MP4")
    if code == 0:
        return ("error", "the render finished but wrote no preview.mp4 or preview.gif")
    return ("error", err or "the render failed (status %s)" % code)


def _every_state(code, err, clip_dir):
    if code != 0:
        return ("error", err or "the render failed (status %s)" % code)
    files = every_files(os.path.basename(clip_dir), clip_dir)
    if len(files) != len(EVERY_SIZES) or not os.path.isfile(os.path.join(clip_dir, "sizes.zip")):
        return ("error", "the render finished but did not write a preview for every size and sizes.zip")
    return ("done", "clip ready")


def every_files(clip, clip_dir):
    """One row per size that has a preview: its id and the URLs of the MP4 and GIF that exist."""
    rows = []
    for size in EVERY_SIZES:
        row = {"size": size, "mp4": None, "gif": None}
        for kind in ("mp4", "gif"):
            if os.path.isfile(os.path.join(clip_dir, size, "preview." + kind)):
                row[kind] = "/clips/%s/%s/preview.%s" % (clip, size, kind)
        if row["mp4"] or row["gif"]:
            rows.append(row)
    return rows


def promo_command(render_python, plan_path, clip_dir, layers, mp4=True, aspect="crop"):
    """The promo render: python -m shotplan render, with the isnet matte when a shot has layers."""
    command = [render_python, "-m", "shotplan", "render", plan_path, clip_dir, "--depth", "fake",
               "--aspect", aspect]
    if mp4:
        command.append("--mp4")
    if layers:
        command += ["--matte", "isnet"]
    return command


def default_render_python():
    """The python that renders: this server's own, which has numpy and Pillow in the venv that runs it.
    MAKECLIP_RENDER_PYTHON overrides it."""
    return os.environ.get(RENDER_PYTHON_ENV) or sys.executable


def serve(port=DEFAULT_PORT, studio=None):
    studio = studio or Studio(os.path.join(os.path.expanduser("~"), "Pictures", "stillmove-input"),
                              os.path.join(os.path.expanduser("~"), "Pictures", "stillmove-output"),
                              default_render_python())
    try:
        httpd = make_server(studio, port)
    except OSError as err:
        if err.errno == errno.EADDRINUSE:
            print(f"error: port {port} is already in use; pick another with a port argument", file=sys.stderr)
        else:
            print(f"error: cannot listen on {HOST}:{port}: {err}", file=sys.stderr)
        return 1
    print(f"make a clip on http://{HOST}:{httpd.server_address[1]}/  (Ctrl-C to stop)")
    print(f"rendering with {studio.render_python}")
    problem = render_python_problem(studio.render_python)
    if problem:
        print(f"warning: {problem}", file=sys.stderr)
    if not studio.has_ffmpeg:
        print("ffmpeg not found: install ffmpeg to get an MP4; clips will be GIF only")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="makeclip", description="Make a clip: a page on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on (default %(default)s)")
    args = parser.parse_args(argv)
    return serve(args.port)


if __name__ == "__main__":
    sys.exit(main())
