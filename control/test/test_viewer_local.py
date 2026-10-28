"""Unit tests for the viewer's LOCAL WORK summary (viewer/server.py: local_state, local_today, refresh_local).

Run from the repo root:  python3 -m unittest test/test_viewer_local.py
The viewer is imported from its file with a temp config and folder; no Mini and no server is contacted.
"""
import importlib.util
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

TMP = tempfile.mkdtemp(prefix="viewer-local-")
PROJECT = os.path.join(TMP, "proj")          # a fixture studio project: the laptop's mail is never the real one's
os.makedirs(PROJECT)
CONFIG = os.path.join(TMP, "machines.json")
with open(CONFIG, "w", encoding="utf-8") as f:
    json.dump({
        "herdr_session": "dev-studio",
        "state_dir": os.path.join(TMP, "state"),
        "machines": [
            {"id": "lap", "label": "Laptop", "role": "hub", "ssh": [], "project": PROJECT},
            {"id": "m1", "label": "Mini 1", "role": "studio", "ssh": ["hosta"]},
        ],
    }, f)


def load_server():
    """Import viewer/server.py from its file. Its own argv parsing must not see the test runner's arguments."""
    spec = importlib.util.spec_from_file_location("viewer_server_under_test", os.path.join(REPO, "viewer", "server.py"))
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"RE_STUDIO_CONFIG": CONFIG}), mock.patch.object(sys, "argv", ["server.py"]):
        spec.loader.exec_module(module)
    return module


server = load_server()


def write_job(state, machine, job_id, status, **extra):
    d = os.path.join(state, "localq", "jobs", machine)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{job_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"id": job_id, "status": status, **extra}, f)


