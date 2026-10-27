"""Doc-test for docs/TUTORIAL.md: every sh line runs, in order, and its output is checked.

Recognised lines (any other line fails the test):
  camera-motion <args>        runs as <python> -m camera_motion.cli <args>, in a temp folder
  camera-motion serve ...     runs the server on a free port; GET / must be 200, then it stops
  mkdir -p <dir>              creates <dir> in the temp folder
  python3 -m venv <dir>       venv lines are skipped unless CM_SLOW=1; then a temp venv is
  source <dir>/bin/activate   built, the project is copied and installed into it with pip,
  pip install <path>          and the CLI lines use that venv's python
A "~/" prefix maps into the temp folder, so the examples never touch a real home folder.
"""
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from camera_motion.export.formats import parse_re10k

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # camera-motion/
DOC = os.path.join(ROOT, "docs", "TUTORIAL.md")
SLOW = os.environ.get("CM_SLOW") == "1"
FENCE = re.compile(r"^```sh[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)


def sh_lines():
    with open(DOC, encoding="utf-8") as f:
        text = f.read()
    lines = []
    for block in FENCE.findall(text):
        for raw in block.splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    return lines


def classify(tokens):
    head = tokens[0]
    if head == "camera-motion":
        return "serve" if tokens[1:2] == ["serve"] else "cli"
    if head == "mkdir" and tokens[1:2] == ["-p"] and len(tokens) == 3:
        return "mkdir"
    if head == "python3" and tokens[1:3] == ["-m", "venv"] and len(tokens) == 4:
        return "venv"
    if head == "source" and len(tokens) == 2 and tokens[1].endswith("/bin/activate"):
        return "activate"
    if head == "pip" and tokens[1:2] == ["install"] and len(tokens) == 3:
        return "pip"
    return None


def expand(token, home):
    return os.path.join(home, token[2:]) if token.startswith("~/") else token


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_200(url, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code
        except OSError:
            time.sleep(0.2)
    return None


def check_output(test, path_, text=None):
    """Check one generated file (or stdout text) by its type."""
    if path_.endswith(".svg"):
        test.assertEqual(ET.parse(path_).getroot().tag.split("}")[-1], "svg")
    elif path_.endswith(".json"):
        with open(path_, encoding="utf-8") as f:
            json.load(f)
    elif path_.endswith(".gltf"):
        with open(path_, encoding="utf-8") as f:
            test.assertTrue(json.load(f)["animations"])
    elif path_.endswith(".py"):
        with open(path_, encoding="utf-8") as f:
            compile(f.read(), path_, "exec")
    elif path_.endswith(".txt"):
        with open(path_, encoding="utf-8") as f:
            test.assertTrue(parse_re10k(f.read().splitlines()))
    else:
        test.fail(f"no output check for {path_}")


class TutorialTests(unittest.TestCase):
    def test_every_line_is_recognised(self):
        lines = sh_lines()
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line=line):
                self.assertIsNotNone(classify(shlex.split(line)), line)

    def test_key_steps_are_described(self):
        with open(DOC, encoding="utf-8") as f:
            text = f.read()
        for phrase in ("Scripting", "Text > Open", "Alt+P", "Numpad 0", "Space",
                       "File > Import > glTF 2.0", "http://127.0.0.1:8790/"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_no_real_user_or_host_details(self):
        with open(DOC, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/", text)
        ips = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)
        self.assertEqual(set(ips) - {"127.0.0.1"}, set())

    def test_tutorial_commands_run(self):
        with tempfile.TemporaryDirectory() as base:
            home = os.path.join(base, "home")
            os.makedirs(home)
            env = dict(os.environ)
            env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
            python = sys.executable
            venv_dir = None

            for line in sh_lines():
                tokens = shlex.split(line)
                kind = classify(tokens)
                with self.subTest(line=line):
                    if kind == "mkdir":
                        os.makedirs(expand(tokens[2], home), exist_ok=True)

                    elif kind == "venv" and SLOW:
                        venv_dir = expand(tokens[3], home)
                        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=True, timeout=300)
                        python = os.path.join(venv_dir, "bin", "python")
                        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

                    elif kind == "pip" and SLOW:
                        self.assertIsNotNone(venv_dir, "pip install before venv")
                        src = os.path.join(base, "src")
                        if not os.path.isdir(src):
                            shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(
                                "build", "*.egg-info", "__pycache__", "out", "frames.json"))
                        pip = os.path.join(venv_dir, "bin", "pip")
                        result = subprocess.run([pip, "install", src], capture_output=True,
                                                text=True, timeout=600)
                        self.assertEqual(result.returncode, 0, result.stderr)

                    elif kind == "cli":
                        args = [expand(t, home) for t in tokens[1:]]
                        cmd = [python, "-m", "camera_motion.cli"] + args
                        result = subprocess.run(cmd, cwd=home, env=env, capture_output=True,
                                                text=True, timeout=120)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        if args[0] == "path":
                            data = json.loads(result.stdout)
                            self.assertIn("first", data)
                            self.assertIn("last", data)
                        if args[0] == "doctor":
                            lines = result.stdout.splitlines()
                            self.assertTrue(lines)
                            self.assertTrue(all(line.startswith("PASS") for line in lines), result.stdout)
                        if "-o" in args:
                            out = os.path.join(home, args[args.index("-o") + 1])
                            check_output(self, out)

                    elif kind == "serve":
                        port = str(free_port())
                        args = tokens[1:]
                        if "--port" in args:
                            args[args.index("--port") + 1] = port
                        else:
                            args += ["--port", port]
                        cmd = [python, "-m", "camera_motion.cli"] + args
                        proc = subprocess.Popen(cmd, cwd=home, env=env,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        try:
                            status = wait_for_200(f"http://127.0.0.1:{port}/")
                            self.assertEqual(status, 200)
                        finally:
                            proc.terminate()
                            proc.wait(timeout=10)

                    elif kind in ("activate", "pip", "venv"):
                        continue  # skipped unless CM_SLOW=1 (see module docstring)

                    else:
                        self.fail(f"unrecognised line: {line}")


if __name__ == "__main__":
    unittest.main()
