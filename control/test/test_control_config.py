"""Config-driven control tools: the audit block, remote_path and studio_dirs (lib/re_studio_config.py), bin/re-audit,
bin/mini-room.py, bin/dispatch and bin/studio-watch.

Every fixture is generic (example app, example project, example folders and hosts). Nothing here opens a socket or
runs ssh: a Mini's Room is a fixture folder, dispatch runs with DISPATCH_SHOW=1 (it prints the remote script), and
studio-watch is only asked for its probe and its targets.

Run from the repo root:  python3 -m unittest discover -s test -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import io
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "lib"))
import re_studio_config as cfg  # noqa: E402

BIN = os.path.join(REPO, "bin")
MACHINES = [
    {"id": "hub-1", "label": "Hub", "role": "hub", "ssh": [], "project": "~/Work/example-studio"},
    {"id": "studio-a", "label": "Studio A", "role": "studio", "ssh": ["studio-a.example"],
     "project": "~/Work/example-studio", "architect": "example-architect"},
    {"id": "studio-b", "label": "Studio B", "role": "studio", "enabled": False, "ssh": [], "project": "~/Other"},
]
AUDIT = {"app": "Example App", "data_dir": "~/example-app-data", "mac_data_dir": "~/Library/Example App",
         "project": "Example Project", "process": ["zz-example-app-not-running"]}


def load_module(name, rel):
    """Import a file from bin/ that has no .py suffix (re-audit, dispatch, studio-watch)."""
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(REPO, rel))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ConfigCase(unittest.TestCase):
    """Each test writes its own machines.json in a temp folder and points RE_STUDIO_CONFIG at it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="control-config-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = os.environ.get("RE_STUDIO_CONFIG")
        self.addCleanup(self.restore_env)
        self.state = os.path.join(self.tmp, "state")

    def restore_env(self):
        if self.saved is None:
            os.environ.pop("RE_STUDIO_CONFIG", None)
        else:
            os.environ["RE_STUDIO_CONFIG"] = self.saved

    def write_config(self, **extra):
        """extra keys are added; a key given as None is left out of the file (e.g. herdr_session=None)."""
        conf = {"herdr_session": "example-studio", "state_dir": self.state, "machines": MACHINES}
        conf.update(extra)
        conf = {k: v for k, v in conf.items() if v is not None}
        path = os.path.join(self.tmp, "machines.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conf, f)
        os.environ["RE_STUDIO_CONFIG"] = path
        return path

    def run_python(self, rel, *args):
        return subprocess.run([sys.executable, os.path.join(REPO, rel), *args], capture_output=True, text=True, timeout=60)


class AuditBlockTest(ConfigCase):
    def test_no_audit_block_means_no_target(self):
        self.write_config()
        self.assertIsNone(cfg.audit())

    def test_audit_needs_a_data_folder_and_a_project(self):
        self.write_config(audit={"app": "Example App", "project": "Example Project"})
        self.assertIsNone(cfg.audit())
        self.write_config(audit={"app": "Example App", "data_dir": "~/x"})
        self.assertIsNone(cfg.audit())

    def test_audit_returns_the_block(self):
        self.write_config(audit=AUDIT)
        self.assertEqual(cfg.audit(), AUDIT)


class RemotePathAndStudioDirsTest(ConfigCase):
    def test_defaults_when_the_keys_are_missing(self):
        self.write_config()
        self.assertEqual(cfg.remote_path(), "$HOME/.local/bin:/opt/homebrew/bin")
        self.assertEqual(cfg.studio_dirs(), ["~/DevStudio"])

    def test_configured_values_are_used(self):
        self.write_config(remote_path="$HOME/bin:/usr/local/bin", studio_dirs=["~/Labs", "/srv/studio"])
        self.assertEqual(cfg.remote_path(), "$HOME/bin:/usr/local/bin")
        self.assertEqual(cfg.studio_dirs(), ["~/Labs", "/srv/studio"])

    def test_a_machines_own_project_is_tried_first_without_repeats(self):
        self.write_config(studio_dirs=["~/Work/example-studio", "~/Labs"])
        self.assertEqual(cfg.studio_dirs("studio-a"), ["~/Work/example-studio", "~/Labs"])
        self.write_config(studio_dirs=["~/Labs"])
        self.assertEqual(cfg.studio_dirs("studio-a"), ["~/Work/example-studio", "~/Labs"])

    def test_cli_prints_remote_path_and_quoted_studio_dirs(self):
        self.write_config(remote_path="$HOME/bin", studio_dirs=["~/Labs"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cfg._main(["remote-path"]), 0)
            self.assertEqual(cfg._main(["studio-dirs", "studio-a"]), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "$HOME/bin")
        self.assertEqual(shlex.split(lines[1]), ["~/Work/example-studio", "~/Labs"])


class ReAuditTest(ConfigCase):
    def test_without_an_audit_block_it_says_so_and_exits_0(self):
        self.write_config()
        r = self.run_python("bin/re-audit")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "no audit target configured"), r.stderr)

    def load(self):
        self.write_config(audit=AUDIT)
        return load_module("re_audit_under_test", "bin/re-audit")

    def test_target_comes_from_the_audit_block(self):
        mod = self.load()
        self.assertEqual(mod.PROJECT, "Example Project")
        self.assertEqual(mod.APP, "Example App")
        self.assertEqual(mod.DATA, os.path.expanduser("~/example-app-data"))

    def test_mini_ids_are_the_enabled_studios_with_a_route(self):
        mod = self.load()
        self.assertEqual(mod.mini_ids(), ["studio-a"])
        self.assertEqual(mod.mini_label("studio-a"), "Studio A")

    def test_mini_room_sends_the_audit_target_as_its_argument(self):
        mod = self.load()
        sent = []
        with mock.patch.object(mod.cfg, "routes", side_effect=lambda mid, remote, timeout=6: sent.append(remote) or []):
            self.assertIsNone(mod.mini_room("studio-a"))
        argv = shlex.split(sent[0])
        self.assertEqual(argv[:2], ["python3", "-"])
        self.assertEqual(json.loads(argv[2]), {"app": "Example App", "mac_data_dir": "~/Library/Example App",
                                               "project": "Example Project", "process": ["zz-example-app-not-running"]})

    def test_app_running_is_unknown_without_patterns_and_false_for_a_missing_app(self):
        mod = self.load()
        self.assertIsNone(mod.app_running([]))
        if shutil.which("pgrep"):
            self.assertIs(mod.app_running(["zz-example-app-not-running"]), False)
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "zz-probe-token"])
            self.addCleanup(lambda: (child.kill(), child.wait()))
            self.assertIs(mod.app_running(["zz-probe-token"]), True)


