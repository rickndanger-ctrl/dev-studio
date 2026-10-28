"""Socket-free tests for studio team grouping and route attribution."""
import unittest

from lib import studio_teams as teams


class StudioTeamsTest(unittest.TestCase):
    def test_team_and_role_suffixes(self):
        self.assertEqual(teams.ROLE_RX, r"(?:^|-)(architect|main|messenger|worker-\d+)$")
        self.assertEqual(teams.team_of("example-codex-worker-10"), ("example-codex", "worker-10"))
        self.assertEqual(teams.team_of("architect"), ("", "architect"))
        self.assertIsNone(teams.team_of("example-visitor"))
        self.assertIsNone(teams.team_of("studio-domain"))
        self.assertIsNone(teams.team_of("remain"))
        self.assertIsNone(teams.team_of(None))

    def test_real_shape_groups_two_teams_with_role_and_numeric_order(self):
        def agent(name, kind, status):
            return {"name": name, "agent": kind, "agent_status": status,
                    "cwd": "/fake/project", "pane_id": "pane", "workspace": "example"}
        rows = [agent("example-worker-10", "claude", "idle"),
                agent("example-codex-main", "codex", "idle"),
                agent("example-worker-2", "claude", "done"),
                agent("example-messenger", "claude", "idle"),
                agent("example-main", "claude", "idle"),
                agent("example-architect", "claude", "idle"),
                agent("example-codex-architect", "codex", "working"),
                agent("example-visitor", "claude", "working")]
        grouped = teams.group(rows)
        self.assertEqual([(t["team"], t["kind"], t["idle"]) for t in grouped],
                         [("example", "claude", True), ("example-codex", "codex", False)])
        self.assertEqual([a["name"] for a in grouped[0]["agents"]],
                         ["example-architect", "example-main", "example-messenger",
                          "example-worker-2", "example-worker-10"])
        self.assertEqual(grouped[1]["agents"], [rows[6], rows[1]])
        self.assertIs(grouped[0]["agents"][0], rows[5])

    def test_mixed_kinds_and_status_fallback(self):
        rows = [{"name": "example-main", "agent": "claude", "status": "blocked"},
                {"name": "example-architect", "agent": "codex", "agent_status": "idle", "status": "working"}]
        result = teams.group(rows)
        self.assertEqual((result[0]["kind"], result[0]["idle"]), ("mixed", False))
        rows[0]["status"] = "idle"
        self.assertTrue(teams.group(rows)[0]["idle"])
        self.assertEqual(teams.group([{"name": "unnamed"}]), [])
        self.assertEqual(teams.group([{"name": "example-main"}])[0]["kind"], "?")
        self.assertEqual(teams.group([{"name": "example-main"}, {"name": "example-architect", "agent": "codex"}])[0]["kind"], "mixed")

    def test_route_uses_sender_then_recipient(self):
        self.assertEqual(teams.team_of_route({"from": "one-worker-1", "to": "two-main"}), "one")
        self.assertEqual(teams.team_of_route({"from": "external", "to": "two-main"}), "two")
        self.assertEqual(teams.team_of_route({"from": "architect", "to": "two-main"}), "")
        self.assertIsNone(teams.team_of_route({"from": "external", "to": "other"}))
        self.assertIsNone(teams.team_of_route({}))


if __name__ == "__main__":
    unittest.main()
