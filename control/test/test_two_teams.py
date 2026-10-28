"""Per-team audit: bin/studio-snap.py returns one snapshot per studio team, and bin/re-audit keys verdicts
"<mid>/<team>" with the label "<machine label> · <team label>".

Fake names only (example machines, example teams). Nothing here reaches a machine: the team snapshots are built from
fixture rows, and the Mini-side prelude is run as a local subprocess.
Run from the repo root:  python3 -m unittest discover -s test -p 'test_*.py'
"""
import importlib.machinery
import importlib.util
import json
import os
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

ROWS = [
    {"name": "team-alpha-architect", "agent": "claude", "agent_status": "working", "cwd": "/nowhere/alpha"},
    {"name": "team-alpha-worker-1", "agent": "claude", "agent_status": "idle", "cwd": "/nowhere/alpha"},
    {"name": "team-beta-architect", "agent": "codex", "agent_status": "idle", "cwd": "/nowhere/beta"},
    {"name": "team-beta-worker-2", "agent": "codex", "agent_status": "blocked", "cwd": "/nowhere/beta"},
    {"name": "helper-without-a-role", "agent": "claude", "agent_status": "idle", "cwd": "/nowhere"},
]
ROUTES = [
    {"from": "team-alpha-architect", "to": "team-alpha-worker-1", "text": "task", "t": 1},
    {"from": "team-beta-architect", "to": "team-beta-worker-2", "text": "task", "t": 2},
    {"from": "auto", "to": "team-alpha-main", "text": "unrouted", "t": 3},
]


def load_module(name, rel):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(REPO, rel))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TeamSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.snap = load_module("studio_snap_under_test", "bin/studio-snap.py")

    def test_one_snapshot_per_team_with_its_own_agents_and_routes(self):
        teams = self.snap.team_snapshots(ROWS, ROUTES, fallback_repo="")
        self.assertEqual([t["team"] for t in teams], ["team-alpha", "team-beta"])
        alpha, beta = teams
        self.assertEqual([a["name"] for a in alpha["agents"]], ["team-alpha-architect", "team-alpha-worker-1"])
        self.assertEqual((alpha["kind"], beta["kind"]), ("claude", "codex"))
        # The 'auto' row has no team sender, so it belongs to the recipient's team.
        self.assertEqual([r["text"] for r in alpha["routes"]], ["task", "unrouted"])
        self.assertEqual([r["text"] for r in beta["routes"]], ["task"])

    def test_idle_means_no_agent_working_or_blocked(self):
        alpha, beta = self.snap.team_snapshots(ROWS, ROUTES, fallback_repo="")
        self.assertFalse(alpha["idle"])     # its architect is working
        self.assertFalse(beta["idle"])      # its worker is blocked

    def test_a_row_without_a_role_is_not_a_team_member(self):
        names = [a["name"] for t in self.snap.team_snapshots(ROWS, ROUTES) for a in t["agents"]]
        self.assertNotIn("helper-without-a-role", names)

    def test_commits_come_from_the_team_repo_or_the_machine_project(self):
        seen = []
        with mock.patch.object(self.snap, "commits", side_effect=lambda repo: seen.append(repo) or [{"hash": repo}]):
            with mock.patch.object(self.snap, "toplevel", side_effect=lambda cwd: "/repo/" + cwd.rsplit("/", 1)[-1]):
                teams = self.snap.team_snapshots(ROWS, ROUTES, fallback_repo="/repo/project")
        self.assertEqual([c["hash"] for c in teams[0]["commits"]], ["/repo/alpha"])
        self.assertEqual([c["hash"] for c in teams[1]["commits"]], ["/repo/beta"])
        self.assertEqual(sorted(set(seen)), ["/repo/alpha", "/repo/beta"])

    def test_a_team_with_no_repo_falls_back_to_the_machine_project(self):
        with mock.patch.object(self.snap, "toplevel", return_value=""), \
                mock.patch.object(self.snap, "commits", return_value=[{"hash": "abc"}]) as commits:
            teams = self.snap.team_snapshots(ROWS, ROUTES, fallback_repo="/repo/project")
        commits.assert_called_with("/repo/project")
        self.assertEqual([c["hash"] for c in teams[0]["commits"]], ["abc"])


