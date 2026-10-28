"""bin/publish-export: a clean export is one commit with a generic author; any private string leaves nothing.

Each test builds its own throwaway source repo in a temp directory (the real repo is never read or changed).
Run from the repo root:  python3 -m unittest discover -s test -p 'test_*.py'
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PRIVATE_WORD = "zebrawood-owner"  # a fake owner word, listed in the temp .privacy-words only
DEFAULT_NAME = "re-studio"
DEFAULT_EMAIL = "re-studio@users.noreply.github.com"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
                          cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, check=True).stdout.strip()


class PublishExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="publish-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = os.path.join(self.tmp, "source")
        self.out = os.path.join(self.tmp, "export")  # does not exist yet
        os.mkdir(self.src)
        self.make_source()

    def make_source(self):
        git(self.src, "init", "-q")
        os.makedirs(os.path.join(self.src, "bin"))
        os.makedirs(os.path.join(self.src, "test"))
        shutil.copy2(os.path.join(REPO, "bin", "publish-export"), os.path.join(self.src, "bin", "publish-export"))
        shutil.copy2(os.path.join(REPO, "test", "privacy.sh"), os.path.join(self.src, "test", "privacy.sh"))
        self.write("README.md", "A public-safe readme.\n")
        self.write("bin/tool", "#!/usr/bin/env bash\necho hi\n")
        os.chmod(os.path.join(self.src, "bin", "tool"), 0o755)
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "init")
        # Untracked, so never exported: the owner's word list and a local note.
        self.write(".privacy-words", PRIVATE_WORD + "\n")
        self.write("local-notes.txt", "private scratch, not tracked\n")

    def write(self, rel, text):
        path = os.path.join(self.src, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def track(self, rel, text):
        self.write(rel, text)
        git(self.src, "add", rel)
        git(self.src, "commit", "-q", "-m", f"add {rel}")

    def publish(self, *args, **env):
        environ = dict(os.environ)
        environ.pop("PUBLISH_NAME", None)
        environ.pop("PUBLISH_EMAIL", None)
        environ.pop("PUBLISH_WORDS", None)
        environ.update(env)
        return subprocess.run([os.path.join(self.src, "bin", "publish-export"), *args], env=environ,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)

    def test_clean_export_is_one_commit_with_a_generic_author(self):
        p = self.publish(self.out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "README.md")))
        tool = os.path.join(self.out, "bin", "tool")
        self.assertTrue(os.stat(tool).st_mode & stat.S_IXUSR, "executable bit kept")
        self.assertFalse(os.path.exists(os.path.join(self.out, "local-notes.txt")), "untracked file not exported")
        self.assertFalse(os.path.exists(os.path.join(self.out, ".privacy-words")), "word list not exported")
        self.assertEqual(git(self.out, "rev-list", "--all", "--count"), "1")
        self.assertEqual(git(self.out, "log", "-1", "--format=%an <%ae>"), f"{DEFAULT_NAME} <{DEFAULT_EMAIL}>")
        self.assertEqual(git(self.out, "log", "-1", "--format=%cn <%ce>"), f"{DEFAULT_NAME} <{DEFAULT_EMAIL}>")
        self.assertEqual(git(self.out, "log", "-1", "--format=%s"), "Initial public release")
        self.assertEqual(git(self.out, "remote"), "", "no remote added")
        self.assertIn("not pushed", p.stdout)
        self.assertIn(f"git -C {self.out} remote add origin", p.stdout)

    def test_name_and_email_can_be_overridden(self):
        p = self.publish(self.out, PUBLISH_NAME="Studio Bot", PUBLISH_EMAIL="bot@example.org")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(git(self.out, "log", "-1", "--format=%an <%ae>"), "Studio Bot <bot@example.org>")

    def test_refuses_a_non_empty_directory_and_touches_nothing(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "keep.txt"), "w") as f:
            f.write("mine\n")
        p = self.publish(self.out)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not empty", p.stderr)
        self.assertEqual(sorted(os.listdir(self.out)), ["keep.txt"])

    def test_an_empty_existing_directory_is_used(self):
        os.makedirs(self.out)
        p = self.publish(self.out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(git(self.out, "rev-list", "--all", "--count"), "1")

    def test_a_private_word_refuses_and_leaves_nothing(self):
        self.track("notes.md", f"the plan mentions {PRIVATE_WORD} by name\n")
        p = self.publish(self.out)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("privacy-word", p.stdout)
        self.assertIn("nothing kept", p.stderr)
        self.assertFalse(os.path.exists(self.out), "the export directory is gone")

    def test_a_private_word_in_an_empty_directory_leaves_it_empty(self):
        self.track("notes.md", f"{PRIVATE_WORD}\n")
        os.makedirs(self.out)
        p = self.publish(self.out)
        self.assertEqual(p.returncode, 1)
        self.assertTrue(os.path.isdir(self.out), "the caller's directory is kept")
        self.assertEqual(os.listdir(self.out), [])

    def test_a_private_home_path_refuses(self):
        # Built from parts so this file does not itself contain a home path that the tree scan would flag.
        fake_home = "/home/" + "alice" + "/project/bin"
        self.track("setup.txt", f"run it from {fake_home}\n")
        p = self.publish(self.out)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("home-path", p.stdout)
        self.assertFalse(os.path.exists(self.out))

    def test_a_missing_word_list_refuses_and_leaves_nothing(self):
        os.remove(os.path.join(self.src, ".privacy-words"))
        p = self.publish(self.out)
        self.assertEqual(p.returncode, 1)
        self.assertIn("no word list", p.stderr)
        self.assertFalse(os.path.exists(self.out))

    def test_publish_words_points_at_another_list(self):
        other = os.path.join(self.tmp, "owner-words.txt")
        with open(other, "w") as f:
            f.write("# owner names\n" + PRIVATE_WORD + "\n")
        self.track("notes.md", f"{PRIVATE_WORD}\n")
        p = self.publish(self.out, PUBLISH_WORDS=other)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(self.out))

    def test_usage_without_an_outdir_exits_2(self):
        p = self.publish()
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage", p.stderr)


if __name__ == "__main__":
    unittest.main()
