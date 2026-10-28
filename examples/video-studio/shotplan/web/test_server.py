"""Tests for the shot-plan web server: every route, the refusals (host, name, traversal, body size,
JSON, paths), the render job lifecycle with an injected fake renderer, and one real render
through sys.executable (the interpreter running the tests, which has numpy and Pillow)."""

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

VIDEO_STUDIO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if VIDEO_STUDIO not in sys.path:
    sys.path.insert(0, VIDEO_STUDIO)

from shotplan.web import server as web  # noqa: E402

SAMPLES = web.SAMPLES
GIF_BYTES = b"GIF89a" + b"\x00" * 16


def plan(duration=1, shots=None, width=64, height=36):
    """A small plan with placeholder images: fast, and needs no sample files."""
    if shots is None:
        shots = [{"id": "a", "image": "placeholder:a",
                  "sequence": {"segments": [{"preset": "dolly_in", "duration_s": duration}]},
                  "duration": duration, "transition": "cut"}]
    return {"id": "tiny", "name": "Tiny", "fps": 24, "width": width, "height": height, "shots": shots}


def snapshot(folder):
    """Every file under folder with its size and mtime, so a test can prove nothing was written."""
    out = {}
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            st = os.stat(path)
            out[path] = (st.st_size, st.st_mtime_ns)
    return out


