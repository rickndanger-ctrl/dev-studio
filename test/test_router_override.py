"""The override applies immediately and disappears without restarting the router."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("router", os.path.join(ROOT, "router", "router.py"))
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


class OverrideTest(unittest.TestCase):
    def test_override_only_steps_down_from_configured_router(self):
        with tempfile.TemporaryDirectory() as studio:
            path = os.path.join(studio, "router-override")
            for configured, override, expected in (("rules", "jev", "rules"), ("rules", "clef", "rules"),
                                                    ("clef", "jev", "clef"), ("clef", "rules", "rules"),
                                                    ("jev", "clef", "clef"), ("jev", "rules", "rules")):
                with self.subTest(configured=configured, override=override):
                    with open(path, "w") as f:
                        f.write(override + "\nreason\n")
                    with mock.patch.object(router, "ROUTER", configured):
                        self.assertEqual(router.effective_router(studio), expected)

    def test_ask_observes_override_and_restores_configured_router(self):
        with tempfile.TemporaryDirectory() as project:
            studio = os.path.join(project, ".studio")
            os.makedirs(studio)
            with open(os.path.join(studio, "peers.json"), "w") as f:
                json.dump({"worker": {"role": "worker"}, "architect": {"role": "architect"}}, f)
            args = type("Args", (), {"studio": studio, "sender": "worker", "to": "auto", "text": "hello"})()
            path = os.path.join(studio, "router-override")
            with mock.patch.object(router, "ROUTER", "jev"), mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "fake"}):
                with mock.patch.object(router, "ask_model", side_effect=ValueError("test")) as ask:
                    with open(path, "w") as f:
                        f.write("rules\nquota\n")
                    with contextlib.redirect_stdout(io.StringIO()):
                        router.cmd_ask(args)
                    ask.assert_not_called()
                    with open(path, "w") as f:
                        f.write("clef\nstandby\n")
                    with mock.patch.object(router, "CLEF_URL", "https://example.invalid"), mock.patch.object(router, "CLEF_MODEL", "clef-test"), mock.patch.dict(os.environ, {"CLEF_API_KEY": "fake"}):
                        with contextlib.redirect_stdout(io.StringIO()):
                            router.cmd_ask(args)
                        self.assertEqual(ask.call_args.kwargs["model"], "clef-test")
                    ask.reset_mock()
                    os.unlink(path)
                    with contextlib.redirect_stdout(io.StringIO()):
                        router.cmd_ask(args)
                    ask.assert_called_once()


if __name__ == "__main__":
    unittest.main()
