"""Tests for lib/localq_report.py, localq_share.paid_today(since) and bin/localq parse_since. No sockets, no models."""
import importlib.machinery, importlib.util, json, os, sys, tempfile, time, unittest

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import localq_report, localq_share


def load_cli():
    loader = importlib.machinery.SourceFileLoader("localq_cli_report", os.path.join(REPO, "bin", "localq"))
    spec = importlib.util.spec_from_loader("localq_cli_report", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def job(jid, kind="draft", status="done", t=1000, seconds=1.0, chars_out=10, verdict=None):
    j = {"id": jid, "kind": kind, "status": status, "finished_at": t, "seconds": seconds, "chars_out": chars_out}
    if verdict:
        j["review"] = {"verdict": verdict}
    return j


class BuildTests(unittest.TestCase):
    def test_counts_by_kind_and_status(self):
        r = localq_report.build([job("a"), job("b", status="failed"), job("c", kind="commit")], None, None)
        self.assertEqual(r["jobs"], 3)
        self.assertEqual(r["by_kind"], {"draft": {"done": 1, "failed": 1}, "commit": {"done": 1}})

    def test_since_drops_older_jobs_and_uses_queued_time_when_unfinished(self):
        old = job("old", t=100)
        waiting = {"id": "w", "kind": "draft", "status": "queued", "queued_at": 900}
        r = localq_report.build([old, job("new", t=2000), waiting], None, 500)
        self.assertEqual(r["jobs"], 2)

    def test_verdict_mix_counts_only_done_jobs(self):
        jobs = [job("a", verdict="accept"), job("b", verdict="fix"), job("c", verdict="reject"), job("d"),
                job("e", status="failed")]
        self.assertEqual(localq_report.build(jobs, None, None)["verdicts"],
                         {"accept": 1, "fix": 1, "reject": 1, "unreviewed": 1})

    def test_share_uses_done_output_against_paid(self):
        r = localq_report.build([job("a", chars_out=30), job("b", status="failed", chars_out=99)], {"chars": 70}, None)
        self.assertEqual(r["share"], {"output_chars": 30, "paid_chars": 70, "pct": 30})
        self.assertIsNone(localq_report.build([job("a")], None, None)["share"]["pct"])

    def test_five_slowest_ordered_and_jobs_without_seconds_skipped(self):
        jobs = [job(f"j{i}", seconds=i) for i in range(1, 8)] + [{"id": "n", "kind": "draft", "status": "queued", "queued_at": 5}]
        slow = localq_report.build(jobs, None, None)["slowest"]
        self.assertEqual([s["id"] for s in slow], ["j7", "j6", "j5", "j4", "j3"])


class RenderTests(unittest.TestCase):
    def test_render_shows_each_section(self):
        out = localq_report.render(localq_report.build(
            [job("a", seconds=2.5, verdict="accept"), job("b", kind="commit", status="failed")], {"chars": 10}, 0))
        self.assertIn("2 jobs", out)
        self.assertIn("draft         1 done", out)
        self.assertIn("commit        1 failed", out)
        self.assertIn("verdicts: 1 accept, 0 fix, 0 reject, 0 unreviewed", out)
        self.assertIn("output share (estimate): 50%; local 10 chars, paid 10 chars", out)
        self.assertIn("a  draft  2.5s", out)

    def test_render_empty_window(self):
        out = localq_report.render(localq_report.build([], None, None))
        self.assertIn("0 jobs", out)
        self.assertIn("no jobs in this window", out)
        self.assertIn("paid unknown", out)

    def test_render_never_exceeds_24_lines(self):
        jobs = [job(f"j{i}", kind=f"kind{i:02d}", seconds=i) for i in range(40)]
        self.assertLessEqual(len(localq_report.render(localq_report.build(jobs, None, None)).splitlines()), 24)


class PaidSinceTests(unittest.TestCase):
    def test_since_replaces_the_day_filter(self):
        with tempfile.TemporaryDirectory() as d:
            for name, t, text in (("a", 1000, "xxxx"), ("b", 3000, "yy"), ("c", 5000, "z")):
                with open(os.path.join(d, name + ".json"), "w") as f:
                    json.dump({"t": t, "from": "m", "to": "n", "text": text}, f)
            day = time.strftime("%Y-%m-%d", time.localtime(1000))
            self.assertEqual(localq_share.paid_today("2001-01-01", d, 2000)["chars"], 3)
            self.assertEqual(localq_share.paid_today(day, d)["chars"], 7)


class ParseSinceTests(unittest.TestCase):
    def test_clock_time_means_today_and_full_form_is_taken_as_given(self):
        cli = load_cli()
        now = time.mktime(time.strptime("2026-10-27 20:00", "%Y-%m-%d %H:%M"))
        self.assertEqual(cli.parse_since("17:46", now), time.mktime(time.strptime("2026-10-27 17:46", "%Y-%m-%d %H:%M")))
        self.assertEqual(cli.parse_since("2026-10-01 08:05", now), time.mktime(time.strptime("2026-10-01 08:05", "%Y-%m-%d %H:%M")))

    def test_bad_input_is_refused(self):
        cli = load_cli()
        with self.assertRaises(cli.Refused):
            cli.parse_since("tea time")


if __name__ == "__main__":
    unittest.main()
