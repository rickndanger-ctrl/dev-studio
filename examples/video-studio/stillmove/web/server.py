"""HTTP server for stillmove: one page, a JSON API and preview GIFs. Binds to 127.0.0.1 only.

  GET  /                          the page
  GET  /api/presets               camera moves: [{id, name, type, description, safe_range}]
                                  safe_range = {param, limit, unit, value, exceeds} or null
  GET  /api/backends              depth backends: [name]
  POST /api/render                multipart: image (<= 5 MB), preset, backend; optional seconds, fps
                                  -> {"id": job id}. The render runs before the reply, in a temp dir.
  GET  /api/jobs/<id>             the job's manifest (no server paths)
  GET  /api/jobs/<id>/preview.gif the preview

Job ids come only from our own table. Nothing in a request is used as a file name or path.

Run: python3 -m stillmove.web.server [port]    (default 8800)
"""
import errno
import json
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOST = "127.0.0.1"
DEFAULT_PORT = 8800
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
# A page on another site cannot set this header without a CORS preflight, which this server never
# grants. POST requests must carry it, and the page sends it on its own render POST.
WRITE_HEADER = "X-Stillmove-Request"
WRITE_HEADER_VALUE = "1"
MAX_UPLOAD = 5 * 1024 * 1024
MAX_BODY = MAX_UPLOAD + 64 * 1024  # the file plus multipart framing and the small text fields
MAX_SIDE = 4096          # largest accepted image side, in pixels
MAX_PIXELS = 16_000_000  # largest accepted image, in pixels
RENDER_WIDTH = 640       # renders use at most this width; the image is scaled by the renderer
FORMAT_EXT = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "BMP": "bmp", "GIF": "gif"}
JOB_ID = re.compile(r"[0-9a-f]{32}")
HTML = "text/html; charset=utf-8"
JSON = "application/json"
DEFAULT_BACKENDS = ("plane", "gradient")
MAX_JOBS = 20  # the newest jobs are kept; older job folders are deleted


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _default_presets():
    from stillmove import _cm

    _cm.import_camera_motion()  # importing _cm alone does not touch sys.path
    from camera_motion.presets import load_catalog

    return load_catalog()


def _default_limits():
    from stillmove import limits

    return limits.load_limits()


def _default_cameras(preset_id, seconds, fps, width, height):
    from stillmove import cameras

    return cameras.from_preset(preset_id, seconds, fps, width, height)


def _default_backend(name):
    from stillmove import depth

    return depth.get_backend(name)


def _default_render(image_path, cams, backend, outdir, mp4=False):
    from stillmove import render

    return render.render(image_path, cams, backend, outdir, mp4=mp4)


def _read_page():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.html"), "rb") as f:
        return f.read()


