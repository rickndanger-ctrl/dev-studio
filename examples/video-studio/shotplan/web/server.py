"""Shot-plan web page: pick a sample plan, edit its shots, check it live, and render a preview.

Stdlib only. Binds to 127.0.0.1 and accepts only Host 127.0.0.1 or localhost. The render runs
in a subprocess under sys.executable (this server's own Python, which has numpy and Pillow in
the venv that runs it), so this server never imports them.

  GET  /                        the page
  GET  /api/plans               sample plans: [{name, title}]
  GET  /api/plan?name=NAME      one sample plus check: {name, plan, errors, rows, svg}
  GET  /api/catalog             preset ids
  POST /api/check               {"plan": {...}} live check: {errors, rows, svg}; writes nothing
  POST /api/render              {"plan": {...}} one background preview at scale 0.25 with the fake
                                depth: 202 {"job"}, 409 while a render runs, 400 on check errors
  GET  /api/render/status       {"state": idle|running|done|error, "message", "gif"}
  GET  /renders/<job>/preview.gif

Paths a plan names (image files and sequence files) must stay inside the samples folder.
Nothing in a request is used as a file name except the sample name, checked against a fixed
pattern and the samples folder listing.

Run from video-studio/: python3 -m shotplan.web.server [port]    (default 8810)
"""

import errno
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTPLAN_DIR = os.path.dirname(HERE)
SAMPLES = os.path.join(SHOTPLAN_DIR, "samples")
VIDEO_STUDIO = os.path.dirname(SHOTPLAN_DIR)  # the project root: `import shotplan` and `python3 -m shotplan` run here
if VIDEO_STUDIO not in sys.path:
    sys.path.insert(0, VIDEO_STUDIO)

HOST = "127.0.0.1"
DEFAULT_PORT = 8810
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
MAX_JOBS = 20             # render folders kept on disk; the newest are kept
MAX_BODY = 64 * 1024          # a plan is a few kilobytes; anything bigger is refused
RENDER_SCALE = 0.25
PLAN_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
HTML = "text/html; charset=utf-8"
JSON = "application/json"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _page():
    with open(os.path.join(HERE, "app.html"), "rb") as f:
        return f.read()


