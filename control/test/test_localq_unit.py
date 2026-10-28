"""Socket-free tests for bin/localq using a recording runner stub."""
import contextlib, importlib, importlib.machinery, importlib.util, io, json, os, shutil, sys, tempfile, time, types, unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))


def load_cli():
    loader = importlib.machinery.SourceFileLoader("localq_cli", os.path.join(REPO, "bin", "localq"))
    spec = importlib.util.spec_from_loader("localq_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


CLI = load_cli()


class RunnerError(Exception):
    pass


def stub_models(calls, events, fail=(), sleep_first=0):
    m = types.ModuleType("localq_models")
    m.RunnerError = RunnerError

    def run(mid, prompt, max_chars, timeout=300):
        events.append("enter")
        calls.append({"mid": mid, "prompt": prompt, "max_chars": max_chars})
        try:
            if len(calls) == 1 and sleep_first:
                time.sleep(sleep_first)
            if len(calls) in fail:
                raise RunnerError("model refused")
            return {"text": f"answer {len(calls)}", "seconds": 1.5, "model": "fake-model"}
        finally:
            events.append("exit")
    m.run = run
    return m


def stub_gate():
    """The contract's gate, in memory: approvals plus LOCALQ_FAKE_GATE for check()."""
    g = types.ModuleType("localq_gate")
    g.approvals = {}

    def allow(mid, hours, who):
        g.approvals[mid] = {"who": who, "at": time.time(), "until": time.time() + hours * 3600}

    def approval(mid):
        a = g.approvals.get(mid)
        return a if a and a["until"] > time.time() else None

    def check(mid):
        fake = os.environ.get("LOCALQ_FAKE_GATE")
        if fake:
            with open(fake) as f:
                ok, reason = json.load(f).get(mid, [True, ""])
            return bool(ok), reason
        return (approval(mid) is not None, "no approval") if mid != "lap" else (True, "")
    g.allow, g.deny, g.approval, g.check = allow, lambda mid: g.approvals.pop(mid, None), approval, check
    return g


try:
    REAL_GATE = importlib.import_module("localq_gate")
except ImportError:
    REAL_GATE = None


class LocalqTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="localq-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = os.path.join(self.tmp, "state")
        # a fixture studio project: the laptop's mail lives in its .studio, never the real repo's
        self.proj = os.path.join(self.tmp, "proj")
        self.mail = os.path.join(self.proj, ".studio", "mail", "delivered")
        os.makedirs(self.mail)
        cwd = os.getcwd()
        os.chdir(self.proj)
        self.addCleanup(os.chdir, cwd)
        conf = {"state_dir": self.state, "machines": [
            {"id": "lap", "label": "Laptop", "role": "hub", "ssh": [], "project": self.proj},
            {"id": "m1", "label": "Mini 1", "role": "studio", "ssh": ["mini1.local"]},
            {"id": "m3", "label": "Mini 3", "role": "studio", "enabled": False, "ssh": []}]}
        self.conf = os.path.join(self.tmp, "machines.json")
        with open(self.conf, "w") as f:
            json.dump(conf, f)
        env = mock.patch.dict(os.environ, {"RE_STUDIO_CONFIG": self.conf})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LOCALQ_FAKE_GATE", None)
        self.calls, self.events = [], []
        self.models = stub_models(self.calls, self.events)
        self.gate = REAL_GATE or stub_gate()
        mods = mock.patch.dict(sys.modules, {"localq_models": self.models, "localq_gate": self.gate})
        mods.start()
        self.addCleanup(mods.stop)

    # helpers
    def fake_gate(self, **answers):
        path = os.path.join(self.tmp, "gate.json")
        with open(path, "w") as f:
            json.dump({k: list(v) for k, v in answers.items()}, f)
        os.environ["LOCALQ_FAKE_GATE"] = path

    def job(self, jid="slug-1", **kw):
        spec = {"id": jid, "kind": "draft", "prompt": "write a haiku", "inputs": "about tests", "max_chars": 400}
        spec.update(kw)
        path = os.path.join(self.tmp, jid.replace("/", "_") + ".job.json")
        with open(path, "w") as f:
            json.dump(spec, f)
        return path

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = CLI.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def load(self, mid, jid):
        with open(os.path.join(self.state, "localq", "jobs", mid, jid + ".json")) as f:
            return json.load(f)

    # add
    def test_add_queues_a_job_with_output_under_state_localq_out(self):
        rc, out, err = self.cli("add", "lap", self.job())
        self.assertEqual(rc, 0, err)
        j = self.load("lap", "slug-1")
        self.assertEqual(j["status"], "queued")
        self.assertEqual(j["chars_in"], len("write a haiku") + len("about tests"))
        self.assertEqual(os.path.realpath(j["output"]), os.path.realpath(os.path.join(self.state, "localq", "out", "slug-1.md")))

    def test_add_maps_a_state_relative_output(self):
        rc, _, err = self.cli("add", "lap", self.job(output="state/localq/out/mine.md"))
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.load("lap", "slug-1")["output"].endswith(os.path.join("localq", "out", "mine.md")))

    def test_add_refuses_what_the_contract_refuses(self):
        cases = [("too big", self.job("big", prompt="x" * 6000, inputs="y" * 200)),
                 ("kind", self.job("k", kind="poem")),
                 ("outside output", self.job("o1", output=os.path.join(self.tmp, "elsewhere.md"))),
                 ("escape", self.job("o2", output="state/localq/out/../../x.md")),
                 ("slash id", self.job("a/b")),
                 ("no prompt", self.job("np", prompt=" "))]
        for name, path in cases:
            rc, _, err = self.cli("add", "lap", path)
            self.assertEqual(rc, 2, name)
            self.assertIn("localq:", err, name)
        self.assertEqual(self.cli("add", "lap", self.job("fits", prompt="x" * 6000, inputs="y" * 144))[0], 0)   # exactly 6144

    def test_add_refuses_a_reused_id_even_on_another_machine_and_a_disabled_machine(self):
        self.assertEqual(self.cli("add", "lap", self.job())[0], 0)
        rc, _, err = self.cli("add", "m1", self.job())
        self.assertEqual(rc, 2)
        self.assertIn("already used", err)
        self.assertEqual(self.cli("add", "m3", self.job("other"))[0], 2)
        self.assertEqual(self.cli("add", "nope", self.job("other"))[0], 2)

    # the gate comes first
    def test_run_on_a_mini_without_approval_never_reaches_the_runner(self):
        self.fake_gate(m1=(False, "no approval for m1"))
        self.cli("add", "m1", self.job("a"))
        self.cli("add", "m1", self.job("b"))
        rc, _, err = self.cli("run", "m1")
        self.assertEqual(rc, 3)
        self.assertIn("approval", err)
        self.assertEqual(self.calls, [])                       # the runner was never called
        for jid in ("a", "b"):
            j = self.load("m1", jid)
            self.assertEqual(j["status"], "blocked")
            self.assertIn("approval", j["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "localq", "out", "a.md")))

    @unittest.skipUnless(REAL_GATE, "real lib/localq_gate not present")
    def test_run_on_a_mini_with_the_real_gate_and_no_approval_never_reaches_the_runner(self):
        self.cli("add", "m1", self.job("a"))
        rc, _, _ = self.cli("run", "m1")
        self.assertEqual(rc, 3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.load("m1", "a")["status"], "blocked")

    def test_blocked_jobs_run_once_the_gate_opens(self):
        self.fake_gate(lap=(False, "memory low"))
        self.cli("add", "lap", self.job("a"))
        self.assertEqual(self.cli("run", "lap")[0], 3)
        self.fake_gate(lap=(True, ""))
        rc, _, err = self.cli("run", "lap")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.load("lap", "a")["status"], "done")

    def test_approval_expiring_during_a_run_blocks_the_rest(self):
        self.fake_gate(m1=(True, ""))                          # the fake gate never bypasses the approval itself
        self.gate.allow("m1", 1 / 3600, "tester")              # a real approval, one second long
        models = stub_models(self.calls, self.events, sleep_first=1.3)   # the first job outlasts it
        with mock.patch.dict(sys.modules, {"localq_models": models}):
            self.cli("add", "m1", self.job("a"))
            time.sleep(0.01)
            self.cli("add", "m1", self.job("b"))
            rc, _, err = self.cli("run", "m1")
        self.assertEqual(rc, 3)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.load("m1", "a")["status"], "done")
        self.assertEqual(self.load("m1", "b")["status"], "blocked")
        self.assertIn("expired", self.load("m1", "b")["reason"])

    # run
    def test_run_works_through_the_queue_one_job_at_a_time(self):
        self.fake_gate(lap=(True, ""))
        self.cli("add", "lap", self.job("a"))
        time.sleep(0.01)
        self.cli("add", "lap", self.job("b", prompt="second"))
        rc, out, err = self.cli("run", "lap")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.events, ["enter", "exit", "enter", "exit"])
        self.assertIn("write a haiku", self.calls[0]["prompt"])
        self.assertIn("about tests", self.calls[0]["prompt"])
        self.assertEqual(self.calls[0]["max_chars"], 400)
        a = self.load("lap", "a")
        self.assertEqual((a["status"], a["chars_out"], a["model"], a["seconds"]), ("done", len("answer 1"), "fake-model", 1.5))
        with open(a["output"]) as f:
            self.assertEqual(f.read().strip(), "answer 1")
        self.assertEqual(self.load("lap", "b")["status"], "done")

    def test_a_failed_job_records_the_reason_and_the_next_one_still_runs(self):
        self.fake_gate(lap=(True, ""))
        models = stub_models(self.calls, self.events, fail=(1,))
        with mock.patch.dict(sys.modules, {"localq_models": models}):
            self.cli("add", "lap", self.job("a"))
            time.sleep(0.01)
            self.cli("add", "lap", self.job("b"))
            rc, _, err = self.cli("run", "lap")
        self.assertEqual(rc, 1)
        a = self.load("lap", "a")
        self.assertEqual((a["status"], a["reason"]), ("failed", "model refused"))
        self.assertEqual(self.load("lap", "b")["status"], "done")

    def test_nothing_queued(self):
        rc, out, _ = self.cli("run", "lap")
        self.assertEqual(rc, 0)
        self.assertIn("nothing queued", out)

    # allow / deny
    def test_allow_and_deny_go_through_the_gate(self):
        rc, out, err = self.cli("allow", "m1", "2")
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.gate.approval("m1"))
        self.assertEqual(self.cli("deny", "m1")[0], 0)
        self.assertIsNone(self.gate.approval("m1"))

    def test_allow_refuses_the_hub_and_silly_hours(self):
        self.assertEqual(self.cli("allow", "lap", "1")[0], 2)
        self.assertEqual(self.cli("allow", "m1", "0")[0], 2)
        self.assertEqual(self.cli("allow", "m1", "100")[0], 2)
        self.assertEqual(self.cli("allow", "m1", "soon")[0], 2)

    # results, review, stats
    def finished_job(self, jid="a"):
        self.fake_gate(lap=(True, ""))
        self.cli("add", "lap", self.job(jid))
        self.assertEqual(self.cli("run", "lap")[0], 0)

    def test_results_pending_lists_done_jobs_not_yet_reviewed(self):
        self.finished_job("a")
        self.cli("add", "lap", self.job("queued-only"))
        _, out, _ = self.cli("results", "--pending")
        self.assertIn("a ", out)
        self.assertNotIn("queued-only", out)
        _, out, _ = self.cli("results")
        self.assertIn("queued-only", out)
        self.assertEqual(self.cli("review", "a", "accept")[0], 0)
        _, out, _ = self.cli("results", "--pending")
        self.assertIn("no pending results", out)
        rows = json.loads(self.cli("results", "--json")[1])
        self.assertEqual({r["id"]: (r["status"], (r["review"] or {}).get("verdict")) for r in rows},
                         {"a": ("done", "accept"), "queued-only": ("queued", None)})

    def test_review_needs_a_done_job_and_a_known_verdict(self):
        self.cli("add", "lap", self.job("q"))
        self.assertEqual(self.cli("review", "q", "accept")[0], 2)
        self.assertEqual(self.cli("review", "ghost", "accept")[0], 2)
        self.finished_job("a")
        self.assertEqual(self.cli("review", "a", "maybe")[0], 2)

    def test_review_records_who_and_note_and_stats_count_verdicts(self):
        self.finished_job("a")
        self.assertEqual(self.cli("review", "a", "fix", "tone", "is", "off", "--by", "architect")[0], 0)
        r = self.load("lap", "a")["review"]
        self.assertEqual((r["verdict"], r["by"], r["note"]), ("fix", "architect", "tone is off"))
        self.cli("review", "a", "accept")                      # a second verdict replaces the first
        with open(os.path.join(self.state, "localq", "stats.json")) as f:
            stats = json.load(f)
        t = stats["totals"]["lap"]
        self.assertEqual((t["jobs"], t["accepted"], t["fixed"], t["rejected"]), (1, 1, 0, 0))
        self.assertEqual(t["chars_out"], len("answer 1"))
        self.assertEqual(self.load("lap", "a")["review"]["by"], "haiku")
        day = time.strftime("%Y-%m-%d")
        self.assertEqual(stats["days"][day]["lap"]["jobs"], 1)

    def write_legacy_stats(self, jobs=2, chars_out=10):
        """The stats file as local-more wrote it: directly under state/, with one machine's totals."""
        path = os.path.join(self.state, "localq-stats.json")
        os.makedirs(self.state, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"days": {}, "totals": {"lap": {"jobs": jobs, "chars_out": chars_out}}}, f)
        return path

    def test_first_write_moves_the_old_stats_file_once_and_keeps_the_totals(self):
        legacy = self.write_legacy_stats(jobs=2, chars_out=10)
        CLI.bump(CLI.today(), "lap", jobs=1, chars_out=5)
        self.assertFalse(os.path.exists(legacy))
        with open(os.path.join(self.state, "localq", "stats.json")) as f:
            t = json.load(f)["totals"]["lap"]
        self.assertEqual((t["jobs"], t["chars_out"]), (3, 15))
        CLI.bump(CLI.today(), "lap", jobs=1)                  # a later write only adds to the new file
        self.assertEqual(CLI.read_stats()["totals"]["lap"]["jobs"], 4)

    def test_a_refused_move_keeps_the_old_file_and_still_carries_the_totals(self):
        legacy = self.write_legacy_stats(jobs=2, chars_out=10)
        real_replace = os.replace

        def refuse_the_legacy_file(src, dst):
            if src == legacy:
                raise PermissionError("sandbox may write only localq/")
            return real_replace(src, dst)

        with mock.patch.object(os, "replace", side_effect=refuse_the_legacy_file):
            CLI.bump(CLI.today(), "lap", jobs=1, chars_out=5)
        self.assertTrue(os.path.exists(legacy))
        self.assertEqual(CLI.read_stats()["totals"]["lap"]["jobs"], 3)
        with open(os.path.join(self.state, "localq", "stats.json")) as f:
            self.assertEqual(json.load(f)["totals"]["lap"]["chars_out"], 15)

    def test_readers_fall_back_to_the_old_file_until_the_new_one_exists(self):
        self.write_legacy_stats(jobs=2, chars_out=10)
        self.assertEqual(self.cli("stats")[0], 0)
        self.assertIn("lap: 2 jobs", self.cli("stats")[1])
        CLI.bump(CLI.today(), "lap", jobs=1)
        self.assertIn("lap: 3 jobs", self.cli("stats")[1])

    def test_stats_prints_today_and_total(self):
        self.finished_job("a")
        self.cli("review", "a", "reject")
        rc, out, _ = self.cli("stats")
        self.assertEqual(rc, 0)
        self.assertIn("today", out)
        self.assertIn("lap: 1 jobs", out)
        self.assertIn("rejected 1", out)

    def write_mail(self, name, t, frm, to, text):
        with open(os.path.join(self.mail, name + ".json"), "w") as f:
            json.dump({"id": name, "t": t, "from": frm, "to": to, "text": text}, f)

    def test_stats_output_share_is_today_local_output_against_today_paid_mail(self):
        self.finished_job("a")                                  # local: 13 + 11 in, 8 out; output share counts the 8
        now = time.time()
        self.write_mail("1", now, "re-studio-architect", "re-studio-worker-1", "x" * 60)
        self.write_mail("2", now, "re-studio-worker-1", "re-studio-architect", "y" * 8)
        self.write_mail("3", now - 2 * 86400, "re-studio-architect", "re-studio-worker-1", "z" * 500)  # not today
        with open(os.path.join(self.mail, "broken.json"), "w") as f:
            f.write("{not json")
        rc, out, _ = self.cli("stats")
        self.assertEqual(rc, 0)
        self.assertIn("output share (estimate): 11% of today's text; local output 8 chars, paid 68 chars in 2 messages", out)
        self.assertIn("paid in by sender: re-studio-architect 60, re-studio-worker-1 8", out)
        self.assertIn("paid out by recipient: re-studio-architect 8, re-studio-worker-1 60", out)

    def test_stats_output_share_is_unknown_without_a_laptop_project(self):
        with open(self.conf) as f:
            conf = json.load(f)
        del conf["machines"][0]["project"]
        with open(self.conf, "w") as f:
            json.dump(conf, f)
        rc, out, _ = self.cli("stats")
        self.assertEqual(rc, 0)
        self.assertIn("output share (estimate): unknown, no laptop project in the config", out)

    def test_output_share_is_none_until_paid_text_is_known(self):
        import localq_share
        self.assertEqual(localq_share.share(30, None), {"output_chars": 30, "paid_chars": None, "pct": None})
        self.assertEqual(localq_share.share(0, {"chars": 0}), {"output_chars": 0, "paid_chars": 0, "pct": None})
        self.assertEqual(localq_share.share(30, {"chars": 70}), {"output_chars": 30, "paid_chars": 70, "pct": 30})

    def test_usage_errors_exit_2(self):
        for args in ([], ["frobnicate"], ["add"], ["run"], ["review", "x"], ["results", "--what"]):
            self.assertEqual(self.cli(*args)[0], 2, args)


    # draft shortcut: uses the in-memory gate and runner, never a model server
    def draft_input(self, text="task and interface"):
        path = os.path.join(self.tmp, "input.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_draft_each_kind_uses_its_prompt_and_only_runs_the_new_job(self):
        self.fake_gate(lap=(True, ""))
        self.cli("add", "lap", self.job("waiting"))
        for number, (kind, prompt) in enumerate(CLI.DRAFT_PROMPTS.items(), 1):
            with self.subTest(kind=kind):
                rc, out, err = self.cli("draft", kind, self.draft_input(), "--max-chars", "123")
                self.assertEqual(rc, 0, err)
                self.assertEqual(out, f"answer {number}\n")
                jid, machine = err.strip().split(" on ")
                self.assertEqual(machine, "lap")
                job = self.load("lap", jid)
                self.assertEqual((job["kind"], job["status"], job["reviewer"]), (kind, "done", "codex"))
                self.assertEqual(job["prompt"], prompt)
                self.assertIn(prompt, self.calls[-1]["prompt"])
                self.assertIn("task and interface", self.calls[-1]["prompt"])
                self.assertEqual((self.calls[-1]["mid"], self.calls[-1]["max_chars"]), ("lap", 123))
                self.assertIn(jid, self.cli("results", "--pending")[1])
        self.assertEqual(self.load("lap", "waiting")["status"], "queued")
        with open(os.path.join(self.state, "localq", "stats.json")) as f:
            self.assertEqual(json.load(f)["totals"]["lap"]["jobs"], len(CLI.DRAFT_PROMPTS))
        self.assertEqual(self.cli("review", jid, "accept")[0], 0)
        self.assertEqual(self.load("lap", jid)["review"]["by"], "codex")

    def test_draft_blocked_reports_reason_and_leaves_other_jobs_alone(self):
        self.fake_gate(lap=(False, "memory low"))
        self.cli("add", "lap", self.job("waiting"))
        rc, out, err = self.cli("draft", "commit", self.draft_input())
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("memory low", err)
        self.assertEqual([j["id"] for j in CLI.all_jobs()], ["waiting"])
        self.assertEqual(self.load("lap", "waiting")["status"], "queued")
        self.assertEqual(self.calls, [])

    def test_draft_failed_run_is_recorded_and_exits_one(self):
        self.fake_gate(lap=(True, ""))
        with mock.patch.dict(sys.modules, {"localq_models": stub_models(self.calls, self.events, fail=(1,))}):
            rc, out, err = self.cli("draft", "commit", self.draft_input())
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("model refused", err)
        self.assertEqual(self.load("lap", err.splitlines()[0].split(" on ")[0])["status"], "failed")

    def test_draft_refuses_bad_inputs_and_options_without_a_job(self):
        path = self.draft_input(" ")
        for args in (("commit", path), ("commit", path + ".missing"), ("m1", path),
                     ("commit", path, "--max-chars", "0"), ("commit", path, "--max-chars", "bad"),
                     ("commit", path, "--unknown", "2"), ("commit",)):
            with self.subTest(args=args):
                rc, out, err = self.cli("draft", *args)
                self.assertEqual((rc, out), (1, ""))
                self.assertIn("localq:", err)
        self.assertEqual(CLI.all_jobs(), [])
        self.assertEqual(self.calls, [])

    def test_draft_input_limit_includes_prompt(self):
        self.fake_gate(lap=(True, ""))
        limit = CLI.MAX_INPUT - len(CLI.DRAFT_PROMPTS["commit"])
        self.assertEqual(self.cli("draft", "commit", self.draft_input("x" * limit))[0], 0)
        rc, _, err = self.cli("draft", "commit", self.draft_input("x" * (limit + 1)))
        self.assertEqual(rc, 1)
        self.assertIn("limit is 6144", err)
        self.assertEqual(len(self.calls), 1)

    def test_draft_refuses_a_busy_laptop_without_creating_a_job(self):
        CLI.take_lock("lap")
        rc, out, err = self.cli("draft", "commit", self.draft_input())
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("already going", err)
        self.assertEqual(CLI.all_jobs(), [])
        self.assertTrue(os.path.exists(CLI.lock_path("lap")))
        self.assertEqual(self.calls, [])

    def test_add_accepts_new_kinds(self):
        for kind in ("commit", "changelog", "status", "brief", "docstrings"):
            self.assertEqual(self.cli("add", "lap", self.job(kind, kind=kind))[0], 0)

    def draft_config(self, **settings):
        with open(self.conf) as f:
            conf = json.load(f)
        conf.update(settings)
        with open(self.conf, "w") as f:
            json.dump(conf, f)

    def test_small_draft_uses_first_approved_mini(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(True, ""), lap=(True, ""))
        rc, out, err = self.cli("draft", "status", self.draft_input())
        self.assertEqual((rc, out), (0, "answer 1\n"), err)
        jid, mid = err.strip().split(" on ")
        self.assertEqual(mid, "m1")
        self.assertEqual(self.load("m1", jid)["status"], "done")
        self.assertEqual([c["mid"] for c in self.calls], ["m1"])
        self.assertIn(jid, self.cli("results", "--pending")[1])

    def test_unapproved_mini_skips_gate_and_uses_laptop(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.fake_gate(m1=(True, ""), lap=(True, ""))
        with mock.patch.object(self.gate, "check", wraps=self.gate.check) as check:
            rc, out, err = self.cli("draft", "commit", self.draft_input())
        self.assertEqual((rc, out), (0, "answer 1\n"), err)
        self.assertEqual([c.args[0] for c in check.call_args_list], ["lap"])
        self.assertIn(" on lap", err)
        self.assertEqual([j["machine"] for j in CLI.all_jobs()], ["lap"])

    def test_blocked_mini_falls_back_without_a_job(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(False, "app running"), lap=(True, ""))
        rc, out, err = self.cli("draft", "testcases", self.draft_input())
        self.assertEqual((rc, out), (0, "answer 1\n"), err)
        self.assertEqual([j["machine"] for j in CLI.all_jobs()], ["lap"])

    def test_runner_failure_is_recorded_then_laptop_runs_new_job(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(True, ""), lap=(True, ""))
        with mock.patch.dict(sys.modules, {"localq_models": stub_models(self.calls, self.events, fail=(1,))}):
            rc, out, err = self.cli("draft", "commit", self.draft_input())
        self.assertEqual((rc, out), (0, "answer 2\n"), err)
        jobs = {j["machine"]: j for j in CLI.all_jobs()}
        self.assertEqual((jobs["m1"]["status"], jobs["lap"]["status"]), ("failed", "done"))
        self.assertNotEqual(jobs["m1"]["id"], jobs["lap"]["id"])
        self.assertIn("model refused", jobs["m1"]["reason"])
        self.assertIn(" on m1", err)
        self.assertIn(" on lap", err)

    def test_all_blocked_lists_every_reason_without_jobs(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(False, "app running"), lap=(False, "memory low"))
        rc, out, err = self.cli("draft", "status", self.draft_input())
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("m1: app running", err)
        self.assertIn("lap: memory low", err)
        self.assertEqual(CLI.all_jobs(), [])

    def test_all_runnable_fail_exit_one_even_if_another_is_blocked(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(True, ""), lap=(False, "memory low"))
        with mock.patch.dict(sys.modules, {"localq_models": stub_models(self.calls, self.events, fail=(1,))}):
            rc, out, err = self.cli("draft", "status", self.draft_input())
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("memory low", err)
        self.assertEqual([j["status"] for j in CLI.all_jobs()], ["failed"])

    def test_long_kind_uses_hub_despite_mini_first(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(True, ""), lap=(True, ""))
        for kind in ("brief", "docstrings"):
            with self.subTest(kind=kind):
                rc, _, err = self.cli("draft", kind, self.draft_input())
                self.assertEqual(rc, 0, err)
                self.assertIn(" on lap", err)
        self.assertEqual([c["mid"] for c in self.calls], ["lap", "lap"])

    def test_busy_mini_is_skipped_and_laptop_runs(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(True, ""), lap=(True, ""))
        CLI.take_lock("m1")
        rc, out, err = self.cli("draft", "status", self.draft_input())
        self.assertEqual((rc, out), (0, "answer 1\n"), err)
        self.assertTrue(os.path.exists(CLI.lock_path("m1")))
        self.assertEqual([c["mid"] for c in self.calls], ["lap"])

    def test_approval_expiring_after_gate_does_not_leave_a_blocked_job(self):
        self.draft_config(draft_machines=["m1", "lap"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(True, ""), lap=(True, ""))
        with mock.patch.object(self.gate, "approval", side_effect=[{"until": time.time() + 1}, None]):
            rc, out, err = self.cli("draft", "status", self.draft_input())
        self.assertEqual((rc, out), (0, "answer 1\n"), err)
        self.assertEqual([j["machine"] for j in CLI.all_jobs()], ["lap"])

    def test_custom_small_kinds_control_routing(self):
        self.draft_config(draft_machines=["m1", "lap"], draft_small_kinds=["status"])
        self.gate.allow("m1", 1, "tester")
        self.fake_gate(m1=(True, ""), lap=(True, ""))
        rc, _, err = self.cli("draft", "commit", self.draft_input())
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.calls[0]["mid"], "lap")


if __name__ == "__main__":
    unittest.main()
