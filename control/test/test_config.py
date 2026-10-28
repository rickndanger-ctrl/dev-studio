"""Unit tests for lib/re_studio_config.py: config lookup, routes, nested SSH quoting, and the CLI.

Run from the repo root:  python3 -m unittest discover -s test -p 'test_*.py'
Every test sets RE_STUDIO_CONFIG, so the developer's own machines.json is never read.
"""
import contextlib
import io
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

FIXTURES = os.path.join(HERE, "fixtures")
ROUTES = os.path.join(FIXTURES, "routes-machines.json")
CI = os.path.join(FIXTURES, "ci-machines.json")
OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6"]


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("RE_STUDIO_CONFIG")
        self.tmp = tempfile.mkdtemp(prefix="re-studio-test-")
        os.environ["RE_STUDIO_CONFIG"] = ROUTES

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("RE_STUDIO_CONFIG", None)
        else:
            os.environ["RE_STUDIO_CONFIG"] = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use(self, path):
        os.environ["RE_STUDIO_CONFIG"] = path

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cfg._main(argv)
        return code, out.getvalue(), err.getvalue()


class LookupTest(ConfigTest):
    def test_env_var_picks_the_config_file(self):
        self.assertEqual(cfg.config_path(), ROUTES)

    def test_get_reads_top_level_keys(self):
        self.assertEqual(cfg.get("viewer_port"), 8799)
        self.assertEqual(cfg.get("missing_key", "fallback"), "fallback")

    def test_field_reads_one_machine_field(self):
        self.assertEqual(cfg.machine("m1")["label"], "Studio A")
        self.assertEqual(cfg.machine("m1").get("architect"), "studio-architect")

    def test_unknown_machine_raises_key_error(self):
        with self.assertRaises(KeyError):
            cfg.machine("nope")

    def test_machines_filter_by_role(self):
        self.assertEqual([m["id"] for m in cfg.machines("studio")], ["m1", "m2", "m3"])
        self.assertEqual([m["id"] for m in cfg.machines("hub")], ["lap"])
        self.assertEqual([m["id"] for m in cfg.machines()], ["lap", "m1", "m2", "m3"])

    def test_state_dir_defaults_inside_repo(self):
        self.assertEqual(cfg.state_dir(), os.path.join(REPO, "state"))


class RoutesTest(ConfigTest):
    def test_hop_lists_primary_first_then_alternates(self):
        self.assertEqual(cfg.hop_lists("m2"), [["host-b"], ["host-a", "host-b-internal"]])

    def test_disabled_machine_has_no_routes(self):
        self.assertEqual(cfg.hop_lists("m3"), [])
        self.assertEqual(cfg.routes("m3", "uptime"), [])

    def test_hub_without_ssh_has_no_routes(self):
        self.assertEqual(cfg.routes("lap", "uptime"), [])

    def test_routes_pair_each_argv_with_its_hops(self):
        rs = cfg.routes("m2", "uptime")
        self.assertEqual([hops for _, hops in rs], [["host-b"], ["host-a", "host-b-internal"]])
        self.assertEqual(rs[0][0][-2:], ["host-b", "uptime"])

    def test_direct_ssh_command(self):
        self.assertEqual(cfg.ssh_command(["host-a"], "uptime"),
                         ["ssh"] + OPTS + ["host-a", "uptime"])

    def test_one_hop_quotes_the_remote_command(self):
        remote = "echo 'it works' && uptime"
        argv = cfg.ssh_command(["host-a", "host-b-internal"], remote)
        self.assertEqual(argv[:-1], ["ssh"] + OPTS + ["host-a"])
        # The last argv item is what the first host runs; splitting it must give back the inner ssh.
        self.assertEqual(shlex.split(argv[-1]),
                         ["ssh"] + OPTS + ["host-b-internal", remote])

    def test_two_hops_survive_two_levels_of_quoting(self):
        remote = "printf '%s\\n' \"a b\"; echo done"
        argv = cfg.ssh_command(["host-a", "host-b-internal", "host-c-deep"], remote)
        outer = shlex.split(argv[-1])
        self.assertEqual(outer[:len(OPTS) + 2], ["ssh"] + OPTS + ["host-b-internal"])
        self.assertEqual(shlex.split(outer[-1]), ["ssh"] + OPTS + ["host-c-deep", remote])

    def test_route_label_direct_for_one_hop(self):
        self.assertEqual(cfg.route_label(["host-a"]), "direct")

    def test_route_label_names_the_jump_machine(self):
        self.assertEqual(cfg.route_label(["host-a", "host-b-internal"]), "via Studio A")

    def test_route_label_falls_back_to_the_host_name(self):
        self.assertEqual(cfg.route_label(["unknown-x", "y"]), "via unknown-x")


