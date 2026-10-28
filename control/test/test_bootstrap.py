"""bin/bootstrap: a dry run changes nothing and exits 0; a sandbox install can be repeated.

Every run gets its own temporary HOME, so the developer's real home is never read or written.
Run from the repo root:  python3 -m unittest discover -s test -p 'test_*.py'
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BOOTSTRAP = os.path.join(REPO, "bin", "bootstrap")


def snapshot(root):
    """Every file and directory under root, relative to it. An untouched tree gives []."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(found)


class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="re-studio-home-")
        self.addCleanup(shutil.rmtree, self.home, True)

    def run_bootstrap(self, *args, sandbox=False):
        env = dict(os.environ)
        env["HOME"] = self.home
        env.pop("RE_STUDIO_CONFIG", None)
        env.pop("RE_STUDIO_HOME", None)
        if sandbox:
            env["RE_STUDIO_HOME"] = self.home  # install.sh writes here and skips systemctl
        return subprocess.run([BOOTSTRAP] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=120)

    def test_dry_run_writes_nothing_and_exits_0(self):
        before = snapshot(self.home)
        p = self.run_bootstrap("--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(before, [])
        self.assertIn("Dry run: nothing was changed", p.stdout)

    def test_output_ends_with_the_next_steps(self):
        p = self.run_bootstrap("--dry-run")
        self.assertEqual(p.returncode, 0, p.stderr)
        tail = p.stdout.rstrip().split("\n")
        next_at = tail.index("Next:")
        block = "\n".join(tail[next_at:])
        self.assertIn("1. Edit your machines:", block)
        self.assertIn("--enable", block)
        self.assertIn("bin/viewer", block)

    def test_enable_flag_changes_the_next_steps(self):
        p = self.run_bootstrap("--dry-run", "--enable", sandbox=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("The services are enabled", p.stdout)
        self.assertNotIn("Start the services (safe to repeat)", p.stdout)

    def test_sandbox_install_is_repeatable(self):
        first = self.run_bootstrap(sandbox=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        machines = os.path.join(self.home, ".config", "re-studio", "machines.json")
        self.assertTrue(os.path.isfile(machines))
        before = snapshot(self.home)
        second = self.run_bootstrap(sandbox=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("left alone", second.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_unknown_option_exits_2(self):
        before = snapshot(self.home)
        p = self.run_bootstrap("--bogus")
        self.assertEqual(p.returncode, 2)
        self.assertIn("unknown option", p.stderr)
        self.assertEqual(snapshot(self.home), before)

    def test_help_exits_0_and_writes_nothing(self):
        p = self.run_bootstrap("--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("bin/bootstrap", p.stdout)
        self.assertEqual(snapshot(self.home), [])


if __name__ == "__main__":
    unittest.main()
