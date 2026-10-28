"""Unit tests for test/privacy.sh, run against temp git repos (nothing here touches the real tree).

Run from the repo root:  python3 -m unittest test/test_privacy.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
PRIVACY = os.path.join(HERE, "privacy.sh")
# Tailnet fixtures are assembled at runtime, so this file never contains a literal tailnet address.
TAILNET_IP = ".".join(["100", "101", "1", "2"])
TAILNET_IP_BIN = ".".join(["100", "101", "7", "8"])
OLD_IP = ".".join(["100", "100", "9", "9"])  # not allow-listed anywhere, so it must always be a hit


def clean_env(**extra):
    """The parent's environment without the privacy variables, plus the ones given here."""
    env = {k: v for k, v in os.environ.items() if k not in ("PRIVACY_WORDS", "PRIVACY_ALLOW")}
    env.update(extra)
    return env
# Author e-mails are assembled at runtime for the same reason; CLEAN_EMAIL is a GitHub no-reply address.
CLEAN_EMAIL = "1+fixture@users.noreply.github.com"
PRIVATE_EMAIL = "dev@" + "fixture.test"


class PrivacyFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="privacy-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        self.words = os.path.join(self.tmp, "words.txt")  # outside the fixture repo, so it is never tracked
        open(self.words, "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def track(self, name, content):
        path = os.path.join(self.repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(path, mode) as f:
            f.write(content)
        subprocess.run(["git", "-C", self.repo, "add", name], check=True)

    def scan(self):
        env = clean_env(PRIVACY_WORDS=self.words)
        return subprocess.run(["bash", PRIVACY, self.repo], capture_output=True, text=True, env=env, timeout=60)

    def scan_revs(self, *args):
        """Run privacy.sh in history mode from inside the fixture repo, as a pre-push hook would."""
        env = clean_env(PRIVACY_WORDS=self.words)
        return subprocess.run(["bash", PRIVACY, *args], cwd=self.repo, capture_output=True, text=True, env=env, timeout=60)

    def commit(self, files, message="update", email=CLEAN_EMAIL):
        """Write files, commit them as the given author, and return the new commit's full sha."""
        for name, content in files.items():
            self.track(name, content)
        git = ["git", "-C", self.repo, "-c", "user.name=fixture", "-c", f"user.email={email}"]
        subprocess.run([*git, "commit", "-q", "-m", message], check=True)
        return subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()


class CleanTree(PrivacyFixture):
    def test_clean_tree_passes(self):
        self.track("README.md", "Studio notes.\nRun the viewer on 127.0.0.1 and mail user@example.com.\n")
        self.track("bin/tool", "#!/usr/bin/env bash\nHOME_DIR=/home/{}/work\n".format("runner"))
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("privacy: OK (2 tracked file(s) scanned)", r.stdout)


class TemplateAndEmails(PrivacyFixture):
    def test_word_list_template_is_not_scanned(self):
        self.track(".privacy-words.example", f"# fake values on purpose\n{TAILNET_IP}\nFakeName\n")
        self.track("README.md", "fine\n")
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("privacy-words-tracked", r.stdout)

    def test_github_noreply_and_private_emails_in_tree(self):
        self.track("README.md", f"contact {CLEAN_EMAIL}\n")
        self.assertEqual(self.scan().returncode, 0)
        self.track("NOTES.md", f"contact {PRIVATE_EMAIL}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("NOTES.md:1: email", r.stdout)


class TailnetIp(PrivacyFixture):
    def test_tailnet_ip_fails_with_file_and_line(self):
        self.track("README.md", f"Clean line.\nThe viewer sits at {TAILNET_IP} on the tailnet.\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"README.md:2: tailnet-ip: {TAILNET_IP}", r.stdout)
        self.assertNotIn("README.md:1:", r.stdout)


class OtherRules(PrivacyFixture):
    def test_lan_ip_home_path_and_tailnet_name_fail(self):
        self.track("a.txt", "host 192.168.1.20\n")
        self.track("b.txt", "cd /home/{}/projects\n".format("probe"))
        self.track("c.txt", "node box.example-tailnet.ts.net\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1)
        self.assertIn("a.txt:1: lan-ip", r.stdout)
        self.assertIn("b.txt:1: home-path", r.stdout)
        self.assertIn("c.txt:1: tailnet-name", r.stdout)

    def test_word_list_hit_is_reported_without_echoing_the_word(self):
        with open(self.words, "w") as f:
            f.write("# owner names\nZebraNebula\n")
        self.track("notes.md", "a line that mentions zebranebula in lower case\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1)
        self.assertIn("notes.md:1: privacy-word", r.stdout)
        self.assertNotIn("zebranebula", r.stdout.lower())

    def test_binary_strings_are_scanned(self):
        png_like = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00\x01\x02" + f"tEXt Source {TAILNET_IP_BIN}".encode() + b"\x00\xff"
        self.track("docs/shot.png", png_like)
        r = self.scan()
        self.assertEqual(r.returncode, 1)
        self.assertIn("docs/shot.png:strings#", r.stdout)
        self.assertIn(f"tailnet-ip: {TAILNET_IP_BIN}", r.stdout)


class HistoryMode(PrivacyFixture):
    def test_clean_history_passes(self):
        self.commit({"README.md": "Studio notes.\n"})
        self.commit({"bin/tool": "#!/usr/bin/env bash\nHOME_DIR=/home/{}/work\n".format("runner")},
                    message="Add tool\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        r = self.scan_revs("--history")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("privacy: OK (2 commits)", r.stdout)

    def test_ip_removed_later_still_fails_history(self):
        old = self.commit({"notes.txt": f"node at {OLD_IP}\n"})
        self.commit({"notes.txt": "clean now\n"})
        self.assertEqual(self.scan().returncode, 0, "the tree is clean, so only history can catch it")
        r = self.scan_revs("--history")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"{old[:12]}:notes.txt:1: tailnet-ip: {OLD_IP}", r.stdout)

    def test_word_removed_later_still_flagged_by_revs(self):
        with open(self.words, "w") as f:
            f.write("Quokka\n")
        old = self.commit({"notes.txt": "a Quokka sighting\n"})
        self.commit({"notes.txt": "clean\n"})
        r = self.scan_revs("--revs", "HEAD")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"{old[:12]}:notes.txt:1: privacy-word", r.stdout)
        self.assertNotIn("quokka", r.stdout.lower())

    def test_private_author_email_fails(self):
        c = self.commit({"README.md": "fine\n"}, email=PRIVATE_EMAIL)
        r = self.scan_revs("--history")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"{c[:12]}:(author):1: email", r.stdout)
        self.assertIn(f"{c[:12]}:(committer):1: email", r.stdout)

    def test_revs_range_scans_only_that_range(self):
        a = self.commit({"notes.txt": f"node at {OLD_IP}\n"})  # A holds the address
        b = self.commit({"notes.txt": "clean\n"})             # B removes it
        r = self.scan_revs("--revs", f"{a}..{b}")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("privacy: OK (1 commits)", r.stdout)
        r = self.scan_revs("--revs", a)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"{a[:12]}:notes.txt:1: tailnet-ip", r.stdout)

    def test_empty_range_is_ok(self):
        self.commit({"README.md": "fine\n"})
        r = self.scan_revs("--revs", "HEAD..HEAD")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("privacy: OK (0 commits)", r.stdout)

    def test_revs_without_arguments_is_usage_error(self):
        self.commit({"README.md": "fine\n"})
        self.assertEqual(self.scan_revs("--revs").returncode, 2)


class AllowList(PrivacyFixture):
    def setUp(self):
        super().setUp()
        self.allow = os.path.join(self.tmp, "allow.txt")

    def allow_with(self, *tokens):
        with open(self.allow, "w") as f:
            f.write("# test allow list\n" + "\n".join(tokens) + "\n")

    def scan_allowed(self):
        env = clean_env(PRIVACY_WORDS=self.words, PRIVACY_ALLOW=self.allow)
        return subprocess.run(["bash", PRIVACY, self.repo], capture_output=True, text=True, env=env, timeout=60)

    def test_listed_fake_ip_passes(self):
        self.allow_with(TAILNET_IP)
        self.track("README.md", f"example peer {TAILNET_IP}\n")
        r = self.scan_allowed()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_unlisted_ip_still_fails(self):
        self.allow_with(TAILNET_IP)
        self.track("README.md", f"example peer {TAILNET_IP}\nreal peer {OLD_IP}\n")
        r = self.scan_allowed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"README.md:2: tailnet-ip: {OLD_IP}", r.stdout)
        self.assertNotIn(f"README.md:1: tailnet-ip", r.stdout)

    def test_entry_equal_to_a_word_is_usage_error(self):
        with open(self.words, "w") as f:
            f.write("Quokka\n")
        self.track("notes.md", "a Quokka sighting\n")
        for entry in ("Quokka", "quokka"):  # case-insensitive
            self.allow_with(entry)
            r = self.scan_allowed()
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("equals a word-list entry", r.stderr)

    def test_prefix_entry_does_not_mask_a_longer_address(self):
        self.allow_with(".".join(TAILNET_IP.split(".")[:3]))  # a prefix of the address, glued to ".2"
        self.track("README.md", f"peer {TAILNET_IP}\nreal peer {OLD_IP}\n")
        r = self.scan_allowed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"README.md:1: tailnet-ip: {TAILNET_IP}", r.stdout)
        self.assertIn(f"README.md:2: tailnet-ip: {OLD_IP}", r.stdout)

    def test_word_inside_longer_allowed_token_passes_but_elsewhere_fails(self):
        with open(self.words, "w") as f:
            f.write("Quokka\n")
        self.allow_with("QuokkaTown")
        self.track("README.md", "visit QuokkaTown\n")
        self.track("notes.md", "a Quokka sighting\n")
        r = self.scan_allowed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("notes.md:1: privacy-word", r.stdout)
        self.assertNotIn("README.md", r.stdout)

    def test_owner_email_in_same_line_as_allowed_slug_fails(self):
        owner = "rick" + "ndanger"  # built at runtime: the owner's real name never sits in this file
        with open(self.words, "w") as f:
            f.write(f"{owner}\n")
        self.allow_with(f"{owner}-ctrl/dev-studio")
        self.track("README.md", f"repo {owner}-ctrl/dev-studio, mail {owner}@gmail.com\n")
        r = self.scan_allowed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("README.md:1: privacy-word", r.stdout)
        self.assertIn("README.md:1: email", r.stdout)

    def test_per_file_entry_applies_only_in_its_file(self):
        self.allow_with(f"docs/example.py :: {TAILNET_IP}")
        self.track("docs/example.py", f"peer {TAILNET_IP}\n")
        self.assertEqual(self.scan_allowed().returncode, 0)
        self.track("other.txt", f"peer {TAILNET_IP}\n")
        r = self.scan_allowed()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"other.txt:1: tailnet-ip: {TAILNET_IP}", r.stdout)

    def test_email_entry_is_usage_error(self):
        for entry in (PRIVATE_EMAIL, f"docs/example.py :: {PRIVATE_EMAIL}"):
            self.allow_with(entry)
            self.track("README.md", "fine\n")
            r = self.scan_allowed()
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("e-mail address", r.stderr)

    def test_missing_explicit_allow_file_is_usage_error(self):
        self.track("README.md", "fine\n")
        r = self.scan_allowed()  # self.allow was never written
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_default_allow_file_is_read_from_the_project(self):
        script_dir = os.path.join(self.repo, "test")
        os.makedirs(os.path.join(script_dir, ""), exist_ok=True)
        shutil.copy(PRIVACY, os.path.join(script_dir, "privacy.sh"))
        os.makedirs(os.path.join(self.repo, "packaging"), exist_ok=True)
        with open(os.path.join(self.repo, "packaging", "privacy-allow.txt"), "w") as f:
            f.write(f"{TAILNET_IP}\n")
        self.track("README.md", f"peer {TAILNET_IP}\n")
        env = clean_env(PRIVACY_WORDS=self.words)
        r = subprocess.run(["bash", os.path.join(script_dir, "privacy.sh"), self.repo],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


def addr(*parts):
    """Build a dotted value at runtime, so this file never carries the literal text."""
    return ".".join(parts)


class MaskBoundaries(PrivacyFixture):
    """Allowed tokens mask only at word boundaries; each lookalike below must still fail."""

    def setUp(self):
        super().setUp()
        self.allow = os.path.join(self.tmp, "allow.txt")
        with open(self.allow, "w") as f:
            f.write(addr("100", "100", "1", "1") + "\n")                 # allowed IP
            f.write("rick" + "ndanger-ctrl/dev-studio\n")                # allowed slug
            f.write("/" + "/".join(["Users", "alice", ""]) + "\n")       # allowed macOS home prefix
        self.owner = "rick" + "ndanger"
        with open(self.words, "w") as f:
            f.write(self.owner + "\n")

    def check(self, line):
        self.track("line.txt", line + "\n")
        env = clean_env(PRIVACY_WORDS=self.words, PRIVACY_ALLOW=self.allow)
        return subprocess.run(["bash", PRIVACY, self.repo], capture_output=True, text=True, env=env, timeout=60)

    def test_lookalikes_fail(self):
        cases = [
            addr("100", "100", "1", "12"),                 # longer last octet
            addr("100", "100", "1", "1", "5"),             # longer dotted number
            "1" + addr("100", "100", "1", "1"),            # glued to a leading digit
            addr("9", "100", "100", "1", "1"),             # tail of a longer dotted number
            "x" + self.owner + "-ctrl/dev-studio",         # slug glued to a letter, owner word remains
        ]
        for line in cases:
            with self.subTest(line=line):
                r = self.check(line)
                self.assertEqual(r.returncode, 1, line + "\n" + r.stdout + r.stderr)

    def test_boundary_forms_pass(self):
        cases = [
            self.owner + "-ctrl/dev-studio.git",           # slug followed by a dot: a real boundary
            "/" + "/".join(["Users", "alice", "x"]),        # home prefix followed by a name: '/' boundary
        ]
        for line in cases:
            with self.subTest(line=line):
                r = self.check(line)
                self.assertEqual(r.returncode, 0, line + "\n" + r.stdout + r.stderr)


class ProjectInsideOuterRepo(unittest.TestCase):
    """control/ is a subfolder of an outer git repo, with its own test/privacy.sh and .privacy-words."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="privacy-outer-"))
        self.outer = os.path.join(self.tmp, "outer")
        self.control = os.path.join(self.outer, "control")
        os.makedirs(os.path.join(self.control, "test"))
        shutil.copy(PRIVACY, os.path.join(self.control, "test", "privacy.sh"))
        self.write("README.md", "outer readme\n")
        self.write("leak.txt", f"stray address {TAILNET_IP}\n")  # outside control/, must be found from the outer repo
        self.write("control/.privacy-words.example", f"{TAILNET_IP_BIN}\n")  # exempt by its prefixed path
        self.write("control/notes.txt", "a Quokka sighting\n")  # control's own word list flags this
        # control's own word list: untracked, so it must never be in the outer repo's listing
        with open(os.path.join(self.control, ".privacy-words"), "w") as f:
            f.write("Quokka\n")
        subprocess.run(["git", "init", "-q", self.outer], check=True)
        subprocess.run(["git", "-C", self.outer, "add", "README.md", "leak.txt", "control/.privacy-words.example",
                        "control/notes.txt", "control/test/privacy.sh"], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content):
        path = os.path.join(self.outer, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def run_script(self, cwd, *args):
        env = clean_env()  # the project's own word list and allow list apply
        return subprocess.run(["bash", os.path.join(self.control, "test", "privacy.sh"), *args],
                              cwd=cwd, capture_output=True, text=True, env=env, timeout=60)

    def test_outer_scan_covers_all_tracked_files(self):
        r = self.run_script(self.tmp, self.outer)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("leak.txt:1: tailnet-ip", r.stdout)               # outside control/: covered
        self.assertIn("control/notes.txt:1: privacy-word", r.stdout)    # prefixed path, control's word list
        self.assertNotIn(".privacy-words.example", r.stdout)            # exempt by prefixed SELF path
        self.assertNotIn("control/test/privacy.sh", r.stdout)           # the guard itself is exempt

    def test_run_from_control_uses_its_own_words_and_exemptions(self):
        r = self.run_script(self.control)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("notes.txt:1: privacy-word", r.stdout)
        self.assertNotIn("leak.txt", r.stdout)                          # outside this project: not its scan
        self.assertNotIn(".privacy-words.example", r.stdout)

    def test_outer_scan_is_clean_when_outer_content_is_clean(self):
        os.remove(os.path.join(self.outer, "leak.txt"))
        subprocess.run(["git", "-C", self.outer, "rm", "-q", "leak.txt"], check=True)
        os.remove(os.path.join(self.control, "notes.txt"))
        subprocess.run(["git", "-C", self.outer, "rm", "-q", "control/notes.txt"], check=True)
        r = self.run_script(self.tmp, self.outer)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("privacy: OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
