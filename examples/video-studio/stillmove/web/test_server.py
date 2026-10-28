import http.client
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image

from stillmove.web import server
from stillmove.web.server import Studio, make_server

PRESETS = [
    {"id": "dolly_in", "name": "Dolly in", "type": "dolly", "params": {"axis": "z", "distance": 1.0},
     "description": "Move closer."},
    {"id": "orbit_left", "name": "Orbit left", "type": "orbit", "params": {"angle": -5, "radius": 5.0},
     "description": "Circle the subject."},
    {"id": "wobble", "name": "Wobble", "type": "wobble", "params": {}, "description": "No limit known."},
]
LIMITS = {"dolly": {"param": "distance", "limit": 0.2, "unit": "scene units"},
          "orbit": {"param": "angle", "limit": 8.0, "unit": "degrees"}}
BOUNDARY = "----stillmovetestboundary"
WRITE = {server.WRITE_HEADER: server.WRITE_HEADER_VALUE}  # the page sends this on every POST


def png_bytes(size=(8, 6)):
    out = io.BytesIO()
    Image.new("RGB", size, (200, 100, 50)).save(out, "PNG")
    return out.getvalue()


def gif_bytes():
    out = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(out, "GIF")
    return out.getvalue()


def multipart(fields, file_field=None, filename="photo.png", payload=b""):
    parts = []
    for name, value in fields.items():
        parts.append(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    if file_field:
        parts.append((f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{file_field}"; '
                      f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                     + payload + b"\r\n")
    parts.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={BOUNDARY}"


class FakeCameras:
    def __init__(self, preset_id, seconds, fps, width, height):
        self.preset_id, self.seconds, self.fps, self.width, self.height = preset_id, seconds, fps, width, height
        self.frames = [object()] * 3


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def cameras(preset_id, seconds, fps, width, height):
            self.calls.append(("cameras", preset_id, seconds, fps, width, height))
            return FakeCameras(preset_id, seconds, fps, width, height)

        def render(image_path, cams, backend, outdir, mp4=False):
            self.calls.append(("render", image_path, backend, outdir))
            os.makedirs(outdir)
            with open(os.path.join(outdir, "preview.gif"), "wb") as f:
                f.write(gif_bytes())
            return {"frames": [os.path.join(outdir, f"f{i}.png") for i in range(3)], "preview": "preview.gif",
                    "mp4": None, "backend": backend, "fps": cams.fps, "width": cams.width, "height": cams.height}

        self.studio = Studio(presets=PRESETS, cameras=cameras, backend=lambda name: name, renderer=render,
                             page=b"<!doctype html><title>test</title>", limits=LIMITS)
        self.addCleanup(self.studio.close)
        self.httpd = make_server(self.studio, port=0)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.read()
        finally:
            conn.close()

    def post_render(self, fields=None, payload=None, filename="photo.png"):
        fields = {"preset": "dolly_in", "backend": "gradient"} if fields is None else fields
        body, ctype = multipart(fields, "image", filename, png_bytes() if payload is None else payload)
        return self.request("POST", "/api/render", body, {"Content-Type": ctype, **WRITE})

    def render_ok(self, **kw):
        status, _, body = self.post_render(**kw)
        self.assertEqual(status, 200, body)
        return json.loads(body)["id"]

    def assert_error(self, result, status):
        got, ctype, body = result
        self.assertEqual(got, status, body)
        self.assertEqual(ctype, server.JSON)
        self.assertIn("error", json.loads(body))

    # --- binding ---------------------------------------------------------------

    def test_binds_to_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_bind_does_not_call_getfqdn(self):
        with mock.patch("socket.getfqdn", side_effect=AssertionError("getfqdn called")):
            httpd = make_server(self.studio, port=0)
        self.addCleanup(httpd.server_close)
        self.assertEqual(httpd.server_name, "127.0.0.1")

    def test_second_server_on_the_same_port_fails(self):
        self.assertFalse(server.StudioServer.allow_reuse_address)
        with self.assertRaises(OSError):
            make_server(self.studio, port=self.port)

    def test_serve_says_port_in_use(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code = server.serve(self.port, studio=Studio(presets=PRESETS, page=b"x"))
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue().strip(), f"error: port {self.port} in use")

    def test_default_port(self):
        self.assertEqual(server.DEFAULT_PORT, 8800)

    # --- read routes -----------------------------------------------------------

    def test_page(self):
        status, ctype, body = self.request("GET", "/")
        self.assertEqual((status, ctype), (200, server.HTML))
        self.assertEqual(body, b"<!doctype html><title>test</title>")

    def test_presets_and_backends(self):
        status, ctype, body = self.request("GET", "/api/presets")
        self.assertEqual((status, ctype), (200, server.JSON))
        self.assertEqual([p["id"] for p in json.loads(body)], ["dolly_in", "orbit_left", "wobble"])
        self.assertEqual(json.loads(self.request("GET", "/api/backends")[2]), ["plane", "gradient"])

    def test_presets_carry_the_safe_range(self):
        presets = {p["id"]: p for p in json.loads(self.request("GET", "/api/presets")[2])}
        self.assertEqual(presets["dolly_in"]["safe_range"], {
            "param": "distance", "limit": 0.2, "unit": "scene units", "value": 1.0, "exceeds": True})
        self.assertEqual(presets["orbit_left"]["safe_range"], {
            "param": "angle", "limit": 8.0, "unit": "degrees", "value": 5, "exceeds": False})
        self.assertIsNone(presets["wobble"]["safe_range"])

    def test_unknown_route_is_404(self):
        self.assert_error(self.request("GET", "/nope"), 404)
        self.assert_error(self.request("POST", "/api/presets", b"", {"Content-Length": "0", **WRITE}), 404)

    # --- rendering -------------------------------------------------------------

    def test_render_then_manifest_and_preview(self):
        job_id = self.render_ok()
        status, ctype, body = self.request("GET", f"/api/jobs/{job_id}")
        self.assertEqual((status, ctype), (200, server.JSON))
        manifest = json.loads(body)
        self.assertEqual(manifest["id"], job_id)
        self.assertEqual(manifest["preset"], "dolly_in")
        self.assertEqual(manifest["backend"], "gradient")
        self.assertEqual(manifest["frames"], 3)
        self.assertEqual(manifest["preview"], f"/api/jobs/{job_id}/preview.gif")
        status, ctype, gif = self.request("GET", manifest["preview"])
        self.assertEqual((status, ctype), (200, "image/gif"))
        self.assertTrue(gif.startswith(b"GIF8"))

    def test_manifest_does_not_leak_server_paths(self):
        job_id = self.render_ok()
        text = self.request("GET", f"/api/jobs/{job_id}")[2].decode()
        self.assertNotIn(self.studio.job_root, text)

    def test_render_size_follows_the_image_and_is_capped(self):
        self.render_ok(payload=png_bytes((2000, 1000)))
        cameras_call = [c for c in self.calls if c[0] == "cameras"][-1]
        self.assertEqual(cameras_call[4:], (640, 320))
        self.render_ok(payload=png_bytes((100, 50)))
        self.assertEqual([c for c in self.calls if c[0] == "cameras"][-1][4:], (100, 50))

    def test_client_file_name_is_ignored(self):
        job_id = self.render_ok(filename="../../evil.png")
        job_dir = self.studio.jobs[job_id]["dir"]
        self.assertEqual(sorted(os.listdir(job_dir)), ["input.png", "out"])
        self.assertEqual(os.path.dirname(job_dir), self.studio.job_root)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.studio.job_root), "evil.png")))

    def test_bad_requests_are_400(self):
        self.assert_error(self.post_render({"preset": "nope", "backend": "gradient"}), 400)
        self.assert_error(self.post_render({"preset": "dolly_in", "backend": "nope"}), 400)
        self.assert_error(self.post_render(payload=b"not an image at all"), 400)
        self.assert_error(self.post_render({"preset": "dolly_in", "backend": "plane", "seconds": "abc"}), 400)
        self.assert_error(self.post_render({"preset": "dolly_in", "backend": "plane", "seconds": "99"}), 400)
        body, ctype = multipart({"preset": "dolly_in", "backend": "plane"})
        self.assert_error(self.request("POST", "/api/render", body, {"Content-Type": ctype, **WRITE}), 400)
        self.assert_error(self.request("POST", "/api/render", b"{}", {"Content-Type": "application/json", **WRITE}), 400)

    def test_huge_image_dimensions_are_400(self):
        self.assert_error(self.post_render(payload=png_bytes((5000, 10))), 400)

    def test_upload_over_5_mb_is_413(self):
        self.assert_error(self.post_render(payload=b"x" * (server.MAX_UPLOAD + 1)), 413)
        self.assertEqual(self.studio.jobs, {})

    def test_missing_content_length_is_refused(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            sock.sendall(b"POST /api/render HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Stillmove-Request: 1\r\nContent-Type: multipart/form-data; boundary=x\r\n\r\n")
            self.assertIn(b" 411 ", sock.recv(200))
        finally:
            sock.close()

    def test_render_failure_is_500_and_leaves_no_job(self):
        def boom(*args, **kwargs):
            raise RuntimeError("no depth")

        self.studio.renderer = boom
        self.assert_error(self.post_render(), 500)
        self.assertEqual(self.studio.jobs, {})
        self.assertEqual(os.listdir(self.studio.job_root), [])

    # --- job ids ---------------------------------------------------------------

    def test_job_ids_come_only_from_our_table(self):
        for raw in ("/api/jobs/" + "0" * 32, "/api/jobs/abc", "/api/jobs/..", "/api/jobs/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
                    "/api/jobs/../../etc/passwd", "/api/jobs/" + "0" * 32 + "/preview.gif"):
            with self.subTest(path=raw):
                self.assert_error(self.request("GET", raw), 404)

    def test_a_real_looking_id_that_is_not_ours_is_404(self):
        os.makedirs(os.path.join(self.studio.job_root, "a" * 32))  # a folder exists, but is not in our table
        self.assert_error(self.request("GET", "/api/jobs/" + "a" * 32), 404)

    def test_missing_preview_file_is_404(self):
        job_id = self.render_ok()
        os.remove(self.studio.jobs[job_id]["preview"])
        self.assert_error(self.request("GET", f"/api/jobs/{job_id}/preview.gif"), 404)


class GuardTests(unittest.TestCase):
    """Host, Origin and the write header: only this machine's pages may use the API."""

    setUp = ServerTests.setUp
    request = ServerTests.request

    def test_foreign_host_is_refused(self):
        status, ctype, body = self.request("GET", "/", headers={"Host": "evil.example"})
        self.assertEqual(status, 403, body)
        self.assertEqual(ctype, server.JSON)

    def test_localhost_host_is_allowed(self):
        status, _, _ = self.request("GET", "/", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_foreign_origin_is_refused_for_get_and_post(self):
        status, _, _ = self.request("GET", "/api/presets", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        body, ctype = multipart({"preset": "dolly_in", "backend": "gradient"}, "image", "photo.png", png_bytes())
        headers = {"Content-Type": ctype, "Origin": "http://evil.example", **WRITE}
        status, _, _ = self.request("POST", "/api/render", body, headers)
        self.assertEqual(status, 403)
        self.assertEqual(self.calls, [], "a refused render must not run")

    def test_post_without_the_write_header_is_refused(self):
        body, ctype = multipart({"preset": "dolly_in", "backend": "gradient"}, "image", "photo.png", png_bytes())
        status, ctype_out, raw = self.request("POST", "/api/render", body, {"Content-Type": ctype})
        self.assertEqual(status, 403, raw)
        self.assertEqual(ctype_out, server.JSON)
        self.assertEqual(self.calls, [])

    def test_the_page_own_post_still_renders(self):
        origin = {"Origin": f"http://127.0.0.1:{self.port}"}
        body, ctype = multipart({"preset": "dolly_in", "backend": "gradient"}, "image", "photo.png", png_bytes())
        status, _, raw = self.request("POST", "/api/render", body, {"Content-Type": ctype, **WRITE, **origin})
        self.assertEqual(status, 200, raw)
        self.assertIn("id", json.loads(raw))


class RealLimitsTests(unittest.TestCase):
    def test_every_catalog_preset_has_a_range_from_limits_json(self):
        studio = Studio(page=b"x")
        self.addCleanup(studio.close)
        presets = studio.public_presets()
        self.assertTrue(presets)
        for preset in presets:
            with self.subTest(id=preset["id"]):
                self.assertIsNotNone(preset["safe_range"])
                self.assertGreater(preset["safe_range"]["limit"], 0)


class PageTests(unittest.TestCase):
    def test_real_page_is_dom_only_and_local(self):
        page = server._read_page().decode("utf-8")
        self.assertNotIn("innerHTML", page)
        self.assertNotIn("http://", page.replace("<!doctype", ""))
        self.assertNotIn("https://", page)
        self.assertIn("safe_range", page)
        self.assertIn("Beyond the safe range: expect torn edges.", page)
        self.assertIn("exceeds", page)
        for route in ("/api/presets", "/api/backends", "/api/render", "/api/jobs/"):
            self.assertIn(route, page)


if __name__ == "__main__":
    unittest.main()


class DefaultPresetsTests(unittest.TestCase):
    """The real loader, not an injected fake: `stillmove serve` failed without it."""

    def test_default_presets_load_in_a_fresh_interpreter(self):
        import subprocess
        import sys

        code = "from stillmove.web import server; print(len(server._default_presets()))"
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "14")
