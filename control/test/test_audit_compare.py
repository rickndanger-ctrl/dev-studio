"""Socket-free tests for lib/audit_compare and bin/audit-compare."""
import json, os, subprocess, sys, unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import audit_compare

OK = {"status": "idle", "summary": "quiet", "concern": "", "needs_claude": False}
KEYS = ("status", "summary", "concern", "needs_claude")
ALLOWED = ("idle", "stalled")
UNITS = [("m1/alpha", "Box · alpha", {"x": 1}), ("m1/beta", "Box · beta", {"x": 2})]


def plan(label, snap):
    return {"state": "idle", "local_prompt": f"state {label} {snap}", "haiku_prompt": f"rules {label} {snap}"}


class CompareTest(unittest.TestCase):
    def rows(self, local, haiku):
        return audit_compare.compare(UNITS, plan, ALLOWED, KEYS, local, haiku)

    def test_each_side_gets_its_own_prompt_and_the_chars_are_kept(self):
        seen = []
        rows = self.rows(lambda p: (seen.append(p), json.dumps(OK))[1], lambda p: (seen.append(p), OK)[1])
        self.assertTrue(seen[0].startswith("state ") and seen[1].startswith("rules "))
        self.assertEqual([r["key"] for r in rows], ["m1/alpha", "m1/beta"])
        self.assertEqual((rows[0]["local_chars"], rows[0]["haiku_chars"]), (len(seen[0]), len(seen[1])))

    def test_the_local_reply_goes_through_fix_before_the_check(self):
        rows = audit_compare.compare(UNITS, plan, ALLOWED, KEYS, lambda p: "plain words", lambda p: OK,
                                     fix=lambda text, pl: json.dumps(dict(OK, summary=text)))
        self.assertEqual(rows[0]["local"]["summary"], "plain words")

    def test_a_failing_side_is_reported_and_the_other_still_runs(self):
        def bad(p):
            raise RuntimeError("gate: memory low")
        rows = self.rows(bad, lambda p: OK)
        self.assertEqual(rows[0]["local"], {"error": "gate: memory low"})
        self.assertEqual(rows[0]["haiku"], OK)

    def test_an_unparseable_local_reply_is_an_error_not_a_crash(self):
        rows = self.rows(lambda p: "no json here", lambda p: OK)
        self.assertIn("error", rows[0]["local"])

    def test_an_invalid_haiku_reply_is_an_error_not_a_disagreement(self):
        for bad in ({"summary": "no status"}, dict(OK, status="great"), ["list"], dict(OK, status=None)):
            with self.subTest(bad=bad):
                rows = self.rows(lambda p: json.dumps(OK), lambda p, b=bad: b)
                self.assertIn("error", rows[0]["haiku"])
                self.assertTrue(audit_compare.render(rows).endswith("0/0 comparable, 2 with an error"))

    def test_each_side_is_timed_and_synonyms_are_normalised_on_both_sides(self):
        rows = self.rows(lambda p: json.dumps(dict(OK, status="waiting")), lambda p: dict(OK, status="stuck"))
        self.assertEqual((rows[0]["local"]["status"], rows[0]["haiku"]["status"]), ("idle", "stalled"))
        self.assertIn("local_seconds", rows[0])
        self.assertIn("haiku_seconds", rows[0])

    def test_render_shows_both_sides_and_the_agreement_count(self):
        rows = self.rows(lambda p: json.dumps(OK),
                         lambda p: dict(OK, status="stalled", summary="nothing moves") if "beta" in p else OK)
        text = audit_compare.render(rows)
        self.assertIn("m1/alpha (Box · alpha, code says idle): SAME", text)
        self.assertRegex(text, r"local \(\d+ chars, 0\.0s\)")
        self.assertIn("SAME", text)
        self.assertIn("DIFFERENT", text)
        self.assertIn("idle - quiet", text)
        self.assertIn("stalled - nothing moves", text)
        self.assertTrue(text.endswith("agreement: 1/2 comparable, 0 with an error"))

    def test_an_error_never_counts_as_agreement(self):
        rows = self.rows(lambda p: "nope", lambda p: (_ for _ in ()).throw(RuntimeError("no claude")))
        self.assertTrue(audit_compare.render(rows).endswith("agreement: 0/0 comparable, 2 with an error"))
        self.assertIn("NOT COMPARED", audit_compare.render(rows))

    def test_a_mix_counts_only_valid_pairs_in_the_denominator(self):
        rows = self.rows(lambda p: "nope" if "beta" in p else json.dumps(OK), lambda p: OK)
        self.assertTrue(audit_compare.render(rows).endswith("agreement: 1/1 comparable, 1 with an error"))

    def test_help_exits_cleanly(self):
        out = subprocess.run([sys.executable, os.path.join(REPO, "bin", "audit-compare"), "--help"], capture_output=True, text=True, timeout=5)
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