def _inside(folder, rel):
    """True if the relative path rel names a file inside folder (no absolute paths, no ..)."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return False
    real_folder = os.path.realpath(folder)
    real = os.path.realpath(os.path.join(real_folder, rel))
    return real.startswith(real_folder + os.sep)


def _plan_paths_ok(plan):
    """Every file a plan names must stay inside the samples folder."""
    if not isinstance(plan, dict) or not isinstance(plan.get("shots"), list):
        return True  # shape errors are reported by check()
    for shot in plan["shots"]:
        if not isinstance(shot, dict):
            continue
        seq = shot.get("sequence")
        if isinstance(seq, str) and not _inside(SAMPLES, seq):
            return False
        image = shot.get("image")
        if isinstance(image, str) and not image.startswith("placeholder:") and not _inside(SAMPLES, image):
            return False
    return True


class Studio:
    """The sample plans, the checks, and the one background render. Every dependency is injectable."""

    def __init__(self, samples=SAMPLES, renderer=None, job_root=None, page=None, max_jobs=MAX_JOBS):
        import shotplan

        self.shotplan = shotplan
        self.samples = samples
        self.renderer = renderer or _render_subprocess
        self.page = page if page is not None else _page()
        self._own_root = job_root is None
        self.job_root = job_root or tempfile.mkdtemp(prefix="shotplan-renders-")
        self.catalog = shotplan.load_json(shotplan.CATALOG)
        self._lock = threading.Lock()
        self.job = None  # {"id", "state", "message", "gif"} for the latest render
        self.max_jobs = max_jobs
        self._job_dirs = []  # job folders on disk, oldest first; older ones are deleted

    def close(self):
        if self._own_root:
            shutil.rmtree(self.job_root, ignore_errors=True)

    # --- samples and checks -------------------------------------------------------------

    def plans(self):
        out = []
        for name in sorted(os.listdir(self.samples)):
            if name.endswith(".json") and PLAN_NAME.match(name[:-5]):
                title = name[:-5]
                try:
                    with open(os.path.join(self.samples, name), encoding="utf-8") as f:
                        title = json.load(f).get("name", title)
                except (OSError, ValueError):
                    pass
                out.append({"name": name[:-5], "title": title})
        return out

    def sample(self, name):
        if not isinstance(name, str) or not PLAN_NAME.match(name):
            raise ApiError(400, "plan names are lower-case letters, digits, '-' or '_'")
        path = os.path.join(self.samples, name + ".json")
        if not os.path.isfile(path):
            raise ApiError(404, "no such plan")
        try:
            with open(path, encoding="utf-8") as f:
                return self.shotplan.load_json(path)
        except (OSError, ValueError) as exc:
            raise ApiError(500, f"cannot read the plan: {exc}") from None

    def checked(self, plan):
        """check() for a plan the page sent: errors, timeline rows and the SVG. Writes nothing."""
        if not _plan_paths_ok(plan):
            raise ApiError(400, "paths in a plan must stay inside the samples folder")
        errors, rows, sequences = self.shotplan.check(plan, self.samples, self.catalog)
        svg = None
        if not errors:
            svg = self.shotplan.timeline_svg(plan, rows, sequences, self.catalog)
        return {
            "errors": errors,
            "rows": [{"start": r[0], "end": r[1], "shot": r[2], "moves": r[3], "transition": r[4]} for r in rows],
            "svg": svg,
        }

    # --- render -------------------------------------------------------------------------

    def start_render(self, plan):
        result = self.checked(plan)
        if result["errors"]:
            raise ApiError(400, "the plan has errors: " + "; ".join(result["errors"][:3]))
        with self._lock:
            if self.job is not None and self.job["state"] == "running":
                raise ApiError(409, "a render is already running")
            job_id = uuid.uuid4().hex
            job_dir = os.path.join(self.job_root, job_id)
            os.makedirs(job_dir)
            self._stage(plan, job_dir)
            self.job = {"id": job_id, "state": "running", "message": "rendering…", "gif": None}
            self._job_dirs.append(job_dir)
            while len(self._job_dirs) > self.max_jobs:  # drop the oldest job folder
                shutil.rmtree(self._job_dirs.pop(0), ignore_errors=True)
        threading.Thread(target=self._run, args=(job_id, job_dir), daemon=True).start()
        return job_id

    def _stage(self, plan, job_dir):
        """Write plan.json and copy the files it names into job_dir, so the render reads only those."""
        with open(os.path.join(job_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f)
        for shot in plan.get("shots", []):
            names = []
            if isinstance(shot.get("sequence"), str):
                names.append(shot["sequence"])
            if not str(shot.get("image", "")).startswith("placeholder:"):
                names.append(shot.get("image", ""))
            for rel in names:
                src = os.path.join(self.samples, rel)
                dst = os.path.join(job_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)

    def _run(self, job_id, job_dir):
        try:
            self.renderer(os.path.join(job_dir, "plan.json"), os.path.join(job_dir, "out"))
            state = ("done", "preview ready", f"/renders/{job_id}/preview.gif")
        except Exception as exc:  # any failure becomes the job's error message
            state = ("error", str(exc) or type(exc).__name__, None)
        with self._lock:
            if self.job is not None and self.job["id"] == job_id:
                self.job.update(state=state[0], message=state[1], gif=state[2])

    def status(self):
        with self._lock:
            if self.job is None:
                return {"state": "idle", "message": "", "gif": None}
            return dict(self.job)

    def gif_path(self, job_id):
        if not JOB_ID.match(job_id):
            raise ApiError(404, "no such render")
        with self._lock:
            ok = self.job is not None and self.job["id"] == job_id and self.job["state"] == "done"
        if not ok:
            raise ApiError(404, "no such render")
        path = os.path.join(self.job_root, job_id, "out", "preview.gif")
        if not os.path.isfile(path):
            raise ApiError(404, "no such render")
        return path


def _render_subprocess(plan_path, outdir):
    """The real render: python -m shotplan render under sys.executable, run in video-studio/."""
    cmd = [sys.executable, "-m", "shotplan", "render", plan_path, outdir, "--depth", "fake",
           "--scale", str(RENDER_SCALE)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=VIDEO_STUDIO)
    if result.returncode != 0:
        lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
        raise RuntimeError(lines[-1] if lines else f"render exited with status {result.returncode}")
    return os.path.join(outdir, "preview.gif")


class StudioHandler(BaseHTTPRequestHandler):
    studio = None  # set by make_server

    def log_message(self, format, *args):
        pass

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
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
            fn()
        except ApiError as err:
            self._json({"error": str(err)}, err.status)
        except Exception:
            self._json({"error": "internal error"}, 500)

    def do_GET(self):
        self._guard(lambda: self._get(urlsplit(self.path)))

    def do_POST(self):
        self._guard(lambda: self._post(urlsplit(self.path).path))

    def do_PUT(self):
        self._json({"error": "method not allowed"}, 405)

    do_DELETE = do_PATCH = do_PUT

    def _get(self, url):
        studio = self.studio
        route = url.path
        if route == "/":
            return self._send(200, HTML, studio.page)
        if route == "/api/plans":
            return self._json(studio.plans())
        if route == "/api/plan":
            name = parse_qs(url.query).get("name", [""])[0]
            plan = studio.sample(name)
            checked = studio.checked(plan)
            return self._json(dict(checked, name=name, plan=plan))
        if route == "/api/catalog":
            return self._json(sorted(p["id"] for p in studio.catalog))
        if route == "/api/render/status":
            return self._json(studio.status())
        match = re.fullmatch(r"/renders/([^/]+)/preview\.gif", route)
        if match:
            with open(studio.gif_path(match.group(1)), "rb") as f:
                return self._send(200, "image/gif", f.read())
        raise ApiError(404, "no such page")

    def _post_json(self):
        # Only application/json is read. A cross-site form can only send form or text bodies,
        # and those need a CORS preflight that this server never answers.
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(415, "POST bodies must be application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(411, "Content-Length is required") from None
        if length < 0:
            raise ApiError(400, "bad Content-Length")
        if length > MAX_BODY:
            self.close_connection = True  # the unread body must not be read as the next request
            raise ApiError(413, "the request body is larger than 64 KB")
        body = self.rfile.read(length)
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "the body must be JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("plan"), dict):
            raise ApiError(400, 'the body must be {"plan": {...}}')
        return data["plan"]

    def _post(self, route):
        studio = self.studio
        if route == "/api/check":
            return self._json(studio.checked(self._post_json()))
        if route == "/api/render":
            job_id = studio.start_render(self._post_json())
            return self._json({"job": job_id}, 202)
        raise ApiError(404, "no such page")


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
            print(f"error: port {port} is already in use; pick another with --port", file=sys.stderr)
        else:
            print(f"error: cannot listen on {HOST}:{port}: {err}", file=sys.stderr)
        return 1
    print(f"shotplan on http://{HOST}:{httpd.server_address[1]}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        studio.close()
    return 0


if __name__ == "__main__":
    sys.exit(serve(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT))
