"""test/shellcheck.sh: skips cleanly when shellcheck is missing, and passes the binary's verdict through."""
import os, stat, subprocess, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SCRIPT = os.path.join(REPO, "test", "shellcheck.sh")


def run(shellcheck):
    env = dict(os.environ, SHELLCHECK=shellcheck)
    return subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True)


class ShellcheckStepTest(unittest.TestCase):
    def fake(self, code):
        d = tempfile.mkdtemp(prefix="fake-sc-")
        self.addCleanup(subprocess.run, ["rm", "-rf", d])
        path = os.path.join(d, "shellcheck")
        with open(path, "w") as f:
            f.write("#!/bin/sh\nfor a in \"$@\"; do echo \"$a\"; done > \"%s/args\"\nexit %d\n" % (d, code))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path, d

    def test_missing_shellcheck_is_a_skip_not_a_failure(self):
        r = run(os.path.join(tempfile.gettempdir(), "no-such-shellcheck-binary"))
        self.assertEqual(r.returncode, 77)
        self.assertIn("SKIP shellcheck (not installed)", r.stdout)

    def test_clean_run_passes_and_gets_the_bash_files(self):
        path, d = self.fake(0)
        r = run(path)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(d, "args")) as f:
            args = f.read().split()
        self.assertIn("test/ci.sh", args)
        self.assertIn("test/shellcheck.sh", args)
        self.assertNotIn("test/test_shellcheck.py", args)

    def test_findings_fail(self):
        path, _ = self.fake(1)
        self.assertEqual(run(path).returncode, 1)


if __name__ == "__main__":
    unittest.main()