class Studio:
    """Presets, backends and the jobs rendered so far. Every dependency can be replaced for tests."""

    def __init__(self, presets=None, backends=DEFAULT_BACKENDS, cameras=None, backend=None,
                 renderer=None, page=None, job_root=None, limits=None, max_jobs=MAX_JOBS):
        self.presets = list(presets) if presets is not None else _default_presets()
        self.backends = tuple(backends)
        self.limits = limits if limits is not None else _default_limits()  # {move type: {param, limit, unit}}
        self.cameras = cameras or _default_cameras
        self.backend = backend or _default_backend
        self.renderer = renderer or _default_render
        self.page = page if page is not None else _read_page()
        self._own_root = job_root is None
        self.job_root = job_root or tempfile.mkdtemp(prefix="stillmove-jobs-")
        self.jobs = {}  # job id -> {"dir": path, "manifest": public manifest}, oldest first
        self.max_jobs = max_jobs
        self._lock = threading.Lock()
        self._render_lock = threading.Lock()  # one render at a time

    def close(self):
        if self._own_root:
            shutil.rmtree(self.job_root, ignore_errors=True)

    def preset(self, preset_id):
        for preset in self.presets:
            if preset.get("id") == preset_id:
                return preset
        raise ApiError(400, "unknown preset")

    def safe_range(self, preset):
        """The single-photo safe range for the preset's move, or None when it has none.

        {param, limit, unit, value, exceeds}: value is the preset's own size, exceeds is value > limit.
        """
        record = self.limits.get(preset.get("type"))
        params = preset.get("params", {})
        if record is None or record["param"] not in params:
            return None
        value = abs(params[record["param"]])
        return {"param": record["param"], "limit": record["limit"], "unit": record["unit"],
                "value": value, "exceeds": value > record["limit"]}

    def public_presets(self):
        return [{"id": p["id"], "name": p.get("name", p["id"]), "type": p.get("type", ""),
                 "description": p.get("description", ""), "safe_range": self.safe_range(p)}
                for p in self.presets]

    def render_job(self, image_bytes, preset_id, backend_name, seconds, fps, mp4):
        from PIL import Image
        import io

        self.preset(preset_id)
        if backend_name not in self.backends:
            raise ApiError(400, "unknown backend; choose one of " + ", ".join(self.backends))
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                fmt, (width, height) = img.format, img.size
        except Exception:
            raise ApiError(400, "the upload is not an image this server can read") from None
        if fmt not in FORMAT_EXT:
            raise ApiError(400, "image format must be one of PNG, JPEG, WEBP, BMP, GIF")
        if max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
            raise ApiError(400, f"image is too large (at most {MAX_SIDE} px a side, {MAX_PIXELS // 1_000_000} megapixels)")

        job_id = uuid.uuid4().hex
        job_dir = os.path.join(self.job_root, job_id)
        os.makedirs(job_dir)
        image_path = os.path.join(job_dir, "input." + FORMAT_EXT[fmt])  # our name, not the client's
        with open(image_path, "wb") as f:
            f.write(image_bytes)

        out_w = min(width, RENDER_WIDTH)
        out_h = max(1, round(height * out_w / width))
        try:
            with self._render_lock:
                cams = self.cameras(preset_id, seconds, fps, out_w, out_h)
                manifest = self.renderer(image_path, cams, self.backend(backend_name),
                                         os.path.join(job_dir, "out"), mp4=mp4)
        except ApiError:
            raise
        except Exception as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise ApiError(500, f"render failed: {type(exc).__name__}: {exc}") from None

        preview = os.path.join(job_dir, "out", manifest.get("preview") or "preview.gif")
        public = {
            "id": job_id, "preset": preset_id, "backend": manifest.get("backend", backend_name),
            "fps": manifest.get("fps", fps), "width": manifest.get("width", out_w),
            "height": manifest.get("height", out_h), "frames": len(manifest.get("frames", [])),
            "preview": f"/api/jobs/{job_id}/preview.gif", "mp4": bool(manifest.get("mp4")),
        }
        stale = []
        with self._lock:
            self.jobs[job_id] = {"dir": job_dir, "preview": preview, "manifest": public}
            while len(self.jobs) > self.max_jobs:  # drop the oldest job and its folder
                stale.append(self.jobs.pop(next(iter(self.jobs)))["dir"])
        for old_dir in stale:
            shutil.rmtree(old_dir, ignore_errors=True)
        return job_id

    def job(self, job_id):
        with self._lock:
            job = self.jobs.get(job_id) if JOB_ID.fullmatch(job_id) else None
        if job is None:
            raise ApiError(404, "no such job")
        return job


def parse_multipart(content_type, body):
    """Return ({field: text}, {field: bytes}) from a multipart/form-data body."""
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(400, "expected multipart/form-data")
    raw = b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if not message.is_multipart():
        raise ApiError(400, "malformed multipart body")
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if part.get_filename() is not None:
            files[name] = payload
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


def _number(fields, key, default, low, high, integer=False):
    if key not in fields:
        return default
    try:
        value = int(fields[key]) if integer else float(fields[key])
    except ValueError:
        raise ApiError(400, f"{key} must be a number") from None
    if not low <= value <= high or value != value:
        raise ApiError(400, f"{key} must be between {low} and {high}")
    return value


