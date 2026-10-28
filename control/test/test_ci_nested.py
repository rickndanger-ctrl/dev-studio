"""test/ci.sh must work when this repo sits at control/ inside another git repo (the public package layout)."""
import os, shutil, subprocess, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


@unittest.skipIf(os.environ.get("RE_STUDIO_CI"), "already running inside ci.sh")
class NestedCiTest(unittest.TestCase):
    def test_ci_passes_from_control_inside_an_outer_repo(self):
        tmp = tempfile.mkdtemp(prefix="nested-ci-")
        self.addCleanup(shutil.rmtree, tmp, True)
        outer = os.path.join(tmp, "outer")
        files = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                               capture_output=True, check=True).stdout.decode().split("\0")
        for p in filter(None, files):
            src = os.path.join(REPO, p)
            if os.path.isfile(src):
                dst = os.path.join(outer, "control", p)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        with open(os.path.join(outer, "README.md"), "w") as f:
            f.write("outer\n")
        subprocess.run(["git", "init", "-q", "-b", "main", outer], check=True)
        subprocess.run(["git", "-C", outer, "add", "-A"], check=True)
        subprocess.run(["git", "-C", outer, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "outer"], check=True)
        env = dict(os.environ, RE_STUDIO_CI_NO_SHOT="1")
        env.pop("RE_STUDIO_CI", None)
        r = subprocess.run(["bash", "control/test/ci.sh"], cwd=outer, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CI OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
