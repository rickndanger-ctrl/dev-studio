"""The makeclip package: it imports as a package, the promo render runs under the server's own Python,
the default render Python is overridable, `makeclip --help` works, and the packaging lists the entry
point and the page."""

import os
import re
import subprocess
import sys
import unittest
from unittest import mock

from makeclip import server

VIDEO_STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pyproject():
    with open(os.path.join(VIDEO_STUDIO, "pyproject.toml"), encoding="utf-8") as f:
        return f.read()


class RenderCommandTests(unittest.TestCase):
    def test_the_promo_render_starts_with_the_given_python_and_runs_shotplan_as_a_module(self):
        command = server.promo_command(sys.executable, "/plan.json", "/clip", layers=False)
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1:4], ["-m", "shotplan", "render"])
        self.assertFalse(any(part.endswith("shotplan.py") for part in command))
        self.assertNotIn("--matte", command)

    def test_layers_add_the_isnet_matte(self):
        command = server.promo_command(sys.executable, "/plan.json", "/clip", layers=True)
        self.assertEqual(command[-2:], ["--matte", "isnet"])

    def test_the_default_render_python_is_the_servers_own(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(server.RENDER_PYTHON_ENV, None)
            self.assertEqual(server.default_render_python(), sys.executable)

    def test_the_default_render_python_can_be_overridden(self):
        with mock.patch.dict(os.environ, {server.RENDER_PYTHON_ENV: "/opt/some/python"}):
            self.assertEqual(server.default_render_python(), "/opt/some/python")

    def test_the_server_does_not_insert_paths_into_sys_path(self):
        with open(server.__file__, encoding="utf-8") as f:
            self.assertNotIn("sys.path.insert", f.read())


class PackageTests(unittest.TestCase):
    def test_makeclip_help_works(self):
        result = subprocess.run([sys.executable, "-m", "makeclip", "--help"], cwd=VIDEO_STUDIO,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--port", result.stdout)

    def test_default_port_is_8820(self):
        self.assertEqual(server.DEFAULT_PORT, 8820)

    def test_pyproject_lists_the_entry_point_and_the_page(self):
        text = _pyproject()
        self.assertRegex(text, r'(?m)^makeclip = "makeclip\.server:main"$')
        self.assertIn('"makeclip", "makeclip.*"', text)
        self.assertRegex(text, r'makeclip = \["app\.html"\]')

    def test_the_package_has_an_init_and_the_page(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.assertTrue(os.path.exists(os.path.join(here, "__init__.py")))
        self.assertTrue(os.path.exists(os.path.join(here, "app.html")))


if __name__ == "__main__":
    unittest.main()
