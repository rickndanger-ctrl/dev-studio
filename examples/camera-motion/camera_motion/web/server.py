"""HTTP server for the camera-motion viewer: pages, a JSON path API, animated SVG previews
and downloads. Binds to 127.0.0.1 only.

Preset ids are looked up in the catalog that is loaded at startup. Nothing else is read
from disk per request; page bytes and frames.json are kept in memory.
"""
import copy
import json
import math
import os
import re
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlsplit

from camera_motion.engine.camera_path import path
from camera_motion.export.formats import export_text
from camera_motion.preview.anim_svg import render_animated
from camera_motion.presets import load_help
from camera_motion.web import export_frames

EASINGS = ("linear", "ease_in", "ease_out", "ease_in_out")
AXES = ("x", "z")
FLOAT_KEYS = ("distance", "angle", "radius", "fov_end")
ALLOWED_KEYS = set(FLOAT_KEYS) | {"axis", "easing", "seconds", "fps"}

# format -> (export_text kind, file extension, content type)
DOWNLOADS = {
    "json": ("json", "json", "application/json"),
    "re10k": ("re10k", "txt", "text/plain; charset=utf-8"),
    "py": ("blender", "py", "text/x-python; charset=utf-8"),
    "gltf": ("gltf", "gltf", "model/gltf+json"),
}
HTML = "text/html; charset=utf-8"
JSON = "application/json"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _read_package_file(name):
    """Bytes of a file bundled in camera_motion/web, or None when it is not there."""
    try:
        return resources.files("camera_motion.web").joinpath(name).read_bytes()
    except FileNotFoundError:
        return None


class Viewer:
    """Catalog, frames.json and page bytes, loaded once."""

    def __init__(self, catalog_path=None, app_html=None, index_html=None, help_data=None):
        catalog_path = catalog_path or export_frames.default_catalog()
        with open(catalog_path, encoding="utf-8") as f:
            self.presets = json.load(f)
        self.by_id = {preset["id"]: preset for preset in self.presets}
        self.help = load_help() if help_data is None else help_data
        doc = export_frames.build(catalog_path)
        self.frames_json = json.dumps(doc, indent=2).encode("utf-8")
        self.app_html = app_html if app_html is not None else _read_package_file("app.html")
        self.index_html = index_html if index_html is not None else _read_package_file("index.html")

    def preset(self, preset_id):
        if preset_id not in self.by_id:
            raise ApiError(404, f"unknown preset {preset_id!r}")
        return self.by_id[preset_id]


def _float(key, value):
    try:
        number = float(value)
    except ValueError:
        raise ApiError(400, f"{key} must be a number") from None
    if not math.isfinite(number):
        raise ApiError(400, f"{key} must be a finite number")
    return number


def _parse_value(key, value):
    if key in ("distance", "angle"):
        return _float(key, value)
    if key == "radius":
        number = _float(key, value)
        if number <= 0:
            raise ApiError(400, "radius must be greater than 0")
        return number
    if key == "fov_end":
        number = _float(key, value)
        if not 1 <= number <= 179:
            raise ApiError(400, "fov_end must be between 1 and 179 degrees")
        return number
    if key == "seconds":
        number = _float(key, value)
        if not 0.5 <= number <= 10:
            raise ApiError(400, "seconds must be between 0.5 and 10")
        return number
    if key == "fps":
        if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 60:
            raise ApiError(400, "fps must be a whole number from 1 to 60")
        return int(value)
    if key == "axis":
        if value not in AXES:
            raise ApiError(400, "axis must be x or z")
        return value
    if key == "easing":
        if value not in EASINGS:
            raise ApiError(400, "easing must be one of " + ", ".join(EASINGS))
        return value
    raise ApiError(400, f"unknown parameter {key!r}")


