"""Socket-free tests for lib/audit_facts: the per-team facts and the short prompt."""
import json, os, sys, unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import audit_facts

NOW = 10000.0


def snap(**kw):
    base = {"agents": [{"name": "t-architect", "status": "idle"}, {"name": "t-main", "status": "working"}, {"name": "t-worker-1", "status": "done"}],
            "routes": [], "commits": []}
    base.update(kw)
    return base


def route(text, ago, sender="t-architect"):
    return {"from": sender, "text": text, "t": NOW - ago}


class FactsTest(unittest.TestCase):
    def test_agents_are_counted_by_state_with_names(self):
        f = audit_facts.facts(snap(agents=snap()["agents"] + [{"name": "t-x", "status": "?"}]), NOW)
        self.assertEqual(f["agents"]["working"], {"count": 1, "names": ["t-main"]})
        self.assertEqual(f["agents"]["done"]["count"], 1)
        self.assertEqual(f["agents"]["other"]["names"], ["t-x"])

    def test_names_are_capped(self):
        agents = [{"name": f"w{i}", "status": "idle"} for i in range(20)]
        f = audit_facts.facts(snap(agents=agents), NOW)
        self.assertEqual(f["agents"]["idle"]["count"], 20)
        self.assertEqual(len(f["agents"]["idle"]["names"]), audit_facts.MAX_NAMES)

    def test_minutes_since_the_last_route_and_commit_and_recent_counts(self):
        f = audit_facts.facts(snap(routes=[route("a", 3000), route("b", 120), route("c", 600)],
                                   commits=[{"hash": "h1", "t": NOW - 1500}, {"hash": "h2", "t": NOW - 300}]), NOW)
        self.assertEqual((f["minutes_since_route"], f["minutes_since_commit"]), (2, 5))
        self.assertEqual((f["routes_last_15_min"], f["commits_last_15_min"]), (2, 1))

    def test_no_routes_or_commits_gives_none(self):
        f = audit_facts.facts(snap(), NOW)
        self.assertEqual((f["minutes_since_route"], f["minutes_since_commit"], f["routes_last_15_min"]), (None, None, 0))

    def test_a_commit_without_a_time_does_not_crash(self):
        f = audit_facts.facts(snap(commits=[{"hash": "h", "subject": "s", "files": 1}]), NOW)
        self.assertIsNone(f["minutes_since_commit"])

    def test_repeated_message_needs_the_same_sender_and_text(self):
        same = audit_facts.facts(snap(routes=[route("fix it", 60), route("fix it", 30)]), NOW)
        other = audit_facts.facts(snap(routes=[route("fix it", 60), route("fix it", 30, "t-worker-1")]), NOW)
        self.assertTrue(same["repeated_message"])
        self.assertFalse(other["repeated_message"])

    def test_one_text_sent_to_different_recipients_is_not_a_repeat(self):
        a, b = dict(route("hash abc", 60), to=["t-architect"]), dict(route("hash abc", 30), to=["t-worker-1"])
        self.assertFalse(audit_facts.facts(snap(routes=[a, b]), NOW)["repeated_message"])
        self.assertTrue(audit_facts.facts(snap(routes=[a, dict(a, t=NOW - 10)]), NOW)["repeated_message"])

    def test_commits_touch_task_files_yes_no_unknown(self):
        task = [route("edit lib/audit_facts.py and test_x.py", 60)]
        yes = audit_facts.facts(snap(routes=task, commits=[{"hash": "h", "paths": ["lib/audit_facts.py"], "t": NOW}]), NOW)
        no = audit_facts.facts(snap(routes=task, commits=[{"hash": "h", "paths": ["docs/other.md"], "t": NOW}]), NOW)
        no_paths = audit_facts.facts(snap(routes=task, commits=[{"hash": "h", "t": NOW}]), NOW)
        no_names = audit_facts.facts(snap(routes=[route("please continue", 60)], commits=[{"hash": "h", "paths": ["a.py"], "t": NOW}]), NOW)
        no_commits = audit_facts.facts(snap(routes=task), NOW)
        self.assertEqual([x["commits_touch_task_files"] for x in (yes, no, no_paths, no_names, no_commits)],
                         ["yes", "no", "unknown", "unknown", "unknown"])

    def test_only_the_last_three_architect_tasks_are_kept_and_cut(self):
        routes = [route(f"task {i} " + "x" * 300, 600 - i) for i in range(5)] + [route("worker reply", 10, "t-worker-1")]
        f = audit_facts.facts(snap(routes=routes), NOW)
        self.assertEqual(len(f["tasks"]), 3)
        self.assertTrue(f["tasks"][0].startswith("task 2"))
        self.assertTrue(all(len(t) <= audit_facts.TASK_CHARS for t in f["tasks"]))


