import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
HELP = {
    "builder": "Record a build agent's progress for the Studio Routing viewer.",
    "dispatch": "Send a task brief to a studio architect.",
    "ev": "Log one Claude action to the studio events file.",
    "mini-room.py": "Print the current Room state and findings of the audited app as JSON. Takes the audit block as JSON.",
    "onmini": "Run a script from stdin on a Mini machine",
    "viewer": "Ensure the Studio Routing app server is running and open its window",
    "publish-export": "Export tracked files as a one-commit repo, checking them against a privacy word list.",
    "barstat": "Read the Studio Routing server status and print JSON for the bar widget.",
    "re-audit": "audit Room tasks in the configured app and report progress, stalls, or loops to Claude",
    "studio-snap.py": "snapshot every studio's agents, routes, and recent commits as JSON",
}
BASH = {"dispatch", "ev", "onmini", "viewer", "publish-export"}


class ScriptHelpTest(unittest.TestCase):
    def test_help_flags_exit_before_side_effects(self):
        for name, expected in HELP.items():
            for flag in ("--help", "-h"):
                with self.subTest(script=name, flag=flag), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    (root / "empty-path").mkdir()
                    config = root / "invalid-config.json"
                    config.write_text("invalid json")
                    env = dict(os.environ, HOME=tmp, PATH=str(root / "empty-path"),
                               RE_STUDIO_CONFIG=str(config))
                    command = ["/bin/bash" if name in BASH else sys.executable,
                               str(REPO / "bin" / name), flag]
                    result = subprocess.run(command, cwd=tmp, env=env, input="", text=True,
                                            capture_output=True, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected + "\n")
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(sorted(p.name for p in root.iterdir()),
                                     ["empty-path", "invalid-config.json"])
                    self.assertEqual(config.read_text(), "invalid json")

    def test_bash_syntax(self):
        for name in BASH:
            with self.subTest(script=name):
                result = subprocess.run(["/bin/bash", "-n", str(REPO / "bin" / name)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
