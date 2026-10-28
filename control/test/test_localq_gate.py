"""Unit tests for lib/localq_gate.py: approval windows, the ordered pre-run checks, and the fake gate.

Run from the repo root:  python3 -m unittest test/test_localq_gate.py
No test contacts a Mini or a model server: subprocess and urllib are patched to fail if reached, and the Mini
probes go through a fake _remote. Every test uses its own temp config and state folder.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "lib"))
import localq_gate as gate  # noqa: E402
import re_studio_config as cfg  # noqa: E402


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="localq-gate-")
        self.state = os.path.join(self.tmp, "state")
        self.config = os.path.join(self.tmp, "machines.json")
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump({
                "herdr_session": "dev-studio",
                "state_dir": self.state,
                "local_min_free_pct": 15,
                "machines": [
                    {"id": "lap", "label": "Laptop", "role": "hub", "ssh": []},
                    {"id": "m1", "label": "Mini 1", "role": "studio", "ssh": ["hosta"], "local_model": {"kind": "cli", "path": "~/Fake Tools/runner", "args": ["-p"]}},
                    {"id": "m2", "label": "Mini 2", "role": "studio", "ssh": ["hostb"],
                     "ssh_alt": [["hosta", "hostc"]]},
                ],
            }, f)
        self.env = mock.patch.dict(os.environ, {"RE_STUDIO_CONFIG": self.config}, clear=False)
        self.env.start()
        os.environ.pop("LOCALQ_FAKE_GATE", None)
        self.addCleanup(self.env.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

        # Nothing may reach a Mini or a model server from these tests.
        def no_subprocess(*a, **k):
            raise AssertionError("a real subprocess was started from a gate test")

        def no_network(*a, **k):
            raise AssertionError("a real network call was made from a gate test")

        for patcher in (mock.patch.object(gate.subprocess, "run", side_effect=no_subprocess),
                        mock.patch.object(gate.urllib.request, "urlopen", side_effect=no_network)):
            patcher.start()
            self.addCleanup(patcher.stop)

        # The fake Mini: answers the probes it is given and records every call.
        self.calls = []
        self.apps_out = ""
        self.mem_out = self.vm_stat_text()
        self.model_ok = True
        self.cli_ok = True
        self.remote_patch = mock.patch.object(gate, "_remote", side_effect=self.fake_remote)
        self.remote_patch.start()
        self.addCleanup(self.remote_patch.stop)

    def fake_remote(self, mid, remote):
        self.calls.append((mid, remote))
        if "pgrep" in remote:
            return 0, self.apps_out
        if "vm_stat" in remote:
            return 0, self.mem_out
        if "curl" in remote:
            return 0, "0\n" if self.model_ok else "7\n"
        if "test -x" in remote:
            return 0, "0\n" if self.cli_ok else "1\n"
        raise AssertionError(f"unexpected probe: {remote}")

    @staticmethod
    def vm_stat_text(free=200000, inactive=300000, purgeable=100000, page_bytes=16384, total_pages=1000000):
        return (f"Mach Virtual Memory Statistics: (page size of {page_bytes} bytes)\n"
                f"Pages free: {free}.\nPages active: 100000.\nPages inactive: {inactive}.\n"
                f"Pages speculative: 20000.\nPages wired down: 300000.\nPages purgeable: {purgeable}.\n"
                f"hw.memsize: {total_pages * page_bytes}\n")

    def write_approval(self, mid, until):
        path = gate.approvals_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({mid: {"who": "tester", "at": until - 3600, "until": until}}, f)


class ApprovalWindow(GateTest):
    def test_allow_then_deny(self):
        entry = gate.allow("m1", 2, "tester")
        self.assertAlmostEqual(entry["until"] - time.time(), 7200, delta=30)
        self.assertIsNotNone(gate.approval("m1"))
        gate.deny("m1")
        self.assertIsNone(gate.approval("m1"))

    def test_allow_rejects_unknown_machine_and_bad_hours(self):
        with self.assertRaises(KeyError):
            gate.allow("nope", 1, "tester")
        with self.assertRaises(ValueError):
            gate.allow("m1", 0, "tester")

    def test_expired_window_is_not_an_approval(self):
        self.write_approval("m1", time.time() - 10)
        self.assertIsNone(gate.approval("m1"))


class MiniChecks(GateTest):
    def set_config(self, **updates):
        with open(self.config, encoding="utf-8") as f:
            config = json.load(f)
        config.update(updates)
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump(config, f)

    def test_custom_app_list_is_quoted_and_empty_list_skips_probe(self):
        gate.allow("m1", 1, "tester")
        self.set_config(local_block_apps=["Example App", "$(bad)"])
        self.assertEqual(gate.check("m1"), (True, ""))
        self.assertIn("'Example App' '$(bad)'", self.calls[0][1])
        self.calls.clear()
        self.set_config(local_block_apps=[])
        self.assertEqual(gate.check("m1"), (True, ""))
        self.assertFalse(any("pgrep" in cmd for _, cmd in self.calls))

    def test_invalid_app_list_fails_closed(self):
        gate.allow("m1", 1, "tester")
        self.set_config(local_block_apps="Example App")
        self.assertEqual(gate.check("m1"), (False, "invalid local_block_apps configuration"))
        self.assertEqual(self.calls, [])

    def test_studio_without_a_runner_is_blocked(self):
        gate.allow("m2", 1, "tester")
        self.assertEqual(gate.check("m2"), (False, "no local runner configured on m2"))
        self.assertFalse(any("test -x" in cmd for _, cmd in self.calls))

    def test_executable_probe_quotes_home_and_absolute_paths(self):
        self.assertEqual(gate._model_check("m1", True), (True, ""))
        self.assertEqual(self.calls[-1][1], "test -x ~/'Fake Tools/runner'; echo $?")
        with mock.patch.object(gate, "local_model", return_value={"kind": "cli", "path": "/fake/a b", "args": []}):
            self.assertEqual(gate._model_check("m1", True), (True, ""))
        self.assertEqual(self.calls[-1][1], "test -x '/fake/a b'; echo $?")

    def test_no_approval_blocks_before_any_probe(self):
        ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("no approval window", reason)
        self.assertEqual(self.calls, [])

    def test_expired_approval_blocks_before_any_probe(self):
        self.write_approval("m1", time.time() - 10)
        ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("expired", reason)
        self.assertEqual(self.calls, [])

    def test_desktop_app_running_blocks_with_reason(self):
        gate.allow("m1", 1, "tester")
        self.apps_out = "Example App\n"
        ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("Example App running on m1", reason)
        self.assertFalse(any("vm_stat" in c for _, c in self.calls), "memory must not be checked after a failure")

    def test_configured_app_running_blocks(self):
        gate.allow("m1", 1, "tester")
        self.apps_out = "Example App\n"
        ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("Example App", reason)

    def test_low_memory_blocks(self):
        gate.allow("m1", 1, "tester")
        self.mem_out = self.vm_stat_text(free=30000, inactive=40000, purgeable=20000)
        ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("9%", reason)

    def test_model_missing_blocks(self):
        gate.allow("m1", 1, "tester")
        self.cli_ok = False
        ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("cli binary is missing on m1", reason)

    def test_unreachable_mini_blocks(self):
        gate.allow("m1", 1, "tester")
        with mock.patch.object(gate, "_remote", side_effect=gate.Unreachable("m1 is unreachable")):
            ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertEqual(reason, "m1 is unreachable")

    def test_ready_mini_passes_and_only_probes_read_only(self):
        gate.allow("m1", 1, "tester")
        ok, reason = gate.check("m1")
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")
        for _, cmd in self.calls:
            self.assertNotIn("open ", cmd)
            self.assertNotIn("quit", cmd)
            self.assertNotIn("kill", cmd)

    def test_vm_stat_counts_free_inactive_and_purgeable_with_the_reported_page_size(self):
        for page_bytes in (4096, 16384):
            with self.subTest(page_bytes=page_bytes):
                self.mem_out = self.vm_stat_text(free=20000, inactive=50000, purgeable=80000,
                                                 page_bytes=page_bytes)
                self.assertEqual(gate._free_pct("m1", True), 15)

    def test_mini_still_requires_fifteen_percent(self):
        gate.allow("m1", 1, "tester")
        self.mem_out = self.vm_stat_text(free=20000, inactive=50000, purgeable=79999)
        self.assertEqual(gate.check("m1"), (False, "free memory on m1 is 14%, below the 15% minimum"))

    def test_incomplete_vm_stat_blocks(self):
        gate.allow("m1", 1, "tester")
        self.mem_out = "Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 100000.\n"
        self.assertEqual(gate.check("m1"), (False, "cannot read free memory on m1"))


class LaptopChecks(GateTest):
    def setUp(self):
        super().setUp()
        self.meminfo = os.path.join(self.tmp, "meminfo")
        self.vmstat = os.path.join(self.tmp, "vmstat")
        self.write_memory(8)
        self.write_swap(10000)
        self.swap_after = 10000
        self.clock = 100.0
        self.sample_elapsed = 1
        read_memory, sample_swap = gate._local_memory, gate._swap_out_rate
        for patcher in (
                mock.patch.object(gate, "_local_memory", side_effect=lambda *args: read_memory(self.meminfo)),
                mock.patch.object(gate, "_swap_out_rate", side_effect=lambda: sample_swap(self.vmstat)),
                mock.patch.object(gate.time, "monotonic", side_effect=lambda: self.clock),
                mock.patch.object(gate.time, "sleep", side_effect=self.fake_sleep)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_memory(self, available_gib, total_gib=32):
        with open(self.meminfo, "w", encoding="utf-8") as f:
            f.write(f"MemTotal: {int(total_gib * 1024 * 1024)} kB\n"
                    f"MemAvailable: {int(available_gib * 1024 * 1024)} kB\n")

    def write_swap(self, pages):
        with open(self.vmstat, "w", encoding="utf-8") as f:
            f.write(f"pgpgout 999999\npswpin 90000\npswpout {pages}\n")

    def fake_sleep(self, seconds):
        self.assertEqual(seconds, 1)
        self.clock += self.sample_elapsed
        self.write_swap(self.swap_after)

    def fake_ok_model(self):
        response = mock.MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        return mock.patch.object(gate.urllib.request, "urlopen", return_value=response)

    def test_lap_needs_no_approval(self):
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (True, ""))
        self.assertEqual(self.calls, [])

    def test_loaded_model_with_two_point_one_gib_and_quiet_swap_passes(self):
        self.write_memory(2.1)  # 6% of total RAM; the loaded model does not need 15%.
        self.swap_after = 10010
        with self.fake_ok_model() as request:
            self.assertEqual(gate.check("lap"), (True, ""))
        request.assert_called_once_with("http://127.0.0.1:18081/v1/models", timeout=5)
        gate.time.sleep.assert_called_once_with(1)

    def test_loaded_model_with_one_point_nine_gib_blocks(self):
        self.write_memory(1.9)
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (
                False, "available memory on lap is 1.90 GiB, below the 2 GiB minimum for a loaded model"))
        gate.time.sleep.assert_not_called()

    def test_loaded_model_with_heavy_swap_blocks_and_reports_the_rate(self):
        self.write_memory(2.1)
        self.swap_after = 11201
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (
                False, "swap-out on lap is 1201 pages/s, above the 1000 pages/s maximum"))

    def test_loaded_model_accepts_exact_memory_and_swap_boundaries(self):
        self.write_memory(2)
        self.swap_after = 11000
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (True, ""))

    def test_swap_rate_uses_elapsed_time_not_the_cumulative_counter(self):
        self.swap_after = 12000
        self.sample_elapsed = 2
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (True, ""))

    def test_swap_counter_reset_blocks(self):
        self.swap_after = 9999
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (False, "cannot read swap-out rate on lap"))

    def test_missing_swap_counter_blocks(self):
        with open(self.vmstat, "w", encoding="utf-8") as f:
            f.write("pswpin 10000\n")
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (False, "cannot read swap-out rate on lap"))

    def test_missing_available_memory_blocks(self):
        with open(self.meminfo, "w", encoding="utf-8") as f:
            f.write("MemTotal: 33554432 kB\n")
        with self.fake_ok_model():
            self.assertEqual(gate.check("lap"), (False, "cannot read available memory on lap"))

    def test_not_loaded_uses_fifteen_percent_even_with_more_than_two_gib(self):
        self.write_memory(4)  # 12% of 32 GiB.
        self.assertEqual(gate._memory_check("lap", False, 15), (
            False, "free memory on lap is 12%, below the 15% minimum"))
        gate.time.sleep.assert_not_called()

    def test_not_loaded_can_pass_below_two_gib_if_fifteen_percent_is_available(self):
        self.write_memory(1.5, total_gib=8)
        self.assertEqual(gate._memory_check("lap", False, 15), (True, ""))
        gate.time.sleep.assert_not_called()

    def test_cli_on_laptop_uses_the_percentage_rule(self):
        with open(self.config, encoding="utf-8") as f:
            config = json.load(f)
        config["machines"][0]["local_model"] = {"kind": "cli", "path": "/fake/runner"}
        with open(self.config, "w", encoding="utf-8") as f:
            json.dump(config, f)
        self.write_memory(4)
        with mock.patch.object(gate.os, "access", return_value=True):
            self.assertEqual(gate.check("lap"), (
                False, "free memory on lap is 12%, below the 15% minimum"))

    def test_lap_model_unreachable_blocks(self):
        import urllib.error
        with mock.patch.object(gate.urllib.request, "urlopen", side_effect=urllib.error.URLError("refused")):
            ok, reason = gate.check("lap")
        self.assertFalse(ok)
        self.assertIn("does not answer on lap", reason)
        gate._local_memory.assert_not_called()


class FakeGate(GateTest):
    def fake_file(self, data):
        path = os.path.join(self.tmp, "fake-gate.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return mock.patch.dict(os.environ, {"LOCALQ_FAKE_GATE": path})

    def test_fake_gate_file_replaces_the_app_memory_and_model_checks(self):
        gate.allow("m1", 1, "tester")
        gate.allow("m2", 1, "tester")
        with self.fake_file({"m1": [False, "fake reason"], "lap": [True, ""]}):
            self.assertEqual(gate.check("m1"), (False, "fake reason"))
            self.assertEqual(gate.check("lap"), (True, ""))
            self.assertEqual(gate.check("m2"), (False, "no fake gate entry for m2"))
        self.assertEqual(self.calls, [], "the fake gate must not reach a Mini")

    def test_fake_ok_still_blocked_without_an_approval(self):
        with self.fake_file({"m1": [True, ""]}):
            ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("no approval window", reason)
        self.assertEqual(self.calls, [])

    def test_fake_ok_still_blocked_by_an_expired_approval(self):
        self.write_approval("m1", time.time() - 10)
        with self.fake_file({"m1": [True, ""]}):
            ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("expired", reason)

    def test_fake_ok_passes_for_a_mini_with_a_valid_approval(self):
        gate.allow("m1", 1, "tester")
        with self.fake_file({"m1": [True, ""]}):
            self.assertEqual(gate.check("m1"), (True, ""))
        self.assertEqual(self.calls, [])

    def test_unknown_machine_fails_even_with_a_fake_entry(self):
        with self.fake_file({"nope": [True, ""]}):
            ok, reason = gate.check("nope")
        self.assertFalse(ok)
        self.assertIn("unknown machine", reason)


class RemoteRoutes(GateTest):
    def setUp(self):
        super().setUp()
        self.remote_patch.stop()  # test the real _remote, with subprocess still patched to fail

    @staticmethod
    def done(rc, out=""):
        return mock.Mock(returncode=rc, stdout=out)

    def test_falls_through_to_the_next_route_when_ssh_cannot_connect(self):
        with mock.patch.object(gate.subprocess, "run", side_effect=[self.done(255), self.done(0, "ok\n")]) as run:
            self.assertEqual(gate._remote("m2", "echo ok"), (0, "ok\n"))
        hosts = [call.args[0][-2] for call in run.call_args_list]
        self.assertEqual(hosts, ["hostb", "hosta"])  # direct first, then the outer ssh to the jump host hosta

    def test_all_routes_failing_is_unreachable(self):
        with mock.patch.object(gate.subprocess, "run", return_value=self.done(255)):
            with self.assertRaises(gate.Unreachable):
                gate._remote("m2", "echo ok")

    def test_remote_failure_is_an_answer_not_unreachable(self):
        with mock.patch.object(gate.subprocess, "run", return_value=self.done(1, "")):
            self.assertEqual(gate._remote("m1", "false"), (1, ""))


class CheckOrder(GateTest):
    def test_approval_is_reported_before_memory(self):
        self.mem_out = self.vm_stat_text(free=10000, inactive=0, purgeable=0)
        ok, reason = gate.check("m1")
        self.assertFalse(ok)
        self.assertIn("no approval window", reason)

    def test_model_is_checked_before_memory(self):
        gate.allow("m1", 1, "tester")
        self.cli_ok = False
        self.mem_out = "invalid memory output\n"
        self.assertEqual(gate.check("m1"), (False, "the cli binary is missing on m1"))
        self.assertTrue(any("test -x" in c for _, c in self.calls))
        self.assertFalse(any("vm_stat" in c for _, c in self.calls))

    def test_ready_mini_checks_model_before_vm_stat(self):
        gate.allow("m1", 1, "tester")
        self.assertEqual(gate.check("m1"), (True, ""))
        self.assertIn("pgrep", self.calls[0][1])
        self.assertIn("test -x", self.calls[1][1])
        self.assertEqual(self.calls[2][1], gate.MEMORY_PROBE)


if __name__ == "__main__":
    unittest.main()
