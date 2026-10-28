"""Socket-free tests for local-first audit review."""
import json
import unittest
from unittest import mock

from lib import audit_review


class AuditReviewTest(unittest.TestCase):
    allowed = {"idle", "progressing", "stalled"}
    keys = ("status", "summary", "concern")
    local_verdict = {"status": "idle", "summary": "local", "concern": ""}
    paid_verdict = {"status": "stalled", "summary": "paid", "concern": "wait"}

    def setUp(self):
        self.stats = {}
        self.prompt = "fixture prompt"
        self.local = mock.Mock(return_value=json.dumps(self.local_verdict))
        self.haiku = mock.Mock(return_value=self.paid_verdict)
        self.hour = mock.patch.object(audit_review, "_hour", return_value="2026-01-02T03")
        self.hour.start()
        self.addCleanup(self.hour.stop)

    def review(self, **kwargs):
        args = {"rng": lambda: 0.9, "sample": 0.1, "stats": self.stats}
        args.update(kwargs)
        return audit_review.review(self.prompt, self.allowed, self.keys, self.local, self.haiku, **args)

    def test_valid_local_with_surrounding_text_and_extra_field(self):
        self.local.return_value = 'Result: ' + json.dumps(dict(self.local_verdict, extra=1)) + '\nThanks'
        self.assertEqual(self.review(), (self.local_verdict, "local"))
        self.local.assert_called_once_with(self.prompt)
        self.haiku.assert_not_called()
        h = self.stats["2026-01-02T03"]
        self.assertEqual(h["local"], 1)
        self.assertGreaterEqual(h["local_seconds"], 0)

    def test_exception_and_timeout_fall_back_and_record_reason(self):
        for exc, reason in ((RuntimeError("gate: unavailable"), "gate: unavailable"),
                            (TimeoutError(), "TimeoutError")):
            with self.subTest(exc=exc):
                self.stats.clear()
                self.local.side_effect = exc
                self.assertEqual(self.review(), (self.paid_verdict, "haiku-fallback"))
                h = self.stats["2026-01-02T03"]
                self.assertEqual((h["local"], h["haiku"], h["fallback"]), (1, 1, 1))
                self.assertEqual(h["local_fail"][reason], 1)

    def test_invalid_outputs_fall_back(self):
        bad = ("plain prose", json.dumps([self.local_verdict]),
               json.dumps(dict(self.local_verdict, status="unknown")),
               json.dumps({"status": "idle", "summary": "missing concern"}),
               '{"status":')
        for output in bad:
            with self.subTest(output=output):
                self.stats.clear()
                self.local.return_value = output
                self.assertEqual(self.review(), (self.paid_verdict, "haiku-fallback"))
                h = self.stats["2026-01-02T03"]
                self.assertEqual((h["fallback"], h["haiku"]), (1, 1))
                self.assertEqual(sum(h["local_fail"].values()), 1)

    def test_sample_agrees_and_keeps_local_verdict(self):
        self.haiku.return_value = dict(self.paid_verdict, status="idle")
        self.assertEqual(self.review(rng=lambda: 0.05), (self.local_verdict, "local+sample"))
        h = self.stats["2026-01-02T03"]
        self.assertEqual((h["sampled"], h["agreed"], h["haiku"]), (1, 1, 1))

    def test_sample_disagrees_and_haiku_wins(self):
        self.assertEqual(self.review(rng=lambda: 0.05), (self.paid_verdict, "local+sample"))
        h = self.stats["2026-01-02T03"]
        self.assertEqual((h["sampled"], h.get("agreed", 0), h["haiku"]), (1, 0, 1))

    def test_synonym_statuses_are_normalised_for_local_and_haiku(self):
        pairs = {"working": "progressing", "waiting": "idle", "stuck": "stalled", "off_scope": "off-scope"}
        allowed = {"progressing", "idle", "stalled", "off-scope"}
        for word, state in pairs.items():
            with self.subTest(word=word):
                local = mock.Mock(return_value=json.dumps(dict(self.local_verdict, status=word)))
                verdict, source = audit_review.review(self.prompt, allowed, self.keys, local, self.haiku, rng=lambda: 0.9)
                self.assertEqual((verdict["status"], source), (state, "local"))
                bad = mock.Mock(return_value="no")
                haiku = mock.Mock(return_value=dict(self.paid_verdict, status=word))
                verdict, source = audit_review.review(self.prompt, allowed, self.keys, bad, haiku)
                self.assertEqual((verdict["status"], source), (state, "haiku-fallback"))

    def test_an_allowed_status_is_never_rewritten(self):
        allowed = {"stuck", "progressing"}    # the laptop task keeps its own "stuck"
        self.assertEqual(audit_review.normalize({"status": "stuck"}, allowed), {"status": "stuck"})
        self.assertEqual(audit_review.normalize({"status": "great"}, allowed), {"status": "great"})

    def test_a_sampled_haiku_synonym_agrees_with_the_local_state(self):
        allowed = {"progressing", "idle"}
        local = mock.Mock(return_value=json.dumps(dict(self.local_verdict, status="progressing")))
        haiku = mock.Mock(return_value=dict(self.paid_verdict, status="working"))
        audit_review.review(self.prompt, allowed, self.keys, local, haiku, rng=lambda: 0.05, stats=self.stats)
        self.assertEqual(self.stats["2026-01-02T03"]["agreed"], 1)

    def test_on_sample_gets_both_verdicts_even_when_haiku_wins(self):
        pairs = []
        self.review(rng=lambda: 0.05, on_sample=lambda local, haiku: pairs.append((local, haiku)))
        self.assertEqual(pairs, [(self.local_verdict, self.paid_verdict)])

    def test_on_sample_is_not_called_without_a_sample_or_on_fallback(self):
        pairs = []
        self.review(on_sample=lambda *a: pairs.append(a))
        self.local.side_effect = RuntimeError("down")
        self.review(rng=lambda: 0.05, on_sample=lambda *a: pairs.append(a))
        self.assertEqual(pairs, [])

    def test_a_failing_on_sample_does_not_change_the_verdict(self):
        def boom(local, haiku):
            raise OSError("disk full")
        self.assertEqual(self.review(rng=lambda: 0.05, on_sample=boom), (self.paid_verdict, "local+sample"))

    ALERT = ("stalled",)

    def alert_review(self, local_status="stalled", haiku_status="stalled", **kwargs):
        self.local.return_value = json.dumps(dict(self.local_verdict, status=local_status))
        self.haiku.return_value = dict(self.paid_verdict, status=haiku_status)
        return self.review(confirm=self.ALERT, **kwargs)

    def test_an_unsampled_local_alert_is_confirmed_by_haiku_and_a_match_keeps_the_local_verdict(self):
        pairs = []
        verdict, source = self.alert_review(on_confirm=lambda local, haiku: pairs.append((local["status"], haiku["status"])))
        self.assertEqual((verdict["summary"], source), ("local", "confirm"))
        self.assertEqual(pairs, [("stalled", "stalled")])
        h = self.stats["2026-01-02T03"]
        self.assertEqual((h["confirmed"], h.get("overturned", 0), h["haiku"]), (1, 0, 1))

    def test_haiku_wins_when_it_overturns_a_local_alert(self):
        pairs = []
        verdict, source = self.alert_review(haiku_status="idle", on_confirm=lambda local, haiku: pairs.append((local["status"], haiku["status"])))
        self.assertEqual((verdict["status"], verdict["summary"], source), ("idle", "paid", "confirm"))
        self.assertEqual(pairs, [("stalled", "idle")])
        self.assertEqual(self.stats["2026-01-02T03"]["overturned"], 1)

    def test_a_calm_local_verdict_is_not_confirmed(self):
        verdict, source = self.alert_review(local_status="idle")
        self.assertEqual(source, "local")
        self.haiku.assert_not_called()
        self.assertNotIn("confirmed", self.stats["2026-01-02T03"])

    def test_a_sampled_alert_uses_the_sample_not_a_second_confirm(self):
        verdict, source = self.alert_review(rng=lambda: 0.05)
        self.assertEqual(source, "local+sample")
        self.assertEqual(self.haiku.call_count, 1)
        self.assertNotIn("confirmed", self.stats["2026-01-02T03"])

    def test_a_fallback_verdict_is_not_confirmed_again(self):
        self.local.side_effect = RuntimeError("down")
        verdict, source = self.review(confirm=self.ALERT)
        self.assertEqual((source, self.haiku.call_count), ("haiku-fallback", 1))

    def test_a_failing_confirm_raises_so_the_alert_is_not_reported_unchecked(self):
        self.haiku.side_effect = RuntimeError("no claude")
        with self.assertRaises(RuntimeError):
            self.alert_review()

    def test_confirm_asks_haiku_the_haiku_prompt_and_a_failing_on_confirm_changes_nothing(self):
        def boom(local, haiku):
            raise OSError("disk full")
        verdict, source = self.alert_review(haiku_status="idle", on_confirm=boom, haiku_prompt="rules prompt")
        self.haiku.assert_called_once_with("rules prompt")
        self.assertEqual((verdict["status"], source), ("idle", "confirm"))

    def test_a_malformed_haiku_confirm_is_a_failed_confirm_never_a_confirmation(self):
        for bad in ({"status": "stalled"}, dict(self.paid_verdict, status="great"), dict(self.paid_verdict, status=None), ["stalled"], "stalled"):
            with self.subTest(bad=bad):
                self.stats.clear()
                self.local.return_value = json.dumps(dict(self.local_verdict, status="stalled"))
                self.haiku.return_value = bad
                with self.assertRaises(ValueError):
                    self.review(confirm=self.ALERT)
                h = self.stats["2026-01-02T03"]
                self.assertEqual((h["confirm_failed"], h.get("overturned", 0)), (1, 0))

    def test_a_failed_haiku_call_counts_as_a_failed_confirm(self):
        self.haiku.side_effect = RuntimeError("no claude")
        with self.assertRaises(RuntimeError):
            self.alert_review()
        self.assertEqual(self.stats["2026-01-02T03"]["confirm_failed"], 1)

    def test_a_malformed_sampled_reply_is_ignored_and_a_malformed_fallback_reply_raises(self):
        self.haiku.return_value = {"status": "stalled"}
        self.assertEqual(self.review(rng=lambda: 0.05), (self.local_verdict, "local"))
        self.assertEqual(self.stats["2026-01-02T03"]["sample_failed"], 1)
        self.local.side_effect = RuntimeError("down")
        with self.assertRaises(ValueError):
            self.review()

    def test_a_sampled_alert_whose_haiku_check_fails_is_not_reported_unconfirmed(self):
        for bad in ({"status": "stalled"}, RuntimeError("no claude")):
            with self.subTest(bad=bad):
                self.stats.clear()
                self.local.return_value = json.dumps(dict(self.local_verdict, status="stalled"))
                self.haiku.side_effect = bad if isinstance(bad, Exception) else None
                self.haiku.return_value = bad
                with self.assertRaises(Exception):
                    self.review(rng=lambda: 0.05, confirm=self.ALERT)
                h = self.stats["2026-01-02T03"]
                self.assertEqual((h.get("sampled", 0), h["confirm_failed"], h["sample_failed"]), (0, 1, 1))

    def test_a_calm_sampled_verdict_survives_a_failed_check_even_with_confirm_on(self):
        self.haiku.return_value = {"status": "idle"}
        self.assertEqual(self.review(rng=lambda: 0.05, confirm=self.ALERT), (self.local_verdict, "local"))
        h = self.stats["2026-01-02T03"]
        self.assertEqual((h.get("sampled", 0), h.get("agreed", 0), h["sample_failed"], h.get("confirm_failed", 0)), (0, 0, 1, 0))

    def test_a_synonym_in_the_haiku_confirm_is_normalised_before_the_check(self):
        allowed = {"stalled", "idle"}
        local = mock.Mock(return_value=json.dumps(dict(self.local_verdict, status="stalled")))
        haiku = mock.Mock(return_value=dict(self.paid_verdict, status="waiting"))
        verdict, source = audit_review.review(self.prompt, allowed, self.keys, local, haiku, rng=lambda: 0.9, confirm=("stalled",))
        self.assertEqual((verdict["status"], source), ("idle", "confirm"))

    def test_ask_haiku_only_counts_the_ask_and_validates_the_reply(self):
        verdict, source = audit_review.ask_haiku_only(self.prompt, self.allowed, self.keys, self.haiku, stats=self.stats)
        self.assertEqual((verdict, source), (self.paid_verdict, "haiku-offscope"))
        self.local.assert_not_called()
        h = self.stats["2026-01-02T03"]
        self.assertEqual((h["offscope"], h["haiku"], h.get("local", 0)), (1, 1, 0))
        for bad in ({"status": "stalled"}, dict(self.paid_verdict, status="great")):
            with self.assertRaises(ValueError):
                audit_review.ask_haiku_only(self.prompt, self.allowed, self.keys, lambda p, b=bad: b)

    def test_without_confirm_nothing_changes(self):
        self.local.return_value = json.dumps(dict(self.local_verdict, status="stalled"))
        self.assertEqual(self.review()[1], "local")
        self.haiku.assert_not_called()

    def test_bump_tracks_hours_and_accepts_missing_stats(self):
        audit_review.bump(self.stats, "passes")
        audit_review.bump(self.stats, "skipped", 2)
        with mock.patch.object(audit_review, "_hour", return_value="2026-01-02T04"):
            audit_review.bump(self.stats, "passes", 3)
            audit_review.bump(None, "passes")
        self.assertEqual(self.stats, {"2026-01-02T03": {"passes": 1, "skipped": 2},
                                      "2026-01-02T04": {"passes": 3}})


if __name__ == "__main__":
    unittest.main()