def write_stats(state, days, legacy=False):
    """localq/stats.json, where bin/localq writes now; legacy=True writes the old state/localq-stats.json."""
    path = os.path.join(state, "localq-stats.json") if legacy else os.path.join(state, "localq", "stats.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"days": days, "totals": {}}, f)


def fixture_mail(name, text_chars):
    """A fresh delivered-mail folder for one test, holding one message of text_chars characters sent now."""
    folder = os.path.join(TMP, "mail-" + name)
    os.makedirs(folder, exist_ok=True)
    if text_chars:
        with open(os.path.join(folder, "1.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "1", "t": time.time(), "from": "a", "to": "b", "text": "p" * text_chars}, f)
    return folder


class LocalState(unittest.TestCase):
    def setUp(self):
        self.state = os.path.join(TMP, "state-" + self._testMethodName)
        os.makedirs(self.state)
        patcher = mock.patch.object(server, "STATE", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = int(time.time())
        self.today = time.strftime("%Y-%m-%d")

    def test_counts_queued_running_and_done_today(self):
        write_job(self.state, "m1", "a", "queued", queued_at=self.now)
        write_job(self.state, "m1", "b", "queued", queued_at=self.now)
        write_job(self.state, "m1", "c", "running", queued_at=self.now)
        write_job(self.state, "m1", "d", "done", finished_at=self.now, chars_in=30, chars_out=12)
        write_job(self.state, "m1", "e", "done", finished_at=self.now - 3 * 86400, chars_in=999)  # not today
        self.assertEqual(server.local_state("m1"), {"queued": 2, "running": 1, "running_id": "c", "held": 0,
                                                    "blocked": "", "done_today": 1, "done_today_chars": 42})

    def test_latest_blocked_reason_wins(self):
        write_job(self.state, "m1", "old", "blocked", queued_at=self.now - 60, reason="memory is low")
        write_job(self.state, "m1", "new", "blocked", queued_at=self.now, reason="ChatGPT running on m1")
        summary = server.local_state("m1")
        self.assertEqual(summary["blocked"], "ChatGPT running on m1")
        self.assertEqual(summary["held"], 2)

    def test_missing_folder_is_all_zero(self):
        self.assertEqual(server.local_state("lap"), {"queued": 0, "running": 0, "running_id": "", "held": 0,
                                                     "blocked": "", "done_today": 0, "done_today_chars": 0})


class LocalToday(unittest.TestCase):
    def setUp(self):
        self.state = os.path.join(TMP, "today-" + self._testMethodName)
        os.makedirs(self.state)
        patcher = mock.patch.object(server, "STATE", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.today = time.strftime("%Y-%m-%d")
        cwd = os.getcwd()
        os.chdir(PROJECT)
        self.addCleanup(os.chdir, cwd)

    def test_sums_jobs_and_characters_over_machines_for_today_only(self):
        write_stats(self.state, {
            self.today: {"lap": {"jobs": 2, "chars_in": 100, "chars_out": 50},
                         "m1": {"jobs": 1, "chars_in": 30, "chars_out": 20}},
            "1999-01-01": {"m1": {"jobs": 9, "chars_in": 9999, "chars_out": 9999}},
        })
        with mock.patch.object(server, "MAIL_DIR", fixture_mail("sum", 300)):
            # output chars 50 + 20 = 70 against 300 paid: 70 / 370 = 19%
            self.assertEqual(server.local_today(), {"jobs": 3, "chars": 200, "paid_chars": 300, "pct": 19})

    def test_reads_the_old_stats_file_until_the_new_one_exists(self):
        write_stats(self.state, {self.today: {"lap": {"jobs": 2, "chars_in": 4, "chars_out": 6}}}, legacy=True)
        with mock.patch.object(server, "MAIL_DIR", fixture_mail("legacy", 0)):
            today = server.local_today()
            self.assertEqual((today["jobs"], today["chars"]), (2, 10))
            write_stats(self.state, {self.today: {"lap": {"jobs": 5, "chars_in": 0, "chars_out": 0}}})
            self.assertEqual(server.local_today()["jobs"], 5)     # the new file wins while both exist

    def test_no_stats_file_is_zero(self):
        with mock.patch.object(server, "MAIL_DIR", fixture_mail("none", 0)):
            self.assertEqual(server.local_today(), {"jobs": 0, "chars": 0, "paid_chars": 0, "pct": None})

    def test_share_is_unknown_when_the_mail_folder_is_not_configured(self):
        write_stats(self.state, {self.today: {"lap": {"jobs": 1, "chars_in": 10, "chars_out": 10}}})
        with mock.patch.object(server, "MAIL_DIR", None):
            self.assertEqual(server.local_today(), {"jobs": 1, "chars": 20, "paid_chars": None, "pct": None})


class RefreshAndSummary(unittest.TestCase):
    def setUp(self):
        self.state = os.path.join(TMP, "refresh-" + self._testMethodName)
        os.makedirs(self.state)
        patcher = mock.patch.object(server, "STATE", self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = int(time.time())
        self.today = time.strftime("%Y-%m-%d")
        cwd = os.getcwd()
        os.chdir(PROJECT)
        self.addCleanup(os.chdir, cwd)

    def test_refresh_puts_each_machines_summary_into_the_shared_state(self):
        write_job(self.state, "lap", "x", "running", queued_at=self.now)
        write_job(self.state, "m1", "y", "blocked", queued_at=self.now, reason="Claude running on m1")
        write_stats(self.state, {self.today: {"lap": {"jobs": 1, "chars_in": 5, "chars_out": 6}}})
        with mock.patch.object(server, "MAIL_DIR", fixture_mail("refresh", 11)):
            server.refresh_local()
        self.assertEqual(server.state["laptop"]["local"]["running"], 1)
        self.assertEqual(server.state["minis"]["m1"]["local"]["blocked"], "Claude running on m1")
        # output chars 6 against 11 paid: 6 / 17 = 35%
        self.assertEqual(server.state["local_today"], {"jobs": 1, "chars": 11, "paid_chars": 11, "pct": 35})

    def test_glasses_summary_carries_local_today(self):
        write_stats(self.state, {self.today: {"m1": {"jobs": 4, "chars_in": 0, "chars_out": 10}}})
        with mock.patch.object(server, "MAIL_DIR", fixture_mail("glasses", 0)):
            self.assertEqual(server.glasses_summary()["local_today"],
                             {"jobs": 4, "chars": 10, "paid_chars": 0, "pct": 100})


if __name__ == "__main__":
    unittest.main()