class MiniPreludeTest(unittest.TestCase):
    def test_a_script_with_the_prelude_imports_the_library_without_a_file(self):
        with open(os.path.join(REPO, "lib", "studio_teams.py")) as f:
            library = f.read()
        script = (library_prelude() + "import studio_teams\n"
                  "print(studio_teams.team_of('team-alpha-worker-1'))\n")
        # The prelude alone must provide the module: run from an empty folder, so no lib/ can be found on the path.
        tmp = tempfile.mkdtemp(prefix="two-teams-")
        self.addCleanup(shutil.rmtree, tmp, True)
        r = subprocess.run([sys.executable, "-"], input=script, capture_output=True, text=True, cwd=tmp, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "('team-alpha', 'worker-1')")


def library_prelude():
    snap = load_module("studio_snap_prelude", "bin/studio-snap.py")
    with open(os.path.join(REPO, "lib", "studio_teams.py")) as f:
        return snap.library_prelude(f.read())


class ReAuditUnitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="two-teams-audit-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = os.environ.get("RE_STUDIO_CONFIG")
        self.addCleanup(self.restore_env)
        conf = {"state_dir": os.path.join(self.tmp, "state"),
                "machines": [{"id": "hub-1", "label": "Hub", "role": "hub", "ssh": []},
                             {"id": "mini-a", "label": "Example Mini", "role": "studio", "ssh": ["mini-a.example"]},
                             {"id": "mini-b", "label": "Other Mini", "role": "studio", "enabled": False, "ssh": []}],
                "audit": {"app": "Example App", "data_dir": self.tmp, "mac_data_dir": self.tmp, "project": "Example"}}
        path = os.path.join(self.tmp, "machines.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conf, f)
        os.environ["RE_STUDIO_CONFIG"] = path
        self.audit = load_module("re_audit_two_teams", "bin/re-audit")

    def restore_env(self):
        if self.saved is None:
            os.environ.pop("RE_STUDIO_CONFIG", None)
        else:
            os.environ["RE_STUDIO_CONFIG"] = self.saved

    def snaps(self):
        return {
            "hub-1": {"online": True, "project": "hub", "teams": [{"team": "", "agents": [], "routes": [], "commits": []}]},
            "mini-a": {"online": True, "project": "example", "teams": [
                {"team": "team-alpha", "kind": "claude", "agents": [{"name": "team-alpha-architect", "status": "idle"}],
                 "routes": [], "commits": []},
                {"team": "team-beta", "kind": "codex", "agents": [{"name": "team-beta-architect", "status": "idle"}],
                 "routes": [], "commits": []}]},
            "mini-b": {"online": False, "note": "no ssh route"},
        }

    def test_one_unit_per_team_keyed_by_machine_and_team(self):
        units = self.audit.studio_units(self.snaps())
        self.assertEqual([(k, label) for k, label, _ in units], [
            ("hub-1/studio", "Hub · studio"),
            ("mini-a/team-alpha", "Example Mini · team-alpha"),
            ("mini-a/team-beta", "Example Mini · team-beta"),
        ])

    def test_each_unit_carries_its_team_snapshot_and_the_project(self):
        units = {k: snap for k, _, snap in self.audit.studio_units(self.snaps())}
        self.assertEqual(units["mini-a/team-beta"]["kind"], "codex")
        self.assertEqual(units["mini-a/team-beta"]["project"], "example")

    def test_a_machine_that_is_down_is_one_unit_under_its_name(self):
        snaps = self.snaps()
        snaps["mini-a"] = {"online": False, "note": "unreachable"}
        units = self.audit.studio_units(snaps)
        self.assertIn(("mini-a/studio", "Example Mini", snaps["mini-a"]), units)

    def test_haiku_is_asked_once_per_changed_team_and_verdicts_are_keyed_per_team(self):
        asked = []

        def fake_ask(key, label, snap):
            asked.append(key)
            return {"studio": key, "status": "idle", "summary": "ok", "concern": "", "needs_claude": False}

        class FakeSnap:
            @staticmethod
            def everyone():
                return self.snaps()

        with mock.patch.object(self.audit, "_load_snap", return_value=FakeSnap), \
                mock.patch.object(self.audit, "ask_studio", side_effect=fake_ask):
            studios, _, _ = self.audit.studio_pass({})
            self.assertEqual(sorted(asked), ["hub-1/studio", "mini-a/team-alpha", "mini-a/team-beta"])
            self.assertEqual(studios["mini-a/team-alpha"]["label"], "Example Mini · team-alpha")
            asked.clear()
            self.audit.studio_pass({"studios": studios})    # nothing changed: no new Haiku question
            self.assertEqual(asked, [])


if __name__ == "__main__":
    unittest.main()
