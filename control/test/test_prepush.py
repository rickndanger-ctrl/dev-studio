"""Tests for hooks/pre-push and the hooks step of install.sh, in throwaway repos with a bare remote.

Never touches the real repo's remotes: every repo here lives in a temp dir. Private values are assembled at run
time so this file itself stays clean under test/privacy.sh.
"""
import os, shutil, subprocess, tempfile, unittest

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ZEROS = "0" * 40
WORD = "alice" + "-laptop"
IP = ".".join(["100", "100", "1", "1"])


def clean_env(**extra):
    """The environment without the owner's word list or allow list: each test brings its own in its temp repo."""
    env = {k: v for k, v in os.environ.items() if k not in ("PRIVACY_WORDS", "PRIVACY_ALLOW")}
    env.update(extra)
    return env


def git(cwd, *args, check=True, stdin=None):
    r = subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True, input=stdin, env=clean_env())
    if check and r.returncode:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r


class PrePushTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepush-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.remote = os.path.join(self.tmp, "remote.git")
        self.work = os.path.join(self.tmp, "work")
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        os.makedirs(os.path.join(self.work, "test"))
        subprocess.run(["git", "init", "-q", "-b", "main", self.work], check=True)
        shutil.copytree(os.path.join(REPO, "hooks"), os.path.join(self.work, "hooks"))
        shutil.copy(os.path.join(REPO, "test", "privacy.sh"), os.path.join(self.work, "test", "privacy.sh"))
        with open(os.path.join(self.work, ".gitignore"), "w") as f:
            f.write(".privacy-words\n")
        self.words(WORD)
        git(self.work, "config", "core.hooksPath", "hooks")
        git(self.work, "remote", "add", "origin", self.remote)
        self.commit("README.md", "hello\n", "first")

    def words(self, *ws):
        with open(os.path.join(self.work, ".privacy-words"), "w") as f:
            f.write("\n".join(ws) + "\n")

    def commit(self, path, text, msg):
        with open(os.path.join(self.work, path), "w") as f:
            f.write(text)
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", msg)
        return git(self.work, "rev-parse", "HEAD").stdout.strip()

    def push(self, *args):
        return git(self.work, "push", *args, check=False)

    def test_clean_push_passes(self):
        r = self.push("origin", "main")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_private_word_in_an_old_commit_blocks(self):
        self.commit("notes.md", f"ssh {WORD}\n", "add notes")
        self.commit("notes.md", "ssh somewhere\n", "scrub notes")     # the tip is clean; history is not
        r = self.push("origin", "main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("notes.md", r.stderr)
        self.assertIn("privacy-word", r.stderr)
        self.assertIn("bin/publish-export", r.stderr)
        self.assertNotIn("main", git(self.remote, "branch", "--list").stdout)    # nothing arrived

    def test_private_ip_blocks(self):
        self.commit("hosts.txt", f"node {IP}\n", "add hosts")
        r = self.push("origin", "main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(IP, r.stderr)

    def test_incremental_push_scans_only_new_commits(self):
        self.assertEqual(self.push("origin", "main").returncode, 0)
        self.commit("a.txt", "fine\n", "clean change")
        self.assertEqual(self.push("origin", "main").returncode, 0)
        self.commit("b.txt", f"{WORD}\n", "leaky change")
        r = self.push("origin", "main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("b.txt", r.stderr)

    def test_new_branch_excludes_what_a_remote_already_has(self):
        self.commit("old.txt", f"{WORD}\n", "old leak")
        self.assertEqual(self.push("--no-verify", "origin", "main").returncode, 0)    # already out there
        git(self.work, "checkout", "-q", "-b", "feature")
        self.commit("new.txt", "clean\n", "clean feature")
        r = self.push("origin", "feature")
        self.assertEqual(r.returncode, 0, r.stderr)
        git(self.work, "checkout", "-q", "-b", "bad")
        self.commit("bad.txt", f"{WORD}\n", "new leak")
        self.assertNotEqual(self.push("origin", "bad").returncode, 0)

    def test_new_remote_is_scanned_in_full_even_if_another_remote_has_the_history(self):
        self.commit("old.txt", f"{WORD}\n", "old leak")
        self.assertEqual(self.push("--no-verify", "origin", "main").returncode, 0)    # the private remote has it all
        pub = os.path.join(self.tmp, "pub.git")
        subprocess.run(["git", "init", "-q", "--bare", pub], check=True)
        git(self.work, "remote", "add", "pub", pub)
        r = self.push("pub", "main")                                                  # the public one must not
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("old.txt", r.stderr)
        self.assertNotIn("main", git(pub, "branch", "--list").stdout)
        r = self.push(pub, "main")                                                    # a bare URL is no exception
        self.assertNotEqual(r.returncode, 0)

    def test_tags_are_scanned(self):
        self.assertEqual(self.push("origin", "main").returncode, 0)
        self.commit("t.txt", f"{WORD}\n", "tagged leak")
        git(self.work, "tag", "-a", "v1", "-m", "release")
        r = self.push("origin", "v1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("t.txt", r.stderr)

    def test_delete_is_skipped(self):
        hook = os.path.join(self.work, "hooks", "pre-push")
        line = f"(delete) {ZEROS} refs/heads/old {'1' * 40}\n"
        r = subprocess.run([hook, "origin", self.remote], cwd=self.work, input=line, capture_output=True, text=True, env=clean_env())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_word_list_warns_but_does_not_block(self):
        os.remove(os.path.join(self.work, ".privacy-words"))
        r = self.push("origin", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING", r.stderr)
        self.assertIn(".privacy-words", r.stderr)

    def test_generic_rules_still_run_without_a_word_list(self):
        os.remove(os.path.join(self.work, ".privacy-words"))
        self.commit("h.txt", f"{IP}\n", "ip")
        self.assertNotEqual(self.push("origin", "main").returncode, 0)


class InstallHooksTest(unittest.TestCase):
    def checkout(self):
        tmp = tempfile.mkdtemp(prefix="install-hooks-")
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.join(tmp, "repo")
        files = subprocess.run(["git", "-C", REPO, "ls-files", "-z"], capture_output=True, check=True).stdout.decode().split("\0")
        for p in filter(None, files):
            if os.path.exists(os.path.join(REPO, p)):
                os.makedirs(os.path.dirname(os.path.join(repo, p)), exist_ok=True)
                shutil.copy2(os.path.join(REPO, p), os.path.join(repo, p))
        shutil.copytree(os.path.join(REPO, "hooks"), os.path.join(repo, "hooks"), dirs_exist_ok=True)
        subprocess.run(["git", "init", "-q", repo], check=True)
        return tmp, repo

    def install(self, tmp, repo, *args):
        env = clean_env(RE_STUDIO_HOME=os.path.join(tmp, "home"))
        return subprocess.run(["bash", os.path.join(repo, "install.sh"), *args], cwd=repo, env=env, capture_output=True, text=True)

    def hooks_path(self, repo):
        return subprocess.run(["git", "-C", repo, "config", "--local", "--get", "core.hooksPath"], capture_output=True, text=True).stdout.strip()

    def test_install_sets_hooks_path_once(self):
        tmp, repo = self.checkout()
        r = self.install(tmp, repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.hooks_path(repo), "hooks")
        self.assertIn("NO .privacy-words", r.stderr)
        r2 = self.install(tmp, repo)
        self.assertIn("already hooks", r2.stdout)

    def test_dry_run_changes_nothing(self):
        tmp, repo = self.checkout()
        r = self.install(tmp, repo, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.hooks_path(repo), "")
        self.assertIn("[dry-run] git", r.stdout)


if __name__ == "__main__":
    unittest.main()
