"""Doc-test for docs/TUTORIAL.md: every sh line runs, in order, and its output is checked.

Recognised lines (any other line fails the test):
  mkdir -p <dir>                          creates <dir> in the temp home
  python3 -m venv / source / pip install  skipped: they set up an environment outside the test
  python3 -m shotplan <args>              the shot-plan CLI, run from video-studio/ (no install needed)
  shotplan <args>                         the same command after `pip install .`; run the same way
  python3 -m stillmove.cli <args>         the stillmove CLI, run from video-studio/
  stillmove <args>                        the same command after `pip install .`; run the same way
  python3 -m stillmove.web.server <port>  a server: the test uses port 0, GET / must be 200, then it stops
  shotplan serve / python3 -m shotplan serve  the same, with --port

"python3" is this test's own interpreter. A "~/" prefix maps into a temp home, so the tutorial
never writes to a real home folder, and every output must go under "~/": nothing is written into
the project. No pixel values are checked, because the warp is still changing.

camera-motion is put on PYTHONPATH for the tutorial commands, which stands in for the
`pip install ../camera-motion` step. FreshInterpreterTests runs without it, to show that
stillmove finds the sibling checkout by itself.
"""
import json
import os
import re
import shlex
import shutil
import site
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE  # video-studio/
DOC = os.path.join(ROOT, "docs", "TUTORIAL.md")
FENCE = re.compile(r"^```sh[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
SKIPPED = ("venv", "source", "pip")
MODULE_PREFIXES = {
    ("python3", "-m", "shotplan"): "shotplan",
    ("python3", "-m", "stillmove.cli"): "stillmove",
    ("python3", "-m", "stillmove.web.server"): "server",
}
COMMAND_NAMES = {"shotplan": "shotplan", "stillmove": "stillmove"}


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
    if head == "mkdir" and tokens[1:2] == ["-p"] and len(tokens) == 3:
        return "mkdir"
    if head == "python3" and tokens[1:3] == ["-m", "venv"]:
        return "skip"
    if head == "source" and len(tokens) == 2 and tokens[1].endswith("/bin/activate"):
        return "skip"
    if head == "pip" and tokens[1:2] == ["install"]:
        return "skip"
    for prefix, kind in MODULE_PREFIXES.items():
        if tuple(tokens[:3]) == prefix:
            return kind
    if head in COMMAND_NAMES:
        return COMMAND_NAMES[head]
    return None


def command_args(tokens):
    """The arguments after the command, for the module form or the installed command."""
    for prefix in MODULE_PREFIXES:
        if tuple(tokens[:3]) == prefix:
            return tokens[3:]
    if tokens[0] in COMMAND_NAMES:
        return tokens[1:]
    return []


def expand(token, home):
    return os.path.join(home, token[2:]) if token.startswith("~/") else token


def option_value(tokens, flag):
    return tokens[tokens.index(flag) + 1] if flag in tokens else None


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


def check_render_folder(test, folder):
    """A render writes manifest.json (valid JSON) and preview.gif (non-empty); no pixel checks."""
    with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as f:
        test.assertIn("frames", json.load(f))
    test.assertGreater(os.path.getsize(os.path.join(folder, "preview.gif")), 0)


def isolated_env(home):
    """An environment whose HOME is the temp folder. The user site (numpy and Pillow) stays the real one."""
    return dict(os.environ, HOME=home, PYTHONUNBUFFERED="1", PYTHONUSERBASE=site.getuserbase())


class TutorialTests(unittest.TestCase):
    def test_every_line_is_recognised(self):
        lines = sh_lines()
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(line=line):
                self.assertIsNotNone(classify(shlex.split(line)), line)

    def test_outputs_go_under_the_temp_home_and_never_into_the_project(self):
        for line in sh_lines():
            tokens = shlex.split(line)
            kind = classify(tokens)
            args = command_args(tokens)
            outputs = []
            if kind == "mkdir":
                outputs.append(tokens[2])
            if kind == "stillmove" and args[:1] == ["render"]:
                outputs.append(option_value(args, "-o"))
            if kind == "shotplan" and args[:1] == ["render"]:
                outputs.append(args[2])  # render PLAN OUTDIR
            if kind == "shotplan" and args[:1] == ["timeline"]:
                outputs.append(option_value(args, "--svg"))
            for out in outputs:
                with self.subTest(line=line):
                    self.assertTrue(out.startswith("~/"), "output not under ~/: %s" % out)

    def test_no_depth_model_is_used_in_the_tutorial(self):
        with open(DOC, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("da2-small", text)

    def test_no_script_path_form_is_left(self):
        with open(DOC, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("shotplan/shotplan.py", text)

    def test_no_real_user_or_host_details(self):
        with open(DOC, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("/Users/", text)
        self.assertNotIn("/home/", text)
        ips = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)
        self.assertEqual(set(ips) - {"127.0.0.1"}, set())

    def test_tutorial_commands_run(self):
        with tempfile.TemporaryDirectory() as home:
            # The tutorial's `pip install ../camera-motion` line is skipped (it needs a real venv), so
            # camera-motion goes on PYTHONPATH for every command instead: the same import as after the install.
            camera_motion = os.path.normpath(os.path.join(ROOT, "..", "camera-motion"))
            env = dict(isolated_env(home), PYTHONPATH=camera_motion)
            python = sys.executable
            for line in sh_lines():
                tokens = shlex.split(line)
                kind = classify(tokens)
                args = [expand(t, home) for t in command_args(tokens)]
                with self.subTest(line=line):
                    if kind == "mkdir":
                        os.makedirs(expand(tokens[2], home), exist_ok=True)

                    elif kind == "skip":
                        continue

                    elif kind == "stillmove":
                        result = subprocess.run([python, "-m", "stillmove.cli"] + args, cwd=ROOT, env=env,
                                                capture_output=True, text=True, timeout=300)
                        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                        if args[0] == "doctor":
                            lines = result.stdout.splitlines()
                            self.assertTrue(lines)
                            self.assertFalse([ln for ln in lines if ln.startswith("FAIL")], result.stdout)
                        if args[0] == "render":
                            check_render_folder(self, option_value(args, "-o"))

                    elif kind == "shotplan" and args[0] == "serve":
                        self._run_server(python, env, [python, "-m", "shotplan", "serve"], args[1:])

                    elif kind == "shotplan":
                        result = subprocess.run([python, "-m", "shotplan"] + args, cwd=ROOT, env=env,
                                                capture_output=True, text=True, timeout=300)
                        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                        if args[0] == "timeline":
                            ET.parse(option_value(args, "--svg"))
                        if args[0] == "render":
                            check_render_folder(self, args[2])

                    elif kind == "server":
                        self._run_server(python, env, [python, "-m", "stillmove.web.server"], args)

                    else:
                        self.fail("unrecognised line: %s" % line)

    def _run_server(self, python, env, prefix, args):
        """Start a server on a free port, check GET / answers 200, then stop it."""
        args = list(args)
        if "--port" in args:
            args[args.index("--port") + 1] = "0"
        else:
            args = ["0"]  # stillmove's web server takes the port as its only argument
        proc = subprocess.Popen(prefix + args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            banner = proc.stdout.readline()
            found = re.search(r"http://127\.0\.0\.1:(\d+)/", banner)
            status = wait_for_200("http://127.0.0.1:%s/" % found.group(1)) if found else None
            if status != 200:
                proc.terminate()
                _, err = proc.communicate(timeout=10)
                self.fail("server did not answer 200 (banner %r, status %s); stderr:\n%s"
                          % (banner, status, err))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()


class FreshInterpreterTests(unittest.TestCase):
    """stillmove must find camera-motion by itself when it is not installed or on PYTHONPATH."""

    def _python(self):
        # The interpreter running the tests: it has numpy and Pillow wherever the suite runs
        # (a venv, Linux, CI). /usr/bin/python3 often has neither, so the test would only skip.
        return sys.executable

    def test_presets_list_without_camera_motion_installed(self):
        python = self._python()
        with tempfile.TemporaryDirectory() as home:
            env = isolated_env(home)
            deps = subprocess.run([python, "-c", "import numpy, PIL"], env=env, capture_output=True, text=True)
            if deps.returncode != 0:
                self.skipTest("this interpreter has no numpy and Pillow")
            probe = subprocess.run([python, "-c", "import camera_motion"], env=env, capture_output=True, text=True)
            if probe.returncode == 0:
                self.skipTest("camera_motion is importable from this interpreter, so the gap cannot show here")
            result = subprocess.run([python, "-m", "stillmove.cli", "presets"], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dolly_in", result.stdout)
        self.assertIn("orbit_left", result.stdout)


if __name__ == "__main__":
    unittest.main()