def parse_overrides(query):
    """Validated overrides from a query string. Unknown or repeated keys are a 400."""
    out = {}
    for key, values in parse_qs(query, keep_blank_values=True).items():
        if key not in ALLOWED_KEYS:
            raise ApiError(400, f"unknown parameter {key!r}")
        if len(values) != 1:
            raise ApiError(400, f"parameter {key!r} is given more than once")
        out[key] = _parse_value(key, values[0])
    return out


def settings(preset, query):
    """The preset with overrides applied, plus its duration in seconds and fps."""
    ov = parse_overrides(query)
    applied = copy.deepcopy(preset)
    params = dict(applied.get("params", {}))
    for key in ("distance", "angle", "radius", "fov_end", "axis"):
        if key in ov:
            params[key] = ov[key]
    applied["params"] = params
    if "easing" in ov:
        applied["easing"] = ov["easing"]
    seconds = ov.get("seconds", preset.get("duration_s", 3.0))
    fps = ov.get("fps", preset.get("fps", 24))
    return applied, seconds, fps


def _svg_bytes(preset_id, name, frames, fps):
    fd, tmp = tempfile.mkstemp(suffix=".svg")
    os.close(fd)
    try:
        render_animated(frames, tmp, title=name or preset_id, fps=fps)
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp)


class ViewerHandler(BaseHTTPRequestHandler):
    viewer = None  # set by make_server

    def do_GET(self):
        try:
            self._dispatch(urlsplit(self.path))
        except ApiError as err:
            self._send(err.status, JSON, json.dumps({"error": str(err)}).encode("utf-8"))

    def log_message(self, format, *args):  # keep test and console output quiet
        pass

    def _send(self, status, content_type, body, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value):
        self._send(200, JSON, json.dumps(value).encode("utf-8"))

    def _dispatch(self, url):
        route, query = url.path, url.query
        viewer = self.viewer

        if route == "/":
            if viewer.app_html is None:
                raise ApiError(404, "app.html is not bundled")
            return self._send(200, HTML, viewer.app_html)
        if route == "/live.html":
            if viewer.index_html is None:
                raise ApiError(404, "index.html is not bundled")
            return self._send(200, HTML, viewer.index_html)
        if route == "/frames.json":
            return self._send(200, JSON, viewer.frames_json)
        if route == "/api/presets":
            return self._json([dict(p, help=viewer.help.get(p["id"], {})) for p in viewer.presets])

        match = re.fullmatch(r"/api/path/([^/]+)", route)
        if match:
            preset_id = match.group(1)
            preset = viewer.preset(preset_id)
            applied, seconds, fps = settings(preset, query)
            frames = path(applied, duration_s=seconds, fps=fps)
            return self._json({"id": preset_id, "fps": fps, "frames": frames})

        match = re.fullmatch(r"/anim/([^/]+)\.svg", route)
        if match:
            preset_id = match.group(1)
            preset = viewer.preset(preset_id)
            applied, seconds, fps = settings(preset, query)
            frames = path(applied, duration_s=seconds, fps=fps)
            body = _svg_bytes(preset_id, preset.get("name"), frames, fps)
            return self._send(200, "image/svg+xml", body)

        match = re.fullmatch(r"/download/([^/]+)\.([^/]+)", route)
        if match:
            preset_id, fmt = match.group(1), match.group(2)
            preset = viewer.preset(preset_id)
            if fmt not in DOWNLOADS:
                raise ApiError(400, "format must be one of " + ", ".join(DOWNLOADS))
            kind, ext, content_type = DOWNLOADS[fmt]
            applied, seconds, fps = settings(preset, query)
            frames = path(applied, duration_s=seconds, fps=fps)
            text = export_text(kind, frames, applied, fps=fps)
            disposition = ("Content-Disposition", f"attachment; filename={preset_id}.{ext}")
            return self._send(200, content_type, text.encode("utf-8"), headers=[disposition])

        raise ApiError(404, f"no route for {route!r}")


def make_server(viewer, port=0):
    """A ThreadingHTTPServer on 127.0.0.1:port serving viewer. Port 0 picks a free port."""
    handler = type("Handler", (ViewerHandler,), {"viewer": viewer})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)