class CliTest(ConfigTest):
    def test_get_prints_value(self):
        code, out, _ = self.run_cli(["get", "viewer_port"])
        self.assertEqual((code, out.strip()), (0, "8799"))

    def test_get_missing_key_returns_1(self):
        code, out, _ = self.run_cli(["get", "nope"])
        self.assertEqual((code, out), (1, ""))

    def test_field_prints_value(self):
        code, out, _ = self.run_cli(["field", "m1", "architect"])
        self.assertEqual((code, out.strip()), (0, "studio-architect"))

    def test_field_unknown_machine_returns_2(self):
        code, _, err = self.run_cli(["field", "zz", "label"])
        self.assertEqual(code, 2)
        self.assertIn("no machine", err)

    def test_ids_lists_role_members(self):
        code, out, _ = self.run_cli(["ids", "studio"])
        self.assertEqual((code, out.split()), (0, ["m1", "m2", "m3"]))

    def test_path_prints_the_config_in_use(self):
        code, out, _ = self.run_cli(["path"])
        self.assertEqual((code, out.strip()), (0, ROUTES))

    def test_run_on_disabled_machine_fails_without_ssh(self):
        code, _, err = self.run_cli(["run", "m3", "uptime"])
        self.assertEqual(code, 2)
        self.assertIn("no ssh route", err)

    def test_no_arguments_prints_usage(self):
        code, _, err = self.run_cli([])
        self.assertEqual(code, 2)
        self.assertIn("re_studio_config", err)


class CiFixtureTest(ConfigTest):
    """The config test/shot.sh uses in CI: the laptop only, nothing to reach."""

    def test_ci_config_has_no_studios_and_no_routes(self):
        self.use(CI)
        self.assertEqual(cfg.machines("studio"), [])
        self.assertEqual(cfg.routes("lap", "uptime"), [])
        self.assertEqual(cfg.get("viewer_port"), 8799)

    def test_custom_config_written_at_runtime(self):
        path = os.path.join(self.tmp, "machines.json")
        with open(path, "w") as f:
            json.dump({"viewer_port": 8801, "machines": [{"id": "lap", "role": "hub", "ssh": []}]}, f)
        self.use(path)
        self.assertEqual(cfg.get("viewer_port"), 8801)
        self.assertEqual(cfg.machine("lap").get("label", "lap"), "lap")


class RunRoutesTest(ConfigTest):
    """`re_studio_config.py run`: only ssh's connect failure (255) moves to the next route. subprocess.run is mocked."""

    def fake(self, results):
        calls = []
        queue = list(results)

        def run(argv, **kwargs):
            calls.append(argv)
            code, err = queue.pop(0)
            return subprocess.CompletedProcess(argv, code, stdout=b"", stderr=err)
        return run, calls

    def run_on_m2(self, results):
        run, calls = self.fake(results)
        with mock.patch.object(cfg.subprocess, "run", side_effect=run):
            code, _, err = self.run_cli(["run", "m2", "uptime"])
        return code, calls, err

    def test_connect_failure_falls_back_to_the_next_route(self):
        code, calls, _ = self.run_on_m2([(255, b"connection refused"), (0, b"")])
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn("host-b-internal", calls[1][-1], "the second route goes through the other hop")

    def test_a_nonzero_exit_is_returned_at_once_and_not_retried(self):
        code, calls, err = self.run_on_m2([(1, b"script failed\n"), (0, b"")])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1, "exactly one call: the job may have started")
        self.assertIn("script failed", err, "the failing route's stderr is still shown")

    def test_success_on_the_first_route_makes_one_call(self):
        code, calls, _ = self.run_on_m2([(0, b"")])
        self.assertEqual((code, len(calls)), (0, 1))

    def test_every_route_failing_to_connect_returns_255(self):
        code, calls, _ = self.run_on_m2([(255, b""), (255, b"")])
        self.assertEqual(code, 255)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
