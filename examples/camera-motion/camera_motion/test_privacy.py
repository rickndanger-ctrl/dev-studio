"""No personal or internal details in the shipped files: user names, private IPs, hostnames."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__", "build", ".venv"}
SELF = os.path.abspath(__file__)  # holds sample bad strings for the detector tests
ALLOWED_NAMES = {"<you>", "user", "USER", "example", "path", "name"}

HOME_PATH = re.compile(r"(?:/Users|/home)/([^/\s\"'`<>)]+)")
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
LOCAL_HOST = re.compile(r"\b[\w-]+\.local\b", re.IGNORECASE)
WORDS = re.compile(r"Mini [12]|OMAC")


def _text_files():
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            full = os.path.join(folder, name)
            if full == SELF:
                continue
            try:
                with open(full, encoding="utf-8") as f:
                    yield os.path.relpath(full, ROOT), f.read()
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable


def _private_ip(a, b):
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or (a == 100 and 64 <= b <= 127)


def _problems(rel, text):
    found = []
    users = {os.environ.get(k) for k in ("USER", "LOGNAME")} - {None, ""}
    for m in HOME_PATH.finditer(text):
        if m.group(1) not in ALLOWED_NAMES:
            found.append(f"{rel}: home path {m.group(0)!r}")
    for user in users:
        if re.search(r"\b%s\b" % re.escape(user), text):
            found.append(f"{rel}: user name {user!r}")
    for m in IPV4.finditer(text):
        a, b, c, d = (int(g) for g in m.groups())
        if max(a, b, c, d) <= 255 and _private_ip(a, b):
            found.append(f"{rel}: private IP {m.group(0)}")
    for m in LOCAL_HOST.finditer(text):
        found.append(f"{rel}: .local hostname {m.group(0)!r}")
    for m in WORDS.finditer(text):
        found.append(f"{rel}: internal name {m.group(0)!r}")
    return found


class TestPrivacy(unittest.TestCase):
    def test_no_private_details_in_files(self):
        problems = []
        for rel, text in _text_files():
            problems.extend(_problems(rel, text))
        self.assertEqual(problems, [])

    def test_detector_catches_each_kind(self):
        os.environ.setdefault("USER", "someone")
        user = os.environ["USER"]
        samples = ["/Users/alice/x", "/home/bob/y", "ip 192.168.1.5", "ip 10.0.0.2", "ip 172.20.1.1",
                   "ip 100.100.1.1", "box.local", "Mini 1", "Mini 2", "OMAC", f"by {user}"]
        for sample in samples:
            self.assertTrue(_problems("x", sample), sample)

    def test_detector_allows_generic_text(self):
        for sample in ["/Users/<you>/x", "/home/user/y", "/Users/example/z", "http://127.0.0.1:8790/",
                       "~/projects", "/path/to/file", "version 1.2.3.4"]:
            self.assertEqual(_problems("x", sample), [], sample)


if __name__ == "__main__":
    unittest.main()