class StudioHandler(BaseHTTPRequestHandler):
    studio = None  # set by make_server

    def log_message(self, format, *args):
        pass

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, value, status=200):
        self._send(status, JSON, json.dumps(value).encode("utf-8"))

    def _guard(self, fn):
        try:
            if not _host_ok(self.headers.get("Host", "")):
                raise ApiError(403, "only 127.0.0.1 and localhost are served")
            # A page on another site can make a browser send a request here. Refuse any request
            # whose Origin is not this machine: a cross-site form cannot forge the Origin header.
            origin = self.headers.get("Origin")
            if origin is not None and urlsplit(origin).hostname not in ALLOWED_HOSTS:
                raise ApiError(403, "cross-origin requests are refused")
            if self.command == "POST" and self.headers.get(WRITE_HEADER) != WRITE_HEADER_VALUE:
                raise ApiError(403, f"POST needs the {WRITE_HEADER} header")
            fn()
        except ApiError as err:
            self._json({"error": str(err)}, err.status)

    def do_GET(self):
        self._guard(lambda: self._get(urlsplit(self.path).path))

    def do_POST(self):
        self._guard(lambda: self._post(urlsplit(self.path).path))

    def _get(self, route):
        studio = self.studio
        if route == "/":
            return self._send(200, HTML, studio.page)
        if route == "/api/presets":
            return self._json(studio.public_presets())
        if route == "/api/backends":
            return self._json(list(studio.backends))
        match = re.fullmatch(r"/api/jobs/([^/]+)", route)
        if match:
            return self._json(studio.job(match.group(1))["manifest"])
        match = re.fullmatch(r"/api/jobs/([^/]+)/preview\.gif", route)
        if match:
            job = studio.job(match.group(1))
            try:
                with open(job["preview"], "rb") as f:
                    body = f.read()
            except OSError:
                raise ApiError(404, "the job has no preview") from None
            return self._send(200, "image/gif", body)
        raise ApiError(404, "no such page")

    def _post(self, route):
        if route != "/api/render":
            raise ApiError(404, "no such page")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(411, "Content-Length is required") from None
        if length < 0:
            raise ApiError(400, "bad Content-Length")
        if length > MAX_BODY:
            raise ApiError(413, "the upload is larger than 5 MB")
        body = self.rfile.read(length)
        fields, files = parse_multipart(self.headers.get("Content-Type", ""), body)
        image = files.get("image")
        if image is None:
            raise ApiError(400, "an image file is required")
        if len(image) > MAX_UPLOAD:
            raise ApiError(413, "the image is larger than 5 MB")
        studio = self.studio
        job_id = studio.render_job(
            image, fields.get("preset", ""), fields.get("backend", DEFAULT_BACKENDS[1]),
            _number(fields, "seconds", 3.0, 0.5, 10.0), _number(fields, "fps", 12, 1, 30, integer=True),
            fields.get("mp4") == "1" and shutil.which("ffmpeg") is not None)
        self._json({"id": job_id})


def _host_ok(host_header):
    """Host must be 127.0.0.1 or localhost, with an optional port."""
    name = host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header
    return name in ALLOWED_HOSTS


class StudioServer(ThreadingHTTPServer):
    allow_reuse_address = False  # a second server on the same port must fail, not share it
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind asks getfqdn(), which can block on DNS. We only need the address.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def make_server(studio, port=DEFAULT_PORT):
    """A StudioServer on 127.0.0.1:port serving studio. Port 0 picks a free port."""
    handler = type("Handler", (StudioHandler,), {"studio": studio})
    return StudioServer((HOST, port), handler)


def serve(port=DEFAULT_PORT, studio=None):
    studio = studio or Studio()
    try:
        httpd = make_server(studio, port)
    except OSError as err:
        studio.close()
        if err.errno == errno.EADDRINUSE:
            print(f"error: port {port} in use", file=sys.stderr)
        else:
            print(f"error: cannot listen on {HOST}:{port}: {err}", file=sys.stderr)
        return 1
    print(f"stillmove on http://{HOST}:{httpd.server_address[1]}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        studio.close()
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        port = int(argv[0]) if argv else DEFAULT_PORT
    except ValueError:
        print("usage: python3 -m stillmove.web.server [port]", file=sys.stderr)
        return 2
    return serve(port)


if __name__ == "__main__":
    sys.exit(main())
