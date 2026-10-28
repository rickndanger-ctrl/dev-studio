"""bin/re-audit wired to lib/audit_review, and the auditor block of `localq report` / the viewer tooltip line.
Fake runners only: no model, no claude call, no socket. Run from the repo root:  python3 -m unittest test.test_audit_wiring
"""
import importlib.machinery, importlib.util, json, os, sys, tempfile, time, types, unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import audit_review, localq_report

TMP = tempfile.mkdtemp(prefix="audit-wiring-")
CONFIG = os.path.join(TMP, "machines.json")
with open(CONFIG, "w") as f:
    json.dump({"state_dir": os.path.join(TMP, "state"), "audit": {"app": "Fake App", "data_dir": os.path.join(TMP, "data"), "project": "Fake"},
               "machines": [{"id": "lap", "role": "hub", "ssh": []}]}, f)


def load_audit():
    loader = importlib.machinery.SourceFileLoader("re_audit_under_test", os.path.join(REPO, "bin", "re-audit"))
    spec = importlib.util.spec_from_loader("re_audit_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"RE_STUDIO_CONFIG": CONFIG}), mock.patch.object(sys, "argv", ["re-audit"]):
        loader.exec_module(mod)
    return mod


RA = load_audit()
LAPTOP = {"status": "progressing", "summary": "moving", "concern": "", "needs_claude": False, "next_check_minutes": 10}
STUDIO = {"status": "idle", "summary": "quiet", "concern": "", "needs_claude": False}


def gate_stub(result, calls):
    m = types.ModuleType("localq_gate")
    m.check = lambda mid: (calls.append(mid), result)[1]
    return m


class AskTest(unittest.TestCase):
    def setUp(self):
        RA.STATS.clear()
        RA.GATE.clear()
        RA.PASS.update(asked=0, reachable=0)
        self.haiku_calls = []
        p = mock.patch.object(RA, "claude_json", side_effect=lambda prompt: (self.haiku_calls.append(prompt), dict(LAPTOP, cost_usd=0.01))[1])
        p.start()
        self.addCleanup(p.stop)
        # never the 1-in-10 sample
        r = mock.patch.object(audit_review.random, "random", return_value=0.99)
        r.start()
        self.addCleanup(r.stop)

    def hour(self):
        return RA.STATS[time.strftime("%Y-%m-%dT%H")]

    def test_a_valid_local_verdict_costs_no_haiku_call_and_keeps_todays_keys(self):
        with mock.patch.object(RA, "local_text", return_value="sure: " + json.dumps(LAPTOP)):
            v = RA.ask_haiku({"tasks": []}, [])
        self.assertEqual(self.haiku_calls, [])
        self.assertEqual(set(v), set(LAPTOP) | {"cost_usd"})
        self.assertEqual(v["cost_usd"], 0)
        self.assertEqual((self.hour()["local"], self.hour().get("fallback", 0)), (1, 0))

    def test_an_invalid_local_verdict_falls_back_to_haiku(self):
        with mock.patch.object(RA, "local_text", return_value="I think it is fine"):
            v = RA.ask_haiku({"tasks": []}, [])
        self.assertEqual(len(self.haiku_calls), 1)
        self.assertEqual(v["cost_usd"], 0.01)
        self.assertEqual((self.hour()["fallback"], self.hour()["haiku"]), (1, 1))

    def test_a_status_outside_the_allowed_set_falls_back(self):
        with mock.patch.object(RA, "local_text", return_value=json.dumps(dict(LAPTOP, status="great"))):
            RA.ask_haiku({"tasks": []}, [])
        self.assertEqual(len(self.haiku_calls), 1)

    def test_the_gate_is_checked_once_per_pass_and_a_closed_gate_means_haiku(self):
        calls = []
        with mock.patch.dict(sys.modules, {"localq_gate": gate_stub((False, "memory low"), calls)}):
            RA.ask_haiku({"tasks": []}, [])
            RA.ask_haiku({"tasks": []}, [])
        self.assertEqual(calls, ["lap"])
        self.assertEqual(len(self.haiku_calls), 2)
        self.assertEqual(self.hour()["local_fail"], {"gate: memory low": 2})

    def test_an_open_gate_runs_the_local_model_with_the_small_reply_limit(self):
        calls, runs = [], []
        models = types.ModuleType("localq_models")
        models.run = lambda mid, prompt, max_chars, timeout=300: (runs.append((mid, max_chars, timeout)), {"text": json.dumps(LAPTOP)})[1]
        with mock.patch.dict(sys.modules, {"localq_gate": gate_stub((True, ""), calls), "localq_models": models}):
            RA.ask_haiku({"tasks": []}, [])
        self.assertEqual(runs, [("lap", 1200, RA.LOCAL_TIMEOUT)])
        self.assertEqual(self.haiku_calls, [])

    def test_the_timeout_comes_from_the_config_and_defaults_to_150(self):
        self.assertEqual(RA.LOCAL_TIMEOUT, 150)
        for value, expected in ((200, 200.0), ("90", 90.0), (0, 150), (-5, 150), ("soon", 150), (None, 150)):
            with mock.patch.object(RA.cfg, "get", side_effect=lambda key, default=None, v=value: v if key == "audit_local_timeout" else default):
                self.assertEqual(RA.local_timeout(), expected, value)

    def test_every_local_ask_logs_its_seconds_and_outcome(self):
        calls, logged = [], []
        models = types.ModuleType("localq_models")
        def run(mid, prompt, max_chars, timeout=300):
            if "boom" in prompt:
                raise RuntimeError("model down")
            return {"text": json.dumps(LAPTOP)}
        models.run = run
        with mock.patch.dict(sys.modules, {"localq_gate": gate_stub((True, ""), calls), "localq_models": models}), \
                mock.patch.object(RA, "append", side_effect=lambda name, line: logged.append((name, line))):
            RA.local_text("fine")
            with self.assertRaises(RuntimeError):
                RA.local_text("boom")
        self.assertEqual([n for n, _ in logged], ["audit.log", "audit.log"])
        self.assertRegex(logged[0][1], r"local ask ok \d+\.\ds \(limit 150s\)")
        self.assertRegex(logged[1][1], r"local ask failed \d+\.\ds")

    def test_a_studio_team_goes_through_review_too(self):
        with mock.patch.object(RA, "local_text", return_value=json.dumps(STUDIO)):
            v = RA.ask_studio("m1/alpha", "Box · alpha", {"routes": [], "project": "p"})
        self.assertEqual((v["status"], v["needs_claude"]), ("idle", False))
        self.assertEqual(self.haiku_calls, [])
        self.assertEqual(RA.PASS["asked"], 1)

    def test_a_failing_haiku_still_gives_the_unknown_verdict(self):
        RA.claude_json.side_effect = RuntimeError("no claude")
        with mock.patch.object(RA, "local_text", return_value="nope"):
            v = RA.ask_haiku({"tasks": []}, ["x"])
        self.assertEqual((v["status"], v["needs_claude"]), ("unknown", True))


class SampleConfigTest(unittest.TestCase):
    def test_audit_sample_comes_from_the_config_and_defaults_to_a_tenth(self):
        self.assertEqual(RA.SAMPLE, 0.1)
        for value, expected in ((0.5, 0.5), ("0.25", 0.25), (0, 0.0), (1, 1.0), (2, 0.1), (-1, 0.1), ("x", 0.1), (None, 0.1)):
            with mock.patch.object(RA.cfg, "get", side_effect=lambda key, default=None, v=value: v if key == "audit_sample" else default):
                self.assertEqual(RA.sample_rate(), expected, value)

    def test_the_rate_reaches_the_review(self):
        with mock.patch.object(RA, "SAMPLE", 0.7), mock.patch.object(audit_review, "review", return_value=(dict(STUDIO), "local")) as r:
            RA.review("p", ("idle",), STUDIO_KEYS_FOR_TEST)
        self.assertEqual(r.call_args.kwargs["sample"], 0.7)

    def test_the_studio_status_comes_from_code_and_the_model_only_writes_the_summary(self):
        snap = {"agents": [{"name": "a-main", "status": "working"}], "routes": [{"from": "a-architect", "text": "do x", "t": time.time() - 60}],
                "commits": [], "project": "p"}
        asked = []
        reply = json.dumps(dict(STUDIO, status="stalled", summary="Waiting.", needs_claude=True))   # the model picks the wrong state
        with mock.patch.object(RA, "local_text", side_effect=lambda p: (asked.append(p), reply)[1]), \
                mock.patch.object(audit_review.random, "random", return_value=0.99):
            v = RA.ask_studio("m1/alpha", "Box · alpha", snap)
        self.assertEqual((v["status"], v["needs_claude"], v["summary"]), ("progressing", False, "Waiting."))
        self.assertLess(len(asked[0]), 1500)
        self.assertIn("Code already decided its status: progressing", asked[0])

    def test_haiku_gets_the_rules_prompt_not_the_state_prompt(self):
        snap = {"agents": [{"name": "a-main", "status": "working"}], "routes": [], "commits": [], "project": "p"}
        seen = []
        with mock.patch.object(RA, "local_text", return_value="no json"), \
                mock.patch.object(RA, "claude_json", side_effect=lambda p: (seen.append(p), dict(STUDIO, status="stalled"))[1]):
            v = RA.ask_studio("m1/alpha", "Box · alpha", snap)
        self.assertEqual(v["status"], "stalled")
        self.assertIn("Rules:", seen[0])
        self.assertNotIn("Code already decided", seen[0])

    def test_a_sampled_haiku_status_is_compared_with_the_final_code_status(self):
        snap = {"agents": [], "routes": [], "commits": [], "project": "p"}
        RA.STATS.clear()
        with mock.patch.object(RA, "local_text", return_value=json.dumps(STUDIO)), \
                mock.patch.object(RA, "claude_json", return_value=dict(STUDIO, status="idle")), \
                mock.patch.object(audit_review.random, "random", return_value=0.0):
            RA.ask_studio("m1/alpha", "Box · alpha", snap)
        h = RA.STATS[time.strftime("%Y-%m-%dT%H")]
        self.assertEqual((h["sampled"], h["agreed"]), (1, 1))


STUDIO_KEYS_FOR_TEST = ("status", "summary", "concern", "needs_claude")


class SampleLogTest(unittest.TestCase):
    def setUp(self):
        RA.STATS.clear()
        RA.GATE.clear()
        RA.PASS.update(asked=0, reachable=0)
        self.logged = []
        for p in (mock.patch.object(RA, "claude_json", return_value=dict(STUDIO, status="stalled", cost_usd=0.01)),
                  mock.patch.object(RA, "local_text", return_value=json.dumps(STUDIO)),
                  mock.patch.object(RA, "append", side_effect=lambda name, line: self.logged.append((name, line))),
                  mock.patch.object(audit_review.random, "random", return_value=0.0)):
            p.start()
            self.addCleanup(p.stop)

    def test_a_sampled_studio_ask_logs_both_verdicts_with_its_team_key(self):
        v = RA.ask_studio("m1/alpha", "Box · alpha", {"routes": [], "project": "p"})
        self.assertEqual(v["status"], "stalled")    # Haiku won, but the pair was kept
        name, line = self.logged[-1]
        row = json.loads(line)
        self.assertEqual(name, "audit-samples.jsonl")
        self.assertEqual((row["key"], row["local"]["status"], row["haiku"]["status"]), ("m1/alpha", "idle", "stalled"))
        self.assertGreater(row["prompt_chars"], 100)
        self.assertRegex(row["time"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_the_laptop_ask_logs_under_its_own_key(self):
        with mock.patch.object(RA, "local_text", return_value=json.dumps(LAPTOP)), \
                mock.patch.object(RA, "claude_json", return_value=dict(LAPTOP, cost_usd=0)):
            RA.ask_haiku({"tasks": []}, [])
        self.assertEqual(json.loads(self.logged[-1][1])["key"], "laptop")

    def test_an_unsampled_ask_logs_no_pair(self):
        with mock.patch.object(audit_review.random, "random", return_value=0.99):
            RA.ask_studio("m1/alpha", "Box · alpha", {"routes": [], "project": "p"})
        self.assertEqual([n for n, _ in self.logged if n == "audit-samples.jsonl"], [])


class PassCountersTest(unittest.TestCase):
    def test_the_pass_counts_itself_and_the_skipped_teams_and_saves_the_stats_file(self):
        def body():
            RA.PASS.update(asked=1, reachable=4)    # four reachable teams, one asked: three skipped
        with mock.patch.object(RA, "pass_body", side_effect=body), mock.patch.object(RA, "STATS_FILE", os.path.join(TMP, "audit-stats-a.json")):
            RA.one_pass()
            RA.one_pass()
            with open(RA.STATS_FILE) as f:
                hour = json.load(f)[time.strftime("%Y-%m-%dT%H")]
        self.assertEqual((hour["passes"], hour["skipped"]), (2, 6))

    def test_the_stats_are_saved_even_when_the_pass_fails(self):
        with mock.patch.object(RA, "pass_body", side_effect=RuntimeError("boom")), \
                mock.patch.object(RA, "STATS_FILE", os.path.join(TMP, "audit-stats-b.json")):
            with self.assertRaises(RuntimeError):
                RA.one_pass()
            self.assertTrue(os.path.exists(RA.STATS_FILE))


class ConfirmTest(unittest.TestCase):
    SNAP = {"agents": [{"name": "a-main", "status": "working"}], "routes": [], "commits": [], "project": "p"}   # code says: stalled

    def setUp(self):
        RA.STATS.clear()
        RA.GATE.clear()
        RA.SOURCE.clear()
        RA.PASS.update(asked=0, reachable=0)
        self.logged = []
        self.haiku_calls = []
        for p in (mock.patch.object(RA, "append", side_effect=lambda name, line: self.logged.append((name, line))),
                  mock.patch.object(audit_review.random, "random", return_value=0.99),
                  mock.patch.object(RA, "local_text", return_value=json.dumps(dict(STUDIO, summary="Nothing moved."))),
                  mock.patch.object(RA, "claude_json", side_effect=self.haiku)):
            p.start()
            self.addCleanup(p.stop)
        self.haiku_status = "idle"

    def haiku(self, prompt):
        self.haiku_calls.append(prompt)
        return dict(STUDIO, status=self.haiku_status, summary="Haiku view.", cost_usd=0.01)

    def rows(self):
        return [json.loads(line) for name, line in self.logged if name == "audit-samples.jsonl"]

    def test_a_local_stalled_verdict_is_overturned_by_haiku_and_does_not_alert(self):
        v = RA.ask_studio("m2/studio", "Mini 2 · studio", self.SNAP)
        self.assertEqual((v["status"], v["needs_claude"], v["summary"]), ("idle", False, "Haiku view."))
        self.assertEqual(RA.SOURCE["m2/studio"], "confirm")
        self.assertIn("Rules:", self.haiku_calls[0])    # Haiku got the rules prompt
        self.assertNotIn("Code already decided", self.haiku_calls[0])
        row, = self.rows()
        self.assertEqual((row["kind"], row["key"], row["local"]["status"], row["haiku"]["status"]), ("confirm", "m2/studio", "stalled", "idle"))
        h = RA.STATS[time.strftime("%Y-%m-%dT%H")]
        self.assertEqual((h["confirmed"], h["overturned"]), (1, 1))

    def test_a_confirmed_alert_still_alerts(self):
        self.haiku_status = "stalled"
        v = RA.ask_studio("m2/studio", "Mini 2 · studio", self.SNAP)
        self.assertEqual((v["status"], v["needs_claude"], v["summary"]), ("stalled", True, "Nothing moved."))
        self.assertEqual(RA.STATS[time.strftime("%Y-%m-%dT%H")].get("overturned", 0), 0)

    def test_an_idle_team_is_not_confirmed(self):
        RA.ask_studio("m2/studio", "Mini 2 · studio", {"agents": [], "routes": [], "commits": []})
        self.assertEqual((self.haiku_calls, self.rows(), RA.SOURCE["m2/studio"]), ([], [], "local"))

    OFF = {"agents": [{"name": "a-main", "status": "working"}], "project": "p",
           "routes": [{"from": "a-architect", "text": "edit lib/a.py", "t": time.time() - 30}],
           "commits": [{"hash": "h", "subject": "test_privacy.py brand removal", "paths": ["test/test_privacy.py"], "t": time.time() - 20}]}

    def test_when_off_scope_is_possible_only_haiku_is_asked_and_the_local_model_is_not(self):
        self.haiku_status = "progressing"
        with mock.patch.object(RA, "local_text", side_effect=AssertionError("the local model must not be asked")):
            v = RA.ask_studio("m2/studio", "Mini 2 · studio", self.OFF)
        self.assertEqual((v["status"], v["needs_claude"], v["summary"]), ("progressing", False, "Haiku view."))
        self.assertEqual(RA.SOURCE["m2/studio"], "haiku-offscope")
        self.assertIn("Check first: off-scope", self.haiku_calls[0])    # the rules prompt with the evidence
        self.assertIn("commit_evidence", self.haiku_calls[0])
        h = RA.STATS[time.strftime("%Y-%m-%dT%H")]
        self.assertEqual((h["offscope"], h["haiku"], h.get("local", 0)), (1, 1, 0))

    def test_haiku_can_call_off_scope_and_that_alerts(self):
        self.haiku_status = "off-scope"
        v = RA.ask_studio("m2/studio", "Mini 2 · studio", self.OFF)
        self.assertEqual((v["status"], v["needs_claude"]), ("off-scope", True))

    def test_off_scope_cannot_come_from_a_sample_a_confirm_or_a_fallback_when_it_is_not_possible(self):
        idle = {"agents": [], "routes": [], "commits": [], "project": "p"}    # nobody working: off-scope is impossible
        self.haiku_status = "off-scope"
        with mock.patch.object(audit_review.random, "random", return_value=0.0):    # sampled
            v = RA.ask_studio("m2/studio", "Mini 2 · studio", idle)
        self.assertEqual((v["status"], v["needs_claude"]), ("idle", False))    # the invalid sample is ignored, the idle verdict stands
        h = RA.STATS[time.strftime("%Y-%m-%dT%H")]
        self.assertEqual((h["sample_failed"], h.get("sampled", 0)), (1, 0))
        with mock.patch.object(RA, "local_text", return_value="nonsense"):    # fallback
            with self.assertRaises(ValueError):
                RA.ask_studio("m2/studio", "Mini 2 · studio", idle)
        stalled = dict(self.SNAP)    # a working team whose local verdict is stalled: confirm
        with self.assertRaises(ValueError):
            RA.ask_studio("m2/studio", "Mini 2 · studio", stalled)
        for prompt in self.haiku_calls:
            self.assertNotIn("Check first: off-scope", prompt)
            self.assertNotIn(", off-scope.", prompt)    # the rules prompt does not even mention it

    def test_a_failed_or_malformed_off_scope_ask_raises_and_is_retried(self):
        for bad in (RuntimeError("no claude"), {"status": "off-scope"}):
            with self.subTest(bad=bad):
                RA.claude_json.side_effect = bad if isinstance(bad, Exception) else (lambda p, b=bad: b)
                with self.assertRaises(Exception):
                    RA.ask_studio("m2/studio", "Mini 2 · studio", self.OFF)

    def test_the_local_model_cannot_make_an_off_scope_call_even_if_it_says_so(self):
        # no commit-file mismatch here, so the local path runs; its off-scope answer is ignored by the code
        snap = dict(self.SNAP)
        with mock.patch.object(RA, "local_text", return_value=json.dumps(dict(STUDIO, status="off-scope", summary="Unrelated."))):
            v = RA.ask_studio("m2/studio", "Mini 2 · studio", snap)
        self.assertEqual(v["status"], "idle")    # Haiku confirm of the code's stalled state said idle; never off-scope
        self.assertNotEqual(v["status"], "off-scope")

    def test_a_failed_confirm_is_an_unanswered_ask_not_an_alert(self):
        RA.claude_json.side_effect = RuntimeError("no claude")
        with self.assertRaises(RuntimeError):
            RA.ask_studio("m2/studio", "Mini 2 · studio", self.SNAP)

    def test_a_sampled_local_alert_with_a_malformed_haiku_sample_does_not_alert(self):
        RA.claude_json.side_effect = lambda prompt: {"status": "stalled"}    # no summary, no concern: malformed
        with mock.patch.object(audit_review.random, "random", return_value=0.0):
            with self.assertRaises(ValueError):
                RA.ask_studio("m2/studio", "Mini 2 · studio", self.SNAP)
        h = RA.STATS[time.strftime("%Y-%m-%dT%H")]
        self.assertEqual((h["confirm_failed"], h["sample_failed"], h.get("sampled", 0)), (1, 1, 0))
        self.assertEqual(self.rows(), [])

    def test_the_sample_rows_carry_their_kind(self):
        with mock.patch.object(audit_review.random, "random", return_value=0.0):
            RA.ask_studio("m2/studio", "Mini 2 · studio", self.SNAP)
        self.assertEqual([r["kind"] for r in self.rows()], ["sample"])
        self.assertEqual(RA.SOURCE["m2/studio"], "local+sample")

    def test_the_audit_log_names_the_source_of_each_studio_verdict(self):
        snaps = {"m1": {"online": True, "project": "p", "teams": [{"team": "alpha", "agents": [{"name": "alpha-main", "status": "working"}],
                                                                       "routes": [], "commits": []}]}}
        with mock.patch.object(RA, "_load_snap", return_value=types.SimpleNamespace(everyone=lambda: snaps)):
            RA.studio_pass({})
        lines = [line for name, line in self.logged if name == "audit.log"]
        self.assertTrue(any(" confirm m1/alpha: " in line for line in lines), lines)
        self.assertFalse(any(" haiku m1/alpha: " in line for line in lines))


class AgingTest(unittest.TestCase):
    SNAPS = {"m1": {"online": True, "project": "p", "teams": [{"team": "alpha", "agents": [{"name": "alpha-main", "status": "working"}],
                                                                  "routes": [], "commits": []}]}}

    def setUp(self):
        RA.STATS.clear()
        RA.GATE.clear()
        RA.SOURCE.clear()
        RA.PASS.update(asked=0, reachable=0)
        self.alerts, self.haiku_calls = [], []
        self.haiku_status = "stalled"
        for p in (mock.patch.object(RA, "append", side_effect=lambda name, line: self.alerts.append(line) if name == "alerts.log" else None),
                  mock.patch.object(audit_review.random, "random", return_value=0.99),
                  mock.patch.object(RA, "local_text", return_value=json.dumps(dict(STUDIO, summary="Nothing moved."))),
                  mock.patch.object(RA, "claude_json", side_effect=self.haiku),
                  mock.patch.object(RA, "_load_snap", return_value=types.SimpleNamespace(everyone=lambda: self.SNAPS))):
            p.start()
            self.addCleanup(p.stop)

    def haiku(self, prompt):
        self.haiku_calls.append(prompt)
        return dict(STUDIO, status=self.haiku_status, summary="Haiku view.", cost_usd=0.01)

    def first(self):
        """A first pass that answers 'progressing' (the team just changed), then the entry aged by 20 minutes."""
        with mock.patch.object(RA, "ask_studio", return_value={"studio": "m1/alpha", "status": "progressing", "summary": "moving",
                                                                  "concern": "", "needs_claude": False}):
            studios, _w, _a = RA.studio_pass({})
        entry = studios["m1/alpha"]
        entry.update(changed_at=time.time() - 1200, asked_at=time.time() - 1200)
        return {"studios": {"m1/alpha": entry}}

    def test_an_aged_progressing_team_is_asked_again_and_a_stalled_verdict_is_confirmed_before_it_alerts(self):
        prev = self.first()
        studios, worst, alerted = RA.studio_pass(prev)
        e = studios["m1/alpha"]
        self.assertEqual((e["status"], e["needs_claude"], e["summary"]), ("stalled", True, "Nothing moved."))
        self.assertEqual(len(self.haiku_calls), 1)    # the confirm
        self.assertIn("Rules:", self.haiku_calls[0])
        self.assertTrue(any("ALERT" in a for a in self.alerts))

    def test_if_haiku_says_not_stalled_its_status_stays_and_the_team_is_not_asked_again_for_stall_after(self):
        prev = self.first()
        self.haiku_status = "progressing"
        studios, _w, _a = RA.studio_pass(prev)
        e = studios["m1/alpha"]
        self.assertEqual((e["status"], e["needs_claude"]), ("progressing", False))
        self.assertFalse(self.alerts)
        calls = len(self.haiku_calls)
        again, _w, _a = RA.studio_pass({"studios": {"m1/alpha": e}})    # next pass: unchanged, asked a moment ago
        self.assertEqual((len(self.haiku_calls), again["m1/alpha"]["status"]), (calls, "progressing"))
        e["asked_at"] = time.time() - 1200    # another STALL_AFTER passed since the ask
        RA.studio_pass({"studios": {"m1/alpha": e}})
        self.assertGreater(len(self.haiku_calls), calls)

    def test_a_failed_confirm_keeps_the_old_status_and_does_not_alert(self):
        prev = self.first()
        RA.claude_json.side_effect = RuntimeError("no claude")
        studios, _w, _a = RA.studio_pass(prev)
        e = studios["m1/alpha"]
        self.assertEqual((e["status"], e["needs_claude"], e["sig"]), ("progressing", False, None))
        self.assertFalse(self.alerts)

    def test_a_team_that_is_not_working_is_never_aged(self):
        prev = self.first()
        self.SNAPS["m1"]["teams"][0]["agents"] = [{"name": "alpha-main", "status": "idle"}]
        try:
            changed, _w, _a = RA.studio_pass(prev)    # the agent list changed, so this is an ordinary ask, not aging
        finally:
            self.SNAPS["m1"]["teams"][0]["agents"] = [{"name": "alpha-main", "status": "working"}]
        self.assertEqual(changed["m1/alpha"]["status"], "idle")


class FailedStudioAskTest(unittest.TestCase):
    SNAPS = {"m1": {"online": True, "project": "p", "teams": [{"team": "alpha", "agents": [{"name": "alpha-architect", "status": "idle"}],
                                                                  "routes": [], "commits": []}]}}

    def run_pass(self, prev, ask):
        snap_mod = types.SimpleNamespace(everyone=lambda: self.SNAPS)
        with mock.patch.object(RA, "_load_snap", return_value=snap_mod), mock.patch.object(RA, "ask_studio", side_effect=ask):
            studios, _worst, _alerted = RA.studio_pass(prev)
        return studios["m1/alpha"]

    def test_a_failed_review_is_asked_again_on_the_next_unchanged_pass(self):
        asks = []
        def failing(sid, label, snap):
            asks.append("fail")
            raise RuntimeError("both models down")
        def working(sid, label, snap):
            asks.append("ok")
            return {"studio": sid, "status": "idle", "summary": "quiet", "concern": "", "needs_claude": False}
        first = self.run_pass({}, failing)
        self.assertIn("review failed", first["summary"])
        self.assertIsNone(first["sig"])
        second = self.run_pass({"studios": {"m1/alpha": first}}, working)
        self.assertEqual(asks, ["fail", "ok"])
        self.assertEqual(second["summary"], "quiet")
        third = self.run_pass({"studios": {"m1/alpha": second}}, working)    # now unchanged and answered: no ask
        self.assertEqual(asks, ["fail", "ok"])


class ReportBlockTest(unittest.TestCase):
    STATS = {"2026-10-27T09": {"passes": 10, "skipped": 7, "local": 4, "fallback": 1, "haiku": 2, "sampled": 1, "agreed": 1, "local_seconds": 12},
             "2026-10-27T10": {"passes": 5, "skipped": 3, "local": 2, "fallback": 0, "haiku": 0, "local_seconds": 8},
             "2026-10-26T23": {"passes": 99, "local": 9}}

    def at(self, text):
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))

    def test_the_block_sums_the_window_and_derives_local_used_and_the_mean(self):
        a = localq_report.auditor_block(self.STATS, self.at("2026-10-27 09:30"))
        self.assertEqual((a["passes"], a["skipped"], a["local_used"], a["haiku"], a["fallback"], a["sampled"], a["agreed"]),
                         (15, 10, 5, 2, 1, 1, 1))
        # a sampled disagreement is a verdict Haiku gave, not the local model
        disagreed = localq_report.auditor_block({"2026-10-27T09": {"local": 5, "fallback": 1, "sampled": 2, "agreed": 0, "haiku": 3}}, None)
        self.assertEqual(disagreed["local_used"], 2)
        self.assertEqual(a["mean_local_seconds"], 3.3)

    def test_a_later_window_drops_the_earlier_hours_and_an_empty_one_is_none(self):
        self.assertEqual(localq_report.auditor_block(self.STATS, self.at("2026-10-27 10:00"))["passes"], 5)
        self.assertIsNone(localq_report.auditor_block(self.STATS, self.at("2026-10-28 00:00")))
        self.assertIsNone(localq_report.auditor_block({}, None))

    def test_the_render_has_one_auditor_line_and_stays_within_24_lines(self):
        block = localq_report.auditor_block(self.STATS, self.at("2026-10-27 09:00"))
        out = localq_report.render(localq_report.build([], None, 0, block))
        self.assertIn("auditor: 15 passes, 10 skipped, 5 local, 2 Haiku (1 fallback, 1 sampled), agreement 1/1, local 3.3s mean", out)
        self.assertLessEqual(len(out.splitlines()), 24)
        self.assertNotIn("auditor:", localq_report.render(localq_report.build([], None, 0)))

    def test_a_failed_confirm_is_not_a_local_verdict_used(self):
        stats = {"2026-10-27T09": {"local": 4, "confirmed": 3, "overturned": 1, "confirm_failed": 1, "haiku": 3}}
        a = localq_report.auditor_block(stats, None)
        self.assertEqual((a["confirm_failed"], a["local_used"]), (1, 2))
        self.assertIn("confirms 3 (1 overturned, 1 failed)", localq_report.render(localq_report.build([], None, 0, a)))

    def test_a_failed_sample_is_not_counted_as_sampled_so_it_never_looks_like_a_disagreement(self):
        # the reviewer counts sampled only for a usable answer: 2 usable samples (1 agreed), 1 failed one
        stats = {"2026-10-27T09": {"local": 5, "sampled": 2, "agreed": 1, "sample_failed": 1, "haiku": 3}}
        a = localq_report.auditor_block(stats, None)
        self.assertEqual(a["local_used"], 4)    # only the one real disagreement is Haiku's

    def test_a_failed_confirm_shows_even_when_no_confirm_succeeded(self):
        a = localq_report.auditor_block({"2026-10-27T09": {"local": 1, "sampled": 0, "confirm_failed": 1, "sample_failed": 1, "haiku": 1}}, None)  # a sampled alert whose check failed
        out = localq_report.render(localq_report.build([], None, 0, a))
        self.assertIn("confirms 0 (0 overturned, 1 failed, 1 samples failed)", out)

    def test_the_confirm_part_shows_when_any_of_its_counters_is_non_zero(self):
        for key in ("confirmed", "overturned", "confirm_failed", "sample_failed"):
            with self.subTest(key=key):
                a = localq_report.auditor_block({"2026-10-27T09": {"local": 2, key: 1, "passes": 1}}, None)
                self.assertIn("confirms", localq_report.render(localq_report.build([], None, 0, a)))
        quiet = localq_report.auditor_block({"2026-10-27T09": {"local": 2, "passes": 1}}, None)
        self.assertNotIn("confirms", localq_report.render(localq_report.build([], None, 0, quiet)))
        only_sample = localq_report.auditor_block({"2026-10-27T09": {"local": 2, "sample_failed": 2}}, None)
        self.assertIn("confirms 0 (0 overturned, 2 samples failed)", localq_report.render(localq_report.build([], None, 0, only_sample)))

    def test_off_scope_checks_show_in_the_report(self):
        a = localq_report.auditor_block({"2026-10-27T09": {"local": 2, "offscope": 3, "haiku": 3, "passes": 1}}, None)
        self.assertEqual((a["offscope"], a["local_used"]), (3, 2))
        self.assertIn("off-scope checks 3", localq_report.render(localq_report.build([], None, 0, a)))
        quiet = localq_report.auditor_block({"2026-10-27T09": {"local": 2, "passes": 1}}, None)
        self.assertNotIn("off-scope checks", localq_report.render(localq_report.build([], None, 0, quiet)))

    def test_confirms_are_summed_and_an_overturned_one_is_not_a_local_verdict(self):
        stats = {"2026-10-27T09": {"local": 6, "confirmed": 3, "overturned": 1, "haiku": 3, "passes": 1}}
        a = localq_report.auditor_block(stats, None)
        self.assertEqual((a["confirmed"], a["overturned"], a["local_used"]), (3, 1, 5))
        out = localq_report.render(localq_report.build([], None, 0, a))
        self.assertIn("confirms 3 (1 overturned)", out)
        self.assertNotIn("confirms", localq_report.render(localq_report.build([], None, 0, localq_report.auditor_block(self.STATS, None))))

    def test_the_tooltip_line_is_for_one_day(self):
        self.assertEqual(localq_report.auditor_line(self.STATS, "2026-10-27"),
                         "today: 5 local, 2 Haiku (1 fallback), 10 skipped, agreement 1/1")
        self.assertEqual(localq_report.auditor_line(self.STATS, "2026-01-01"), "")


if __name__ == "__main__":
    unittest.main()
