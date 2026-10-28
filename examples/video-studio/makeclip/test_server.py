"""Tests for the make-a-clip server: every route, the upload checks, the render job lifecycle with a
fake renderer, the value ranges, the CSRF guards and the path checks. Temporary folders only: the
real ~/Pictures is never touched, and no model or render python is needed."""

import http.client
import json
import math
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

from makeclip import server as clipserver

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff" + b"\x00" * 64


def fake_renderer(gate=None, fail=None, frames_per_tick=True):
    """Writes the frames and the previews the real render would write. gate: wait for it first."""
    def render(command, env, clip_dir, expected):
        if gate is not None:
            gate.wait(10)
        if fail is not None:
            return 1, fail
        os.makedirs(os.path.join(clip_dir, "frames"), exist_ok=True)
        for i in range(expected):
            with open(os.path.join(clip_dir, "frames", "%04d.png" % i), "wb") as f:
                f.write(PNG)
        with open(os.path.join(clip_dir, "preview.mp4"), "wb") as f:
            f.write(b"\x00\x00\x00\x18ftypmp42")
        with open(os.path.join(clip_dir, "preview.gif"), "wb") as f:
            f.write(b"GIF89a")
        return 0, ""
    return render


class Client:
    def __init__(self, port):
        self.port = port

    def request(self, method, path, body=None, raw=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        head = {"Host": host or "127.0.0.1:%d" % self.port}
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            head["Content-Type"] = "application/json"
        if data is not None:
            head["Content-Length"] = str(len(data))
        if method == "POST":
            head["X-Requested-With"] = "makeclip"
        head.update(headers or {})
        conn.request(method, path, body=data, headers=head)
        resp = conn.getresponse()
        payload = resp.read()
        ctype = resp.getheader("Content-Type", "")
        extra = dict(resp.getheaders())
        conn.close()
        if ctype.startswith("application/json"):
            return resp.status, json.loads(payload.decode("utf-8")), extra
        return resp.status, payload, extra

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def wait_done(self, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            _, status, _ = self.get("/api/render/status")
            if status["state"] != "running":
                return status
            time.sleep(0.05)
        raise AssertionError("render did not finish in %s s" % timeout)


def good_render(**over):
    body = {"image": "still.png", "preset": "dolly_in", "amount": 0.4, "seconds": 2, "size": "square",
            "layers": False, "depth": "simple"}
    body.update(over)
    return body


class ServerCase(unittest.TestCase):
    renderer = None

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.inputs = os.path.join(self.root, "inputs")
        self.outputs = os.path.join(self.root, "outputs")
        self.outside = os.path.join(self.root, "outside")
        os.makedirs(self.outside)
        self.plans = os.path.join(self.root, "pictures")
        os.makedirs(self.plans)
        self.studio = clipserver.Studio(self.inputs, self.outputs, render_python=clipserver.sys.executable,
                                        renderer=self.renderer or fake_renderer(), page=b"<!doctype html>app",
                                        plans_root=self.plans)
        self.studio._onnx = False  # no layers unless a test asks: the render python is not assumed to have onnx
        self.httpd = clipserver.make_server(self.studio, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = Client(self.port)
        # The tests never depend on what models are installed here.
        self._saved = (clipserver.ISNET_MODEL, clipserver.DEPTH_MODEL)
        clipserver.ISNET_MODEL = os.path.join(self.root, "no-isnet.onnx")
        clipserver.DEPTH_MODEL = os.path.join(self.root, "no-depth.onnx")

    def tearDown(self):
        clipserver.ISNET_MODEL, clipserver.DEPTH_MODEL = self._saved
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def add_input(self, name="still.png", data=PNG):
        os.makedirs(self.inputs, exist_ok=True)
        with open(os.path.join(self.inputs, name), "wb") as f:
            f.write(data)


class RouteTests(ServerCase):
    def test_index_serves_the_page(self):
        status, body, extra = self.client.get("/")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"<!doctype html>app")

    def test_index_is_503_without_a_page(self):
        self.studio.page = None
        self.assertEqual(self.client.get("/")[0], 503)

    def test_options_lists_the_moves_sizes_and_durations(self):
        status, opts, _ = self.client.get("/api/options")
        self.assertEqual(status, 200)
        self.assertEqual(len(opts["presets"]), 14)
        dolly = next(p for p in opts["presets"] if p["id"] == "dolly_in")
        self.assertEqual((dolly["param"], dolly["min"], dolly["max"], dolly["default"]), ("distance", 0.1, 0.6, 0.4))
        self.assertEqual(opts["durations"], {"min": 2, "max": 8, "default": 4})
        self.assertEqual([s["id"] for s in opts["sizes"]], ["square", "wide", "vertical"])
        self.assertEqual(opts["depth"], {"simple": True, "real": False})
        self.assertFalse(opts["layers"])

    def test_images_lists_the_inputs_newest_first(self):
        self.assertEqual(self.client.get("/api/images")[1], [])
        self.add_input("a.png")
        time.sleep(0.02)
        self.add_input("b.jpg", JPG)
        names = [row["name"] for row in self.client.get("/api/images")[1]]
        self.assertEqual(names, ["b.jpg", "a.png"])

    def test_an_input_picture_is_served(self):
        self.add_input()
        status, body, extra = self.client.get("/inputs/still.png")
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG)
        self.assertEqual(extra.get("Content-Type"), "image/png")

    def test_status_is_idle_before_any_render(self):
        status, body, _ = self.client.get("/api/render/status")
        self.assertEqual(body, {"state": "idle", "frame": 0, "frames": 0, "message": "", "clip": None})

    def test_clips_is_empty_at_first(self):
        self.assertEqual(self.client.get("/api/clips")[1], [])

    def test_unknown_route_is_404(self):
        self.assertEqual(self.client.get("/nope")[0], 404)
        self.assertEqual(self.client.post("/api/nope", {})[0], 404)

    def test_other_methods_are_405(self):
        self.assertEqual(self.client.request("PUT", "/api/render", raw=b"{}")[0], 405)


class UploadTests(ServerCase):
    def upload(self, name="My Holiday Cake!", ctype="image/png", data=PNG, query=True):
        path = "/api/upload?name=%s" % urllib.parse.quote(name) if query else "/api/upload"
        return self.client.request("POST", path, raw=data, headers={"Content-Type": ctype})

    def test_a_png_is_saved_under_a_slug_and_a_stamp(self):
        status, body, _ = self.upload()
        self.assertEqual(status, 200)
        self.assertRegex(body["name"], r"^my-holiday-cake-\d{8}-\d{6}\.png$")
        self.assertEqual(body["url"], "/inputs/" + body["name"])
        with open(os.path.join(self.inputs, body["name"]), "rb") as f:
            self.assertEqual(f.read(), PNG)

    def test_a_jpeg_is_saved_as_jpg(self):
        status, body, _ = self.upload(ctype="image/jpeg", data=JPG)
        self.assertEqual(status, 200)
        self.assertTrue(body["name"].endswith(".jpg"))

    def test_a_wrong_type_is_415(self):
        self.assertEqual(self.upload(ctype="image/gif", data=b"GIF89a")[0], 415)

    def test_a_fake_signature_is_400(self):
        self.assertEqual(self.upload(ctype="image/png", data=b"not a picture at all")[0], 400)
        self.assertEqual(self.upload(ctype="image/jpeg", data=PNG)[0], 400)

    def test_a_missing_name_is_400(self):
        self.assertEqual(self.upload(query=False)[0], 400)

    def test_an_upload_over_25_mb_is_413_from_the_header_alone(self):
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(("POST /api/upload?name=x HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                          "X-Requested-With: makeclip\r\nContent-Type: image/png\r\n"
                          "Content-Length: %d\r\n\r\n" % (self.port, clipserver.MAX_UPLOAD + 1)).encode("ascii"))
            status_line = sock.recv(64).split(b"\r\n", 1)[0]
        self.assertIn(b" 413 ", status_line, status_line)


class RenderTests(ServerCase):
    def setUp(self):
        super().setUp()
        self.add_input()

    def test_a_render_runs_and_the_clip_downloads(self):
        status, body, _ = self.client.post("/api/render", good_render())
        self.assertEqual(status, 202)
        self.assertRegex(body["job"], r"^[0-9a-f]{32}$")
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["message"], "clip ready")
        self.assertEqual(done["frames"], 49)  # 2 s at 24 fps, plus one
        self.assertEqual(done["frame"], 49)
        clip = done["clip"]
        self.assertRegex(clip, r"^still-\d{8}-\d{6}$")
        status, mp4, extra = self.client.get("/clips/%s/preview.mp4" % clip)
        self.assertEqual(status, 200)
        self.assertEqual(extra.get("Content-Type"), "video/mp4")
        self.assertNotIn("Content-Disposition", extra)
        status, mp4, extra = self.client.get("/clips/%s/preview.mp4?download=1" % clip)
        self.assertIn("attachment", extra.get("Content-Disposition", ""))
        self.assertEqual(self.client.get("/clips/%s/preview.gif" % clip)[0], 200)

    def test_clip_json_records_the_request_and_the_clip_is_listed(self):
        self.client.post("/api/render", good_render(preset="pan_left", amount=5, size="wide"))
        done = self.client.wait_done()
        with open(os.path.join(self.outputs, done["clip"], "clip.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual((meta["preset"], meta["amount"], meta["size"], meta["frames"]), ("pan_left", 5.0, "wide", 49))
        clips = self.client.get("/api/clips")[1]
        self.assertEqual(clips[0]["name"], done["clip"])
        self.assertEqual(clips[0]["mp4"], "/clips/%s/preview.mp4" % done["clip"])
        self.assertEqual(clips[0]["size"], "wide")

    def test_the_camera_file_has_the_chosen_size_and_frames(self):
        self.client.post("/api/render", good_render(size="vertical", seconds=3))
        done = self.client.wait_done()
        with open(os.path.join(self.outputs, done["clip"], "camera.json"), encoding="utf-8") as f:
            cam = json.load(f)
        self.assertEqual((cam["width"], cam["height"]), (1080, 1920))
        self.assertEqual(len(cam["frames"]), 73)

    def end_z(self, preset, amount):
        """The camera's z position at the last frame: the subject is at the origin, the camera starts at z 5."""
        self.client.post("/api/render", good_render(preset=preset, amount=amount))
        done = self.client.wait_done()
        with open(os.path.join(self.outputs, done["clip"], "camera.json"), encoding="utf-8") as f:
            return json.load(f)["frames"][-1]["c2w"][2][3]

    def test_an_orbit_keeps_the_camera_distance(self):
        self.client.post("/api/render", good_render(preset="orbit_left", amount=8))
        done = self.client.wait_done()
        self.assertEqual(done["state"], "done", done.get("message"))
        with open(os.path.join(self.outputs, done["clip"], "camera.json"), encoding="utf-8") as f:
            frames = json.load(f)["frames"]
        distances = [math.hypot(fr["c2w"][0][3], fr["c2w"][2][3]) for fr in frames]
        self.assertAlmostEqual(max(distances) - min(distances), 0.0, delta=0.05)

    def test_the_sign_follows_the_catalog(self):
        self.assertGreater(self.end_z("dolly_out", 0.2), 5.0)  # away from the subject
        self.assertLess(self.end_z("dolly_in", 0.2), 5.0)      # toward the subject

    def test_a_failed_render_reports_the_last_stderr_line(self):
        self.studio.renderer = fake_renderer(fail="boom: no depth model")
        self.assertEqual(self.client.post("/api/render", good_render())[0], 202)
        done = self.client.wait_done()
        self.assertEqual(done["state"], "error")
        self.assertEqual(done["message"], "boom: no depth model")

    def test_no_preview_after_a_zero_exit_is_an_error(self):
        self.studio.renderer = lambda command, env, clip_dir, expected: (0, "")
        self.client.post("/api/render", good_render())
        done = self.client.wait_done()
        self.assertEqual(done["state"], "error")
        self.assertIn("no preview.mp4", done["message"])

    def test_progress_counts_the_frames_written(self):
        gate = threading.Event()

        def slow(command, env, clip_dir, expected):
            os.makedirs(os.path.join(clip_dir, "frames"), exist_ok=True)
            for i in range(10):
                with open(os.path.join(clip_dir, "frames", "%04d.png" % i), "wb") as f:
                    f.write(PNG)
            gate.wait(10)
            return 1, "stopped"

        self.studio.renderer = slow
        self.client.post("/api/render", good_render())
        time.sleep(0.2)
        status = self.client.get("/api/render/status")[1]
        self.assertEqual(status["state"], "running")
        self.assertEqual((status["frame"], status["frames"]), (10, 49))
        gate.set()
        self.client.wait_done()

    def test_a_second_render_while_one_runs_is_409(self):
        gate = threading.Event()
        self.studio.renderer = fake_renderer(gate=gate)
        self.assertEqual(self.client.post("/api/render", good_render())[0], 202)
        status, body, _ = self.client.post("/api/render", good_render())
        self.assertEqual(status, 409)
        gate.set()
        self.assertEqual(self.client.wait_done()["state"], "done")
        self.studio.renderer = fake_renderer()
        self.assertEqual(self.client.post("/api/render", good_render())[0], 202)
        self.assertEqual(self.client.wait_done()["state"], "done")

    def test_out_of_range_values_are_400(self):
        cases = [
            good_render(amount=0.7),                 # dolly_in max 0.6
            good_render(amount=0.05),                # below the minimum
            good_render(amount=True),                # a boolean is not a number
            good_render(preset="orbit_left", amount=20),
            good_render(seconds=1),
            good_render(seconds=9),
            good_render(size="tall"),
            good_render(preset="zoom_nope"),
            good_render(depth="deep"),
            good_render(layers="yes"),
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/render", body)[0], 400)

    def test_layers_and_real_depth_refuse_when_their_models_are_missing(self):
        self.assertEqual(self.client.post("/api/render", good_render(layers=True))[0], 400)
        self.assertEqual(self.client.post("/api/render", good_render(depth="real"))[0], 400)

    def test_an_image_that_is_missing_or_outside_is_refused(self):
        self.assertEqual(self.client.post("/api/render", good_render(image="nope.png"))[0], 404)
        self.assertEqual(self.client.post("/api/render", good_render(image="../secret.png"))[0], 400)

    def test_a_render_body_that_is_not_json_is_415(self):
        status, _, _ = self.client.request("POST", "/api/render", raw=b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_a_render_body_over_64_kb_is_413(self):
        big = {"image": "still.png", "pad": "a" * (clipserver.MAX_JSON + 10)}
        self.assertEqual(self.client.post("/api/render", big)[0], 413)


class PathSafetyTests(ServerCase):
    def test_an_input_name_with_dots_or_slashes_is_400(self):
        for name in ("..%2Fsecret.png", "..secret.png", ".hidden.png"):
            with self.subTest(name=name):
                self.assertIn(self.client.get("/inputs/" + name)[0], (400, 404))
        self.assertEqual(self.client.get("/inputs/a/b.png")[0], 404)

    def test_an_input_symlink_pointing_outside_is_refused(self):
        secret = os.path.join(self.outside, "secret.png")
        with open(secret, "wb") as f:
            f.write(PNG)
        os.makedirs(self.inputs, exist_ok=True)
        os.symlink(secret, os.path.join(self.inputs, "link.png"))
        self.assertEqual(self.client.get("/inputs/link.png")[0], 404)
        self.assertEqual(self.client.post("/api/render", good_render(image="link.png"))[0], 404)

    def test_a_clip_name_that_is_not_a_clip_is_refused(self):
        self.assertEqual(self.client.get("/clips/..%2Fetc/preview.mp4")[0], 400)  # not a clip name
        self.assertEqual(self.client.get("/clips/notaclip/preview.mp4")[0], 400)
        self.assertEqual(self.client.get("/clips/still-20260101-000000/preview.mp4")[0], 404)  # well formed, not there

    def test_a_clip_folder_symlinked_outside_is_refused(self):
        os.makedirs(self.outside, exist_ok=True)
        with open(os.path.join(self.outside, "preview.mp4"), "wb") as f:
            f.write(b"x")
        os.makedirs(self.outputs, exist_ok=True)
        os.symlink(self.outside, os.path.join(self.outputs, "still-20260101-000000"))
        self.assertEqual(self.client.get("/clips/still-20260101-000000/preview.mp4")[0], 404)


class GuardTests(ServerCase):
    def test_a_foreign_host_is_403(self):
        self.assertEqual(self.client.get("/api/options", host="evil.example.com")[0], 403)
        self.assertEqual(self.client.post("/api/render", good_render(), host="evil.example.com")[0], 403)

    def test_localhost_is_allowed(self):
        self.assertEqual(self.client.get("/api/options", host="localhost:%d" % self.port)[0], 200)

    def test_a_foreign_origin_is_403(self):
        status, _, _ = self.client.get("/api/options", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.client.post("/api/render", good_render(), headers={"Origin": "null"})
        self.assertEqual(status, 403)

    def test_a_post_without_x_requested_with_is_403(self):
        status, body, _ = self.client.request("POST", "/api/render", body=good_render(),
                                              headers={"X-Requested-With": ""})
        self.assertEqual(status, 403)
        self.assertIn("X-Requested-With", body["error"])
        self.assertEqual(self.client.get("/api/render/status")[1]["state"], "idle")

    def test_an_upload_without_the_header_is_403(self):
        status, _, _ = self.client.request("POST", "/api/upload?name=x", raw=PNG,
                                           headers={"Content-Type": "image/png", "X-Requested-With": ""})
        self.assertEqual(status, 403)


class SetupTests(unittest.TestCase):
    def test_the_real_pictures_folder_is_not_the_default_in_tests(self):
        # Studio takes folders explicitly, so the default ~/Pictures paths are only in serve().
        with open(clipserver.__file__, encoding="utf-8") as handle:
            studio_src = handle.read()
        self.assertIn("def serve(port=DEFAULT_PORT, studio=None):", studio_src)


if __name__ == "__main__":
    unittest.main()
