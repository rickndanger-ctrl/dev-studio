import http.client
import json
import threading
import unittest
import xml.etree.ElementTree as ET

from camera_motion.export.formats import parse_re10k
from camera_motion.web.server import Viewer, make_server

STUB_HELP = {
    "dolly_in": {"when": "Move toward the subject.", "feel": "Draws the viewer in."},
    "crane_up": {"when": "Reveal the scene from above.", "feel": "Rising and open."},
}

APP = b"<!doctype html><title>app</title>"
LIVE = b"<!doctype html><title>live</title>"
FRAMES_3S = 73  # 3 s at 24 fps, inclusive
HTML = "text/html; charset=utf-8"
JSON = "application/json"


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.viewer = Viewer(app_html=APP, index_html=LIVE, help_data=STUB_HELP)
        cls.httpd = make_server(cls.viewer, port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def get(self, raw_path):
        """Raw request: the path is sent exactly as given, with no normalisation."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", raw_path)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.getheader("Content-Disposition"), resp.read()
        finally:
            conn.close()

    def assert_error(self, raw_path, status):
        got, ctype, _, body = self.get(raw_path)
        self.assertEqual(got, status, raw_path)
        self.assertEqual(ctype, JSON, raw_path)
        self.assertIn("error", json.loads(body), raw_path)

    # --- routes: status and exact content type ---------------------------------

    def test_root_serves_app(self):
        status, ctype, _, body = self.get("/")
        self.assertEqual((status, ctype), (200, HTML))
        self.assertEqual(body, APP)

    def test_live_page_serves_index(self):
        status, ctype, _, body = self.get("/live.html")
        self.assertEqual((status, ctype), (200, HTML))
        self.assertEqual(body, LIVE)

    def test_frames_json(self):
        status, ctype, _, body = self.get("/frames.json")
        self.assertEqual((status, ctype), (200, JSON))
        self.assertEqual(len(json.loads(body)), 14)

    def test_presets_list(self):
        status, ctype, _, body = self.get("/api/presets")
        self.assertEqual((status, ctype), (200, JSON))
        presets = json.loads(body)
        self.assertEqual(len(presets), 14)
        self.assertIn("dolly_in", {p["id"] for p in presets})

    def test_presets_carry_help(self):
        presets = {p["id"]: p for p in json.loads(self.get("/api/presets")[3])}
        self.assertEqual(presets["dolly_in"]["help"], STUB_HELP["dolly_in"])
        self.assertEqual(presets["crane_up"]["help"], STUB_HELP["crane_up"])
        others = [p for pid, p in presets.items() if pid not in STUB_HELP]
        self.assertTrue(others)
        self.assertTrue(all(p["help"] == {} for p in others))

    def test_api_path(self):
        status, ctype, _, body = self.get("/api/path/dolly_in")
        self.assertEqual((status, ctype), (200, JSON))
        data = json.loads(body)
        self.assertEqual(data["id"], "dolly_in")
        self.assertEqual(data["fps"], 24)
        self.assertEqual(len(data["frames"]), FRAMES_3S)

    def test_anim_svg(self):
        status, ctype, _, body = self.get("/anim/dolly_in.svg")
        self.assertEqual((status, ctype), (200, "image/svg+xml"))
        self.assertEqual(ET.fromstring(body).tag.split("}")[-1], "svg")

    def test_download_json(self):
        status, ctype, disposition, body = self.get("/download/dolly_in.json")
        self.assertEqual((status, ctype), (200, JSON))
        self.assertEqual(disposition, "attachment; filename=dolly_in.json")
        self.assertEqual(len(json.loads(body)["frames"]), FRAMES_3S)

    def test_download_re10k(self):
        status, ctype, disposition, body = self.get("/download/dolly_in.re10k")
        self.assertEqual((status, ctype), (200, "text/plain; charset=utf-8"))
        self.assertEqual(disposition, "attachment; filename=dolly_in.txt")
        self.assertEqual(len(parse_re10k(body.decode("utf-8").splitlines())), FRAMES_3S)

    def test_download_py(self):
        status, ctype, disposition, body = self.get("/download/dolly_in.py")
        self.assertEqual((status, ctype), (200, "text/x-python; charset=utf-8"))
        self.assertEqual(disposition, "attachment; filename=dolly_in.py")
        compile(body.decode("utf-8"), "dolly_in.py", "exec")

    def test_download_gltf(self):
        status, ctype, disposition, body = self.get("/download/dolly_in.gltf")
        self.assertEqual((status, ctype), (200, "model/gltf+json"))
        self.assertEqual(disposition, "attachment; filename=dolly_in.gltf")
        self.assertTrue(json.loads(body)["animations"])

    # --- overrides ----------------------------------------------------------------

    def test_override_changes_the_path(self):
        default = json.loads(self.get("/api/path/dolly_in")[3])["frames"]
        longer = json.loads(self.get("/api/path/dolly_in?distance=2")[3])["frames"]
        self.assertNotEqual(default[-1]["position"], longer[-1]["position"])
        self.assertNotEqual(default[-1]["position"][2], longer[-1]["position"][2])

    def test_seconds_and_fps_override_frame_count(self):
        data = json.loads(self.get("/api/path/dolly_in?seconds=1&fps=10")[3])
        self.assertEqual(data["fps"], 10)
        self.assertEqual(len(data["frames"]), 11)

    # --- 404 and 400 -----------------------------------------------------------------

    def test_unknown_preset_is_404(self):
        for route in ("/api/path/nope", "/anim/nope.svg", "/download/nope.json"):
            with self.subTest(route=route):
                self.assert_error(route, 404)

    def test_unknown_route_is_404(self):
        for route in ("/nope", "/api/path/dolly_in/extra", "/anim/dolly_in.png"):
            with self.subTest(route=route):
                self.assert_error(route, 404)

    def test_bad_override_values_are_400(self):
        bad = [
            "distance=abc", "distance=inf", "distance=nan", "distance=",
            "radius=0", "radius=-1",
            "fov_end=0", "fov_end=180",
            "seconds=0.1", "seconds=20",
            "fps=0", "fps=61", "fps=1.5", "fps=abc",
            "axis=y", "easing=bounce",
            "foo=1",
            "distance=1&distance=2",
        ]
        for query in bad:
            with self.subTest(query=query):
                self.assert_error("/api/path/dolly_in?" + query, 400)
                self.assert_error("/anim/dolly_in.svg?" + query, 400)

    def test_unknown_download_format_is_400(self):
        self.assert_error("/download/dolly_in.exe", 400)

    # --- path traversal: ids come only from the catalog -------------------------------

    def test_traversal_attempts_are_404(self):
        for route in (
            "/api/path/..%2Fpresets%2Fcatalog",
            "/api/path/../../etc/passwd",
            "/anim/..%2F..%2Fsecret.svg",
            "/download/..%2F..%2Fsecret.json",
            "/download/../../etc/passwd.json",
        ):
            with self.subTest(route=route):
                self.assert_error(route, 404)

    def test_binds_to_loopback_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