class MiniRoomTest(ConfigCase):
    def setUp(self):
        super().setUp()
        self.mac = os.path.join(self.tmp, "mac-app")
        self.ws = os.path.join(self.tmp, "workspace")
        os.makedirs(os.path.join(self.mac, "rooms"))
        os.makedirs(os.path.join(self.ws, "findings"))
        with open(os.path.join(self.mac, "projects.json"), "w") as f:
            json.dump([{"id": "p1", "name": "Example Project", "workspace": self.ws}], f)
        with open(os.path.join(self.mac, "rooms", "p1.json"), "w") as f:
            json.dump({"tasks": [{"title": "Map the parser", "status": "running"}]}, f)
        with open(os.path.join(self.ws, "findings", "a.md"), "w") as f:
            f.write("found it\n")

    def run_room(self, target):
        return subprocess.run([sys.executable, os.path.join(BIN, "mini-room.py"), json.dumps(target)],
                              capture_output=True, text=True, timeout=60)

    def test_reads_the_room_it_is_given(self):
        r = self.run_room({"app": "Example App", "mac_data_dir": self.mac, "project": "Example Project"})
        out = json.loads(r.stdout)
        self.assertEqual([t["title"] for t in out["tasks"]], ["Map the parser"])
        self.assertIn("a.md", out["findings"])

    def test_no_target_argument_is_an_error_not_a_crash(self):
        r = subprocess.run([sys.executable, os.path.join(BIN, "mini-room.py")], capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(r.stdout)["error"], "no audit target given")

    def test_target_without_a_mac_folder_or_project_is_an_error(self):
        r = self.run_room({"app": "Example App", "project": "Example Project"})
        self.assertEqual(json.loads(r.stdout)["error"], "no audit target configured")

    def test_reports_whether_the_app_runs_on_the_mini(self):
        r = self.run_room({"app": "Example App", "mac_data_dir": self.mac, "project": "Example Project",
                           "process": ["zz-example-app-not-running"]})
        self.assertIs(json.loads(r.stdout)["app_running"], False)
        r = self.run_room({"app": "Example App", "mac_data_dir": self.mac, "project": "Example Project"})
        self.assertIsNone(json.loads(r.stdout)["app_running"])

    def test_unknown_project_is_an_error(self):
        r = self.run_room({"app": "Example App", "mac_data_dir": self.mac, "project": "Nope"})
        self.assertIn("no project named Nope", json.loads(r.stdout)["error"])


class DispatchTest(ConfigCase):
    def setUp(self):
        super().setUp()
        self.brief = os.path.join(self.tmp, "brief.txt")
        with open(self.brief, "w") as f:
            f.write("Task: smoke test\n")

    def show(self, machine):
        return subprocess.run(["bash", os.path.join(BIN, "dispatch"), machine, self.brief],
                              capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, DISPATCH_SHOW="1"))

    def test_remote_script_uses_the_configured_path_and_folders(self):
        self.write_config(remote_path="$HOME/bin:/usr/local/bin", studio_dirs=["~/Labs"])
        r = self.show("studio-a")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("export PATH=$HOME/bin:/usr/local/bin:$PATH", r.stdout)
        self.assertIn("for d in '~/Work/example-studio' '~/Labs'; do", r.stdout)
        self.assertIn("agent prompt example-architect", r.stdout)
        self.assertNotIn("/opt/homebrew", r.stdout)

    def test_a_machine_without_an_architect_is_refused(self):
        self.write_config()
        r = self.show("studio-b")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no architect for studio-b", r.stderr)


class StudioWatchTest(ConfigCase):
    def load(self, **extra):
        self.write_config(remote_path="$HOME/bin:/usr/local/bin", **extra)
        return load_module("studio_watch_under_test", "bin/studio-watch")

    def test_probe_uses_the_configured_session_and_path(self):
        mod = self.load()
        probe = mod.probe_script()
        self.assertEqual(probe, "export PATH=$HOME/bin:/usr/local/bin:$PATH; herdr --session example-studio agent list 2>/dev/null")

    def test_targets_are_the_enabled_machines_in_config_order(self):
        mod = self.load()
        self.assertEqual(mod.watch_targets(), [("hub-1", "Hub", True), ("studio-a", "Studio A", False)])

    def test_without_a_session_it_stops_with_a_message(self):
        mod = self.load()
        self.write_config(herdr_session=None)
        with self.assertRaises(SystemExit) as caught:
            mod.probe_script()
        self.assertIn("herdr_session", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