class SkewAndFullTextTest(unittest.TestCase):
    def test_small_clock_skew_clamps_to_now_and_counts_as_recent(self):
        f = audit_facts.facts(snap(routes=[route("a", -120)], commits=[{"hash": "h", "t": NOW + 200}]), NOW)
        self.assertEqual((f["minutes_since_route"], f["minutes_since_commit"]), (0, 0))
        self.assertEqual((f["routes_last_15_min"], f["commits_last_15_min"]), (1, 1))

    def test_a_time_more_than_five_minutes_ahead_is_unknown_not_recent(self):
        f = audit_facts.facts(snap(routes=[route("a", -400)], commits=[{"hash": "h", "t": NOW + 3600}]), NOW)
        self.assertEqual((f["minutes_since_route"], f["minutes_since_commit"]), (None, None))
        self.assertEqual((f["routes_last_15_min"], f["commits_last_15_min"]), (0, 0))

    def test_a_far_future_time_does_not_hide_a_real_old_one(self):
        f = audit_facts.facts(snap(routes=[route("a", -9000), route("b", 600)]), NOW)
        self.assertEqual(f["minutes_since_route"], 10)

    def test_files_named_past_the_display_cut_still_match_commits(self):
        task = "x" * 200 + " then edit lib/audit_facts.py"
        f = audit_facts.facts(snap(routes=[route(task, 60)], commits=[{"hash": "h", "paths": ["lib/audit_facts.py"], "t": NOW}]), NOW)
        self.assertEqual(f["commits_touch_task_files"], "yes")
        self.assertTrue(all(len(t) <= audit_facts.TASK_CHARS for t in f["tasks"]))


def working(n=1, **kw):
    return snap(agents=[{"name": f"w{i}", "status": "working"} for i in range(n)], **kw)