def fake_renderer(gate=None, fail=None):
    """A renderer that writes a GIF stub, or waits for gate, or raises fail."""
    def render(plan_path, outdir):
        if gate is not None:
            gate.wait(10)
        if fail is not None:
            raise RuntimeError(fail)
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "preview.gif"), "wb") as f:
            f.write(GIF_BYTES)
        return os.path.join(outdir, "preview.gif")
    return render


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, host=None, raw=None, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": host or "127.0.0.1:%d" % self.port}
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        if data is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
        headers.update(extra or {})
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        ctype = resp.getheader("Content-Type", "")
        conn.close()
        if ctype.startswith("application/json"):
            return resp.status, json.loads(payload.decode("utf-8"))
        return resp.status, payload

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def wait_done(self, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            _, status = self.get("/api/render/status")
            if status["state"] != "running":
                return status
            time.sleep(0.05)
        self.fail("render did not finish in %s s" % timeout)


class ServerCase(unittest.TestCase):
    renderer = None  # set by subclasses

    def setUp(self):
        self.job_root = tempfile.mkdtemp()
        self.studio = web.Studio(renderer=self.renderer or fake_renderer(), job_root=self.job_root)
        self.httpd = web.make_server(self.studio, 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.client = Client(self.port)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.job_root, ignore_errors=True)

class RoutesTests(ServerCase):
    def test_index_serves_the_page(self):
        status, body = self.client.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Shot plans", body)

    def test_plans_lists_the_samples(self):
        status, plans = self.client.get("/api/plans")
        self.assertEqual(status, 200)
        names = {p["name"] for p in plans}
        self.assertTrue({"house_tour", "product_reveal", "travel_montage"} <= names, names)

    def test_plan_returns_the_checked_sample(self):
        status, data = self.client.get("/api/plan?name=house_tour")
        self.assertEqual(status, 200)
        self.assertEqual(data["errors"], [])
        self.assertTrue(data["rows"])
        self.assertIn("<svg", data["svg"])
        self.assertEqual(data["plan"]["id"], "house_tour")

    def test_catalog_lists_preset_ids(self):
        status, ids = self.client.get("/api/catalog")
        self.assertEqual(status, 200)
        self.assertIn("dolly_in", ids)
        self.assertEqual(ids, sorted(ids))

    def test_status_is_idle_before_any_render(self):
        status, body = self.client.get("/api/render/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "idle")

    def test_check_returns_errors_inline(self):
        bad = plan(duration=2, shots=[{"id": "a", "image": "placeholder:a",
                                       "sequence": {"segments": [{"preset": "dolly_in", "duration_s": 1}]},
                                       "duration": 2, "transition": "cut"}])
        status, body = self.client.post("/api/check", {"plan": bad})
        self.assertEqual(status, 200)
        self.assertTrue(any(e.startswith("shots[0].duration") for e in body["errors"]), body["errors"])
        self.assertIsNone(body["svg"])

    def test_check_of_a_good_plan_has_svg_and_rows(self):
        status, body = self.client.post("/api/check", {"plan": plan()})
        self.assertEqual(status, 200)
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["rows"][0]["shot"], "a")
        self.assertIn("<svg", body["svg"])

    def test_check_writes_no_files(self):
        before_samples = snapshot(SAMPLES)
        before_jobs = snapshot(self.job_root)
        bad = plan(duration=2)
        self.client.post("/api/check", {"plan": bad})
        self.client.post("/api/check", {"plan": plan()})
        self.assertEqual(snapshot(SAMPLES), before_samples)
        self.assertEqual(snapshot(self.job_root), before_jobs)

    def test_unknown_route_is_404(self):
        self.assertEqual(self.client.get("/nope")[0], 404)
        self.assertEqual(self.client.post("/api/nope", {"plan": plan()})[0], 404)

    def test_other_methods_are_405(self):
        self.assertEqual(self.client.request("PUT", "/api/check", raw=b"{}")[0], 405)


class RefusalTests(ServerCase):
    def test_a_foreign_host_is_refused(self):
        status, body = self.client.get("/api/catalog", host="evil.example.com")
        self.assertEqual(status, 403)
        self.assertIn("127.0.0.1", body["error"])
        self.assertEqual(self.client.post("/api/check", {"plan": plan()}, host="evil.example.com")[0], 403)

    def test_a_form_or_text_post_is_415_even_with_json_inside(self):
        # A cross-site form can only send a form-type body: it must not be read as JSON.
        body = json.dumps({"plan": plan()}).encode("utf-8")
        for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            with self.subTest(ctype=ctype):
                status, _ = self.client.request("POST", "/api/render", raw=body, extra={"Content-Type": ctype})
                self.assertEqual(status, 415)
        self.assertEqual(self.client.get("/api/render/status")[1]["state"], "idle")

    def test_a_cross_origin_post_is_refused(self):
        status, body = self.client.post("/api/render", {"plan": plan()},
                                        extra={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("cross-origin", body["error"])
        self.assertEqual(self.client.get("/api/render/status")[1]["state"], "idle")
        self.assertEqual(self.client.get("/api/catalog", extra={"Origin": "null"})[0], 403)

    def test_a_same_origin_post_is_allowed(self):
        origin = {"Origin": "http://127.0.0.1:%d" % self.port}
        self.assertEqual(self.client.post("/api/check", {"plan": plan()}, extra=origin)[0], 200)
        self.assertEqual(self.client.post("/api/check", {"plan": plan()},
                                          extra={"Origin": "http://localhost:%d" % self.port})[0], 200)

    def test_localhost_and_127_are_allowed_with_or_without_port(self):
        self.assertEqual(self.client.get("/api/catalog", host="localhost:%d" % self.port)[0], 200)
        self.assertEqual(self.client.get("/api/catalog", host="localhost")[0], 200)
        self.assertEqual(self.client.get("/api/catalog", host="127.0.0.1")[0], 200)

    def test_bad_sample_names_are_refused(self):
        self.assertEqual(self.client.get("/api/plan?name=../shotplan")[0], 400)
        self.assertEqual(self.client.get("/api/plan?name=..%2Fsecret")[0], 400)
        self.assertEqual(self.client.get("/api/plan?name=House")[0], 400)
        self.assertEqual(self.client.get("/api/plan?name=")[0], 400)

    def test_unknown_sample_is_404(self):
        self.assertEqual(self.client.get("/api/plan?name=nosuch")[0], 404)

    def test_traversal_in_the_gif_route_is_404(self):
        self.assertEqual(self.client.get("/renders/../../shotplan/shotplan.py/preview.gif")[0], 404)
        self.assertEqual(self.client.get("/renders/%2e%2e/preview.gif")[0], 404)
        self.assertEqual(self.client.get("/renders/" + "0" * 32 + "/preview.gif")[0], 404)

    def test_an_oversize_body_is_413(self):
        # Only the headers are sent, declaring a body over the cap: the server must refuse
        # from the length alone, without waiting for the body.
        import socket

        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(("POST /api/check HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                          "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                          % (self.port, web.MAX_BODY + 1)).encode("ascii"))
            status_line = sock.recv(64).split(b"\r\n", 1)[0]
        self.assertIn(b" 413 ", status_line, status_line)

    def test_a_body_that_is_not_json_is_400(self):
        self.assertEqual(self.client.request("POST", "/api/check", raw=b"not json")[0], 400)

    def test_a_body_without_a_plan_object_is_400(self):
        self.assertEqual(self.client.post("/api/check", {"plan": "nope"})[0], 400)
        self.assertEqual(self.client.request("POST", "/api/check", raw=b"[1, 2]")[0], 400)

    def test_a_plan_naming_files_outside_the_samples_is_400(self):
        escape = plan(shots=[{"id": "a", "image": "placeholder:a", "sequence": "../../secret.json",
                              "duration": 1, "transition": "cut"}])
        status, body = self.client.post("/api/check", {"plan": escape})
        self.assertEqual(status, 400)
        self.assertIn("samples folder", body["error"])
        absolute = plan(shots=[{"id": "a", "image": "/etc/passwd", "sequence": {"segments": [
            {"preset": "dolly_in", "duration_s": 1}]}, "duration": 1, "transition": "cut"}])
        self.assertEqual(self.client.post("/api/check", {"plan": absolute})[0], 400)

    def test_render_refuses_a_plan_with_errors(self):
        bad = plan()
        bad["shots"][0]["duration"] = 2  # the sequence still lasts 1 s: a duration mismatch
        status, body = self.client.post("/api/render", {"plan": bad})
        self.assertEqual(status, 400)
        self.assertIn("errors", body["error"])
        self.assertEqual(self.client.get("/api/render/status")[1]["state"], "idle")


class RenderJobTests(ServerCase):
    def test_start_status_done_and_the_gif(self):
        status, body = self.client.post("/api/render", {"plan": plan()})
        self.assertEqual(status, 202)
        job = body["job"]
        self.assertRegex(job, r"^[0-9a-f]{32}$")
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["gif"], "/renders/%s/preview.gif" % job)
        status, gif = self.client.get(done["gif"])
        self.assertEqual(status, 200)
        self.assertEqual(gif, GIF_BYTES)

    def test_a_failed_render_reports_its_message(self):
        self.studio.renderer = fake_renderer(fail="boom: no depth model")
        self.assertEqual(self.client.post("/api/render", {"plan": plan()})[0], 202)
        done = self.client.wait_done()
        self.assertEqual(done["state"], "error")
        self.assertEqual(done["message"], "boom: no depth model")
        self.assertIsNone(done["gif"])
        self.assertEqual(self.client.get("/renders/" + "0" * 32 + "/preview.gif")[0], 404)

    def test_a_second_render_while_one_runs_is_409(self):
        gate = threading.Event()
        self.studio.renderer = fake_renderer(gate=gate)
        self.assertEqual(self.client.post("/api/render", {"plan": plan()})[0], 202)
        self.assertEqual(self.client.get("/api/render/status")[1]["state"], "running")
        status, body = self.client.post("/api/render", {"plan": plan()})
        self.assertEqual(status, 409)
        self.assertIn("already running", body["error"])
        gate.set()
        self.assertEqual(self.client.wait_done()["state"], "done")
        self.studio.renderer = fake_renderer()
        self.assertEqual(self.client.post("/api/render", {"plan": plan()})[0], 202)
        self.assertEqual(self.client.wait_done()["state"], "done")

    def test_the_gif_is_only_served_for_the_finished_job(self):
        gate = threading.Event()
        self.studio.renderer = fake_renderer(gate=gate)
        job = self.client.post("/api/render", {"plan": plan()})[1]["job"]
        self.assertEqual(self.client.get("/renders/%s/preview.gif" % job)[0], 404)  # still running
        gate.set()
        self.client.wait_done()
        self.assertEqual(self.client.get("/renders/%s/preview.gif" % job)[0], 200)

    def test_the_render_gets_only_staged_files(self):
        seen = {}

        def spy(plan_path, outdir):
            seen["dir"] = os.path.dirname(plan_path)
            seen["files"] = sorted(os.listdir(seen["dir"]))
            return fake_renderer()(plan_path, outdir)

        self.studio.renderer = spy
        self.client.post("/api/render", {"plan": plan()})
        self.client.wait_done()
        self.assertEqual(seen["files"], ["plan.json"])


class RealRenderTests(unittest.TestCase):
    def test_a_real_render_runs_under_the_servers_own_python(self):
        job_root = tempfile.mkdtemp()
        try:
            studio = web.Studio(job_root=job_root)  # the default renderer: a subprocess on sys.executable
            httpd = web.make_server(studio, 0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            real_run = subprocess.run
            commands = []

            def recording_run(cmd, *args, **kwargs):
                commands.append(cmd)
                return real_run(cmd, *args, **kwargs)

            try:
                client = Client(httpd.server_address[1])
                with mock.patch.object(web.subprocess, "run", side_effect=recording_run):
                    self.assertEqual(client.post("/api/render", {"plan": plan(width=64, height=36)})[0], 202)
                    done = client.wait_done(timeout=120)
                self.assertEqual(done["state"], "done", done.get("message"))
                self.assertEqual(commands[0][0], sys.executable)
                status, gif = client.get(done["gif"])
                self.assertEqual(status, 200)
                self.assertTrue(gif.startswith(b"GIF8"))
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            shutil.rmtree(job_root, ignore_errors=True)


class ServeTests(unittest.TestCase):
    def test_a_port_in_use_is_one_clear_error(self):
        holder = web.make_server(web.Studio(renderer=fake_renderer(), job_root=tempfile.mkdtemp()), 0)
        try:
            port = holder.server_address[1]
            import io
            import contextlib
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = web.serve(port, studio=web.Studio(renderer=fake_renderer(), job_root=tempfile.mkdtemp()))
            self.assertEqual(code, 1)
            self.assertIn("already in use", err.getvalue())
        finally:
            holder.server_close()

    def test_the_server_binds_only_to_loopback_and_skips_getfqdn(self):
        import socket
        from unittest import mock

        def no_lookup(*args, **kwargs):
            raise AssertionError("socket.getfqdn was called while binding")

        with mock.patch.object(socket, "getfqdn", side_effect=no_lookup):
            httpd = web.make_server(web.Studio(renderer=fake_renderer(), job_root=tempfile.mkdtemp()), 0)
        try:
            self.assertEqual(httpd.server_address[0], "127.0.0.1")
            self.assertFalse(httpd.allow_reuse_address)
        finally:
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
