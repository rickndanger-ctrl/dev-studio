"""Tests that viewer/server.py and bin/studio-snap.py take owner-specific values from the config. Fake names only.

Run from the repo root:  python3 -m unittest test.test_viewer_config
"""
import importlib.machinery, importlib.util, json, os, sys, tempfile, unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
TMP = tempfile.mkdtemp(prefix="viewer-config-")


def write_config(name, extra):
    path = os.path.join(TMP, name + ".json")
    base = {"state_dir": os.path.join(TMP, "state-" + name),
            "machines": [{"id": "hub1", "label": "Home Base", "role": "hub", "ssh": []},
                         {"id": "box1", "label": "Box One", "role": "studio", "ssh": ["host-a"], "project": "~/Studios/one"},
                         {"id": "box2", "label": "Box Two", "role": "studio", "enabled": False, "ssh": []}]}
    base.update(extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(base, f)
    return path


def load_server(config):
    spec = importlib.util.spec_from_file_location("viewer_server_cfg_" + os.path.basename(config), os.path.join(REPO, "viewer", "server.py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"RE_STUDIO_CONFIG": config}), mock.patch.object(sys, "argv", ["server.py"]):
        spec.loader.exec_module(module)
    return module


class Defaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server(write_config("plain", {}))

    def test_generic_labels_and_no_audit_panel_without_config(self):
        s = self.server
        self.assertEqual(s.LABELS["lap.model"], "Local model")
        self.assertIsNone(s.AUDIT)
        self.assertIsNone(s.DATA)
        self.assertIn("for n in ''; do", s.mini_script("box1"))
        self.assertIsNone(s.state["audit"])

    def test_layout_follows_config_order_hub_first(self):
        self.assertEqual([m["id"] for m in self.server.state["layout"]], ["hub1", "box1", "box2"])
        self.assertEqual(self.server.HUB_LABEL, "Home Base")

    def test_probe_script_uses_default_path_and_the_machines_own_project_first(self):
        script = self.server.mini_script("box1")
        self.assertIn("export PATH=$HOME/.local/bin:/opt/homebrew/bin:$PATH", script)
        self.assertIn('["~/Studios/one", "~/DevStudio"]', script)
        self.assertNotIn("@", script.replace("@@", ""))   # every placeholder was filled


class Configured(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = load_server(write_config("custom", {
            "labels": {"lap.model": "Fake Model 9", "lap.haiku": "Checker"},
            "studio_dirs": ["~/Work/studio"], "studio_folder_names": ["Work", "w-1"],
            "remote_path": "/fake/bin:$HOME/bin",
            "audit": {"app": "Fake App", "data_dir": "~/fake-data", "mac_data_dir": "~/fake-mac-data", "process": ["FakeApp", "fake cli"]}}))

    def test_labels_merge_over_generic_names(self):
        self.assertEqual(self.server.LABELS["lap.model"], "Fake Model 9")
        self.assertEqual(self.server.LABELS["lap.member"], "Member")
        self.assertEqual(self.server.state["labels"]["lap.haiku"], "Checker")

    def test_studio_values_reach_the_probe_script_and_the_cwd_filter(self):
        script = self.server.mini_script("box1")
        self.assertIn("export PATH=/fake/bin:$HOME/bin:$PATH", script)
        self.assertIn('["~/Studios/one", "~/Work/studio"]', script)
        self.assertEqual(self.server.STUDIO_FOLDER_NAMES, {"Work", "w-1"})

    def test_audit_block_with_a_data_folder_shows_the_panel(self):
        self.assertEqual(self.server.state["audit"]["app"], "Fake App")
        self.assertEqual(self.server.DATA, os.path.expanduser("~/fake-data"))

    def test_app_data_folder_and_processes_reach_the_probe_script(self):
        script = self.server.mini_script("box1")
        self.assertIn('os.path.expanduser("~/fake-mac-data")', script)
        self.assertIn("for n in FakeApp 'fake cli'; do", script)

    def test_audit_block_without_a_data_folder_hides_it(self):
        self.assertIsNone(load_server(write_config("half", {"audit": {"app": "Fake App"}})).AUDIT)


class StudioSnap(unittest.TestCase):
    def test_active_log_searches_the_given_dirs(self):
        loader = importlib.machinery.SourceFileLoader("studio_snap_cfg", os.path.join(REPO, "bin", "studio-snap.py"))
        spec = importlib.util.spec_from_loader("studio_snap_cfg", loader)
        snap = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "argv", ["studio-snap.py"]):
            loader.exec_module(snap)
        log = os.path.join(TMP, "somewhere", ".studio", "router.log")
        os.makedirs(os.path.dirname(log))
        with open(log, "w") as f:
            f.write(json.dumps({"kind": "route", "t": 5, "text": "hi"}) + "\n")
        with mock.patch.dict(os.environ, {"HOME": os.path.join(TMP, "emptyhome")}):
            self.assertEqual(snap.active_log(False, [os.path.join(TMP, "somewhere")]), log)
            self.assertEqual(snap.active_log(False, [os.path.join(TMP, "nowhere")]), "")

    def test_set_path_expands_variables(self):
        loader = importlib.machinery.SourceFileLoader("studio_snap_cfg2", os.path.join(REPO, "bin", "studio-snap.py"))
        spec = importlib.util.spec_from_loader("studio_snap_cfg2", loader)
        snap = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "argv", ["studio-snap.py"]):
            loader.exec_module(snap)
        with mock.patch.dict(os.environ, {"HOME": "/fake/home", "PATH": "/usr/bin"}):
            snap.set_path("$HOME/x:/opt/y")
            self.assertEqual(os.environ["PATH"], "/fake/home/x:/opt/y:/usr/bin")


if __name__ == "__main__":
    unittest.main()