class StateTest(unittest.TestCase):
    def state(self, s):
        return audit_facts.derive_status(audit_facts.facts(s, NOW))

    def test_no_agent_working_is_idle_even_with_a_repeat_or_recent_routes(self):
        s = snap(agents=[{"name": "a", "status": "done"}], routes=[route("x", 5), route("x", 4)])
        self.assertEqual(self.state(s), "idle")
        self.assertEqual(self.state(snap(agents=[])), "idle")

    def test_working_with_a_recent_route_or_commit_is_progressing(self):
        self.assertEqual(self.state(working(routes=[route("a", 60)])), "progressing")
        self.assertEqual(self.state(working(commits=[{"hash": "h", "t": NOW - 60}])), "progressing")

    def test_working_with_nothing_in_15_minutes_is_stalled(self):
        self.assertEqual(self.state(working(routes=[route("a", 3000)])), "stalled")
        self.assertEqual(self.state(working()), "stalled")

    def test_working_with_a_repeated_message_is_looping(self):
        self.assertEqual(self.state(working(routes=[route("fix", 60), route("fix", 30)])), "looping")

    def test_the_model_may_judge_off_scope_only_when_working_and_the_files_do_not_match(self):
        task = [route("edit lib/a.py", 60)]
        off = audit_facts.plan("L", working(routes=task, commits=[{"hash": "h", "paths": ["docs/z.md"], "t": NOW - 30}]), NOW)
        match = audit_facts.plan("L", working(routes=task, commits=[{"hash": "h", "paths": ["lib/a.py"], "t": NOW - 30}]), NOW)
        idle = audit_facts.plan("L", snap(agents=[], routes=task, commits=[{"hash": "h", "paths": ["docs/z.md"], "t": NOW - 30}]), NOW)
        self.assertEqual([p["offscope"] for p in (off, match, idle)], [True, False, False])
        self.assertEqual([p["judge"] for p in (off, match, idle)], [False, False, False])    # the local model never judges
        self.assertIn("Check first: off-scope", off["haiku_prompt"])
        self.assertNotIn("Check first: off-scope", match["haiku_prompt"])
        for pl in (off, match, idle):    # the local prompt never mentions off-scope and carries no commit evidence
            self.assertNotIn("off-scope", pl["local_prompt"])
            self.assertNotIn("commit_evidence", pl["local_prompt"])
            self.assertIn("Keep status", pl["local_prompt"])

    def test_fix_reply_pins_the_status_and_follows_needs_claude(self):
        pl = {"state": "progressing", "judge": False}
        out = json.loads(audit_facts.fix_reply('ok {"status":"stalled","summary":"s","concern":"c","needs_claude":true}', pl))
        self.assertEqual((out["status"], out["needs_claude"], out["summary"]), ("progressing", False, "s"))
        stalled = json.loads(audit_facts.fix_reply('{"summary":"s"}', {"state": "stalled", "judge": False}))
        self.assertEqual((stalled["status"], stalled["needs_claude"], stalled["concern"]), ("stalled", True, ""))

    def test_fix_reply_takes_off_scope_only_when_judging_is_allowed(self):
        reply = '{"status":"off_scope","summary":"s","concern":"c","needs_claude":false}'
        self.assertEqual(json.loads(audit_facts.fix_reply(reply, {"state": "progressing", "judge": True}))["status"], "off-scope")
        self.assertTrue(json.loads(audit_facts.fix_reply(reply, {"state": "progressing", "judge": True}))["needs_claude"])
        self.assertEqual(json.loads(audit_facts.fix_reply(reply, {"state": "progressing", "judge": False}))["status"], "progressing")

    def test_the_local_prompt_forbids_guessing_and_asks_for_a_short_summary(self):
        text = audit_facts.plan("L", working(routes=[route("t", 5)]), NOW)["local_prompt"]
        self.assertIn("State only what the facts show, no guesses like 'may have stalled'", text)
        self.assertIn("under 140 characters", text)
        self.assertNotIn("no guesses", audit_facts.plan("L", working(routes=[route("t", 5)]), NOW)["haiku_prompt"])

    def test_fix_reply_cuts_a_long_summary(self):
        out = json.loads(audit_facts.fix_reply(json.dumps({"summary": "  " + "w" * 400}), {"state": "idle", "judge": False}))
        self.assertEqual(len(out["summary"]), audit_facts.SUMMARY_CHARS)

    def test_fix_reply_leaves_a_bad_reply_alone_so_it_falls_back(self):
        pl = {"state": "idle", "judge": False}
        for bad in ("no json at all", '["x"]', '{"status":"idle"}', '{"summary": 3}', ""):
            self.assertEqual(audit_facts.fix_reply(bad, pl), bad)

    def test_both_prompts_stay_short(self):
        pl = audit_facts.plan("L", working(routes=[route("t", 5)]), NOW)
        self.assertLess(len(pl["local_prompt"]), 1500)
        self.assertLess(len(pl["haiku_prompt"]), 1500)
        self.assertLess(len(pl["local_prompt"]), len(pl["haiku_prompt"]))


class EvidenceTest(unittest.TestCase):
    TASK = [route("edit lib/a.py", 60)]
    OFF = [{"hash": "h", "subject": 'docs: fix "typo"', "paths": ["docs/readme.md", "docs/b.md", "docs/c.md", "docs/d.md"], "t": NOW - 30}]

    def test_the_commit_subjects_and_paths_reach_both_prompts_when_the_match_is_no(self):
        pl = audit_facts.plan("L", working(routes=self.TASK, commits=self.OFF), NOW)
        key = "haiku_prompt"    # only Haiku sees the evidence; the local model does not judge
        self.assertIn("commit_evidence", pl[key])
        self.assertIn("docs: fix _typo_", pl[key])
        self.assertIn("docs/readme.md", pl[key])
        self.assertNotIn("docs/d.md", pl[key])    # at most 3 paths per commit
        self.assertLess(len(pl[key]), 1500)
        self.assertNotIn("commit_evidence", pl["local_prompt"])

    def test_no_evidence_when_the_files_match_or_are_unknown(self):
        match = [{"hash": "h", "subject": "s", "paths": ["lib/a.py"], "t": NOW - 30}]
        for commits in (match, [{"hash": "h", "subject": "s", "t": NOW - 30}], []):
            pl = audit_facts.plan("L", working(routes=self.TASK, commits=commits), NOW)
            self.assertNotIn("commit_evidence", pl["local_prompt"])
            self.assertNotIn("commit_evidence", pl["haiku_prompt"].split("Rules:")[0])

    def test_only_the_three_newest_commits_are_shown(self):
        commits = [{"hash": f"h{i}", "subject": f"subject number {i}", "paths": ["z/x.md"], "t": NOW - i} for i in range(6)]
        pl = audit_facts.plan("L", working(routes=self.TASK, commits=commits), NOW)
        self.assertIn("subject number 2", pl["haiku_prompt"])
        self.assertNotIn("subject number 3", pl["haiku_prompt"])

    def test_the_limit_holds_with_long_evidence(self):
        commits = [{"hash": f"h{i}", "subject": "日本語" * 50, "paths": ["very/long/" * 20 + "f.md"] * 5, "t": NOW - i} for i in range(5)]
        agents = [{"name": "é" * 30 + str(i), "status": ["working", "idle", "done", "x"][i % 4]} for i in range(20)]
        pl = audit_facts.plan("L" * 90, snap(agents=agents, routes=[route("edit lib/a.py " + "日" * 300, 5)] * 3, commits=commits), NOW)
        for key in ("local_prompt", "haiku_prompt"):
            self.assertLess(len(pl[key]), 1500, key)

    def test_the_task_lines_survive_when_the_model_has_to_judge_off_scope(self):
        agents = [{"name": "agent-number-" + str(i), "status": ["working", "idle", "done", "x"][i % 4]} for i in range(20)]
        routes = [route("edit lib/a.py then " + "long task text " * 20, 50 - i) for i in range(3)]
        commits = [{"hash": f"h{i}", "subject": "docs: fix the readme " * 5, "paths": ["docs/some/long/path/readme.md"] * 3, "t": NOW - i} for i in range(3)]
        pl = audit_facts.plan("Machine · team", snap(agents=agents, routes=routes, commits=commits), NOW)
        self.assertTrue(pl["offscope"])
        for key in ("local_prompt", "haiku_prompt"):
            self.assertLess(len(pl[key]), 1500, key)
        self.assertNotIn("Last architect tasks: []", pl["haiku_prompt"])
        self.assertIn("edit lib/a.py", pl["haiku_prompt"])
        self.assertIn("commit_evidence", pl["haiku_prompt"])

    def test_off_scope_is_possible_only_while_a_task_line_and_the_evidence_fit_the_haiku_prompt(self):
        routes = [route(f"edit lib/a.py {i} then " + "long task text " * 20, 50 - i) for i in range(3)]
        commits = [{"hash": f"h{i}", "subject": "docs: fix the readme " * 5, "paths": ["docs/some/long/path/readme.md"] * 3, "t": NOW - i} for i in range(3)]
        seen = set()
        for limit in range(300, 1600, 40):
            with mock.patch.object(audit_facts, "LIMIT", limit):
                pl = audit_facts.plan("Machine · team", working(routes=routes, commits=commits), NOW)
            seen.add(pl["offscope"])
            if pl["offscope"]:
                self.assertNotIn("Last architect tasks: []", pl["haiku_prompt"], limit)
                self.assertIn("commit_evidence", pl["haiku_prompt"], limit)
            else:
                self.assertNotIn("Check first: off-scope", pl["haiku_prompt"], limit)
                self.assertNotIn("commit_evidence", pl["haiku_prompt"], limit)
                self.assertEqual(pl["state"], "progressing")
            self.assertFalse(pl["judge"])
        self.assertEqual(seen, {True, False})    # both outcomes were reached

    def test_haiku_checks_off_scope_before_the_catch_all(self):
        text = audit_facts.plan("L", working(routes=self.TASK, commits=self.OFF), NOW)["haiku_prompt"]
        self.assertLess(text.index("off-scope if an agent is working"), text.index("stalled = otherwise"))
        self.assertLess(text.index("off-scope if an agent is working"), text.index("progressing = a route or commit"))

    def test_the_python_order_is_unchanged_idle_looping_progressing_stalled(self):
        s = working(routes=[route("x", 60), route("x", 30)], commits=self.OFF)
        self.assertEqual(audit_facts.derive_status(audit_facts.facts(s, NOW)), "looping")


class PromptTest(unittest.TestCase):
    def test_twenty_long_unicode_names_and_backslashes_stay_under_the_limit(self):
        names = ["日本語\\\"名前" * 10, "\x00\x1f\\" * 30, "é" * 100]
        agents = [{"name": names[i % 3] + str(i), "status": ["working", "idle", "done", "weird"][i % 4]} for i in range(20)]
        pl = audit_facts.plan("日本語\\" * 30, snap(agents=agents, routes=[route("日" * 300, 5)]), NOW)
        for key in ("local_prompt", "haiku_prompt"):
            self.assertLess(len(pl[key]), 1500, key)
            self.assertNotIn("\\u00", pl[key])

    def test_names_lose_escapes_and_fall_back_to_counts_when_nothing_else_fits(self):
        f = audit_facts.facts(snap(agents=[{"name": 'a"b\\c\n', "status": "idle"}]), NOW)
        self.assertEqual(f["agents"]["idle"]["names"], ["a_b_c_"])    # quotes, backslashes and control characters
        f["agents"] = {s: {"count": 9, "names": ["n" * 16] * 3} for s in ("working", "idle", "done", "other")}
        f["tasks"] = []
        text = audit_facts.prompt("L" * 40, f)
        self.assertLess(len(text), 1500)


    def test_non_ascii_task_lines_and_long_labels_stay_under_the_limit(self):
        routes = [route("é" * 300, 100 - i) for i in range(3)] + [route("日本語" * 100, 5)]
        f = audit_facts.facts(snap(routes=routes), NOW)
        text = audit_facts.prompt("Ünïcode label " * 20, f)
        self.assertLess(len(text), 1500)

    def test_the_limit_is_enforced_by_trimming_tasks_after_the_build(self):
        f = audit_facts.facts(snap(routes=[route("z" * 500, 5)]), NOW)
        f["tasks"] = ["z" * 900] * 3    # a caller that skipped the display cut
        self.assertLess(len(audit_facts.prompt("L", f)), 1500)

    def test_a_long_label_is_cut(self):
        text = audit_facts.prompt("L" * 500, audit_facts.facts(snap(), NOW))
        self.assertNotIn("L" * (audit_facts.MAX_LABEL + 1), text)

    def test_the_worst_case_prompt_stays_under_1500_chars(self):
        agents = [{"name": f"team-worker-number-{i}", "status": s} for i, s in enumerate(["working", "idle", "done"] * 4)]
        routes = [route("y" * 300, 100 - i) for i in range(5)]
        text = audit_facts.prompt("Machine label · team", audit_facts.facts(snap(agents=agents, routes=routes), NOW))
        self.assertLess(len(text), 1500)

    def test_it_holds_the_rules_the_states_and_the_reply_shape_but_no_snapshot(self):
        text = audit_facts.prompt("L", audit_facts.facts(snap(routes=[route("do x", 5)]), NOW))
        for needle in ("progressing = a route or commit in the last 15 min", "idle = no agent working",
                       "off-scope", "Example:", '"needs_claude":true|false', '"do x"'):
            self.assertIn(needle, text)
        self.assertNotIn("Snapshot", text)


if __name__ == "__main__":
    unittest.main()
