"""lib/localq_models.py: the openai runner against the fake server, and the cli runner with subprocess mocked.

Nothing here calls a Mini or a real model: openai tests use test/fake_model_server.py on a free port, and the
cli tests replace subprocess.run. Each test points RE_STUDIO_CONFIG at its own temp config.
Run from the repo root:  python3 -m unittest discover -s test -p 'test_*.py'
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, HERE)
import localq_models as lm  # noqa: E402
from fake_model_server import FakeModelServer  # noqa: E402

PROMPT_TOKEN = "prompt-token-7f3a"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get("RE_STUDIO_CONFIG")
        self.tmp = tempfile.mkdtemp(prefix="localq-models-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.saved is None:
            os.environ.pop("RE_STUDIO_CONFIG", None)
        else:
            os.environ["RE_STUDIO_CONFIG"] = self.saved

    def use_config(self, machines):
        path = os.path.join(self.tmp, "machines.json")
        with open(path, "w") as f:
            json.dump({"machines": machines}, f)
        os.environ["RE_STUDIO_CONFIG"] = path


class ResolveTest(ConfigTest):
    def test_hub_defaults_to_server_and_studios_have_no_runner(self):
        self.use_config([{"id": "lap", "role": "hub", "ssh": []},
                         {"id": "m1", "role": "studio", "ssh": ["host-a"]}])
        self.assertEqual(lm.resolve("lap"), {"kind": "openai", "url": lm.DEFAULT_OPENAI_URL, "model": ""})
        with self.assertRaisesRegex(lm.RunnerError, "no local runner configured"):
            lm.resolve("m1")

    def test_an_explicit_local_model_entry_wins(self):
        self.use_config([{"id": "lap", "role": "hub", "ssh": [],
                          "local_model": {"kind": "openai", "url": "http://127.0.0.1:9/v1/", "model": "Named.gguf"}}])
        self.assertEqual(lm.resolve("lap"), {"kind": "openai", "url": "http://127.0.0.1:9/v1", "model": "Named.gguf"})

    def test_an_unknown_machine_is_a_runner_error(self):
        self.use_config([{"id": "lap", "role": "hub", "ssh": []}])
        with self.assertRaises(lm.RunnerError):
            lm.resolve("nope")

    def test_short_name_drops_every_directory(self):
        self.assertEqual(lm.short_name("models/Fake-Model.gguf"), "Fake-Model.gguf")
        self.assertEqual(lm.short_name("Plain.gguf"), "Plain.gguf")

    def test_bad_max_chars_is_refused_before_anything_runs(self):
        self.use_config([{"id": "lap", "role": "hub", "ssh": []}])
        for bad in (0, -5, "40", True):
            with self.assertRaises(lm.RunnerError):
                lm.run("lap", "hi", bad)

    def test_invalid_cli_path_and_args_are_refused(self):
        for spec in ({"kind": "cli"}, {"kind": "cli", "path": "/fake/runner", "args": "-p"},
                     {"kind": "cli", "path": "/fake/runner", "args": [1]}):
            with self.subTest(spec=spec):
                self.use_config([{"id": "m1", "role": "studio", "local_model": spec}])
                with self.assertRaisesRegex(lm.RunnerError, "invalid cli path or args"):
                    lm.resolve("m1")


class OpenAIRunnerTest(ConfigTest):
    def point_laptop_at(self, server, model=""):
        self.use_config([{"id": "lap", "role": "hub", "ssh": [],
                          "local_model": {"kind": "openai", "url": server.url, "model": model}}])

    def test_text_seconds_and_a_short_model_name(self):
        with FakeModelServer(model="models/Fake-Model.gguf") as srv:
            self.point_laptop_at(srv)
            out = lm.run("lap", "hello there", 4000, timeout=10)
        self.assertEqual(out["text"], "echo: hello there")
        self.assertIsInstance(out["seconds"], float)
        self.assertGreaterEqual(out["seconds"], 0)
        self.assertEqual(out["model"], "Fake-Model.gguf")
        self.assertNotIn("/", out["model"])
        self.assertNotIn("models", out["model"])

    def test_empty_model_config_asks_the_server_and_uses_its_first_model(self):
        with FakeModelServer(model="models/Served-One.gguf") as srv:
            self.point_laptop_at(srv, model="")
            lm.run("lap", "ping", 500, timeout=10)
        methods = [(m, p) for m, p, _ in srv.requests]
        self.assertIn(("GET", "/v1/models"), methods)
        chat = [body for m, p, body in srv.requests if p == "/v1/chat/completions"][0]
        self.assertEqual(chat["model"], "models/Served-One.gguf")

    def test_configured_model_is_sent_as_given(self):
        with FakeModelServer() as srv:
            self.point_laptop_at(srv, model="Configured.gguf")
            out = lm.run("lap", "ping", 500, timeout=10)
        chat = [body for m, p, body in srv.requests if p == "/v1/chat/completions"][0]
        self.assertEqual(chat["model"], "Configured.gguf")
        self.assertEqual(out["model"], "Configured.gguf")
        self.assertFalse(any(p == "/v1/models" for _, p, _ in srv.requests), "no listing when a model is set")

    def test_the_request_carries_one_user_message_and_a_token_cap(self):
        with FakeModelServer() as srv:
            self.point_laptop_at(srv, model="Configured.gguf")
            lm.run("lap", "a question", 3000, timeout=10)
        chat = [body for m, p, body in srv.requests if p == "/v1/chat/completions"][0]
        self.assertEqual(chat["messages"], [{"role": "user", "content": "a question"}])
        self.assertEqual(chat["max_tokens"], 1000)
        self.assertEqual(chat["temperature"], 0.2)

    def test_the_reply_is_clipped_to_max_chars(self):
        with FakeModelServer(reply="x" * 500) as srv:
            self.point_laptop_at(srv)
            out = lm.run("lap", "long please", 40, timeout=10)
        self.assertEqual(len(out["text"]), 40)
        self.assertEqual(out["text"], "x" * 40)

    def test_a_server_that_is_down_is_a_runner_error(self):
        port = free_port()  # nothing listens here now
        self.use_config([{"id": "lap", "role": "hub", "ssh": [],
                          "local_model": {"kind": "openai", "url": f"http://127.0.0.1:{port}/v1", "model": "X.gguf"}}])
        with self.assertRaises(lm.RunnerError) as ctx:
            lm.run("lap", "hi", 100, timeout=5)
        self.assertIn("unreachable", str(ctx.exception))

    def test_an_http_error_is_a_runner_error(self):
        with FakeModelServer(status=500) as srv:
            self.point_laptop_at(srv, model="X.gguf")
            with self.assertRaises(lm.RunnerError) as ctx:
                lm.run("lap", "hi", 100, timeout=10)
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_a_slow_server_times_out_as_a_runner_error(self):
        with FakeModelServer(delay=1.5) as srv:
            self.point_laptop_at(srv, model="X.gguf")
            with self.assertRaises(lm.RunnerError):
                lm.run("lap", "hi", 100, timeout=0.3)

    def test_a_reply_without_text_is_a_runner_error(self):
        with FakeModelServer() as srv:
            self.point_laptop_at(srv, model="X.gguf")
            srv.reply = None
            srv.requests.clear()
            with mock.patch.object(lm, "_http_json", return_value={"choices": []}):
                with self.assertRaises(lm.RunnerError) as ctx:
                    lm.run("lap", "hi", 100, timeout=10)
        self.assertIn("no text", str(ctx.exception))


class CliRunnerTest(ConfigTest):
    """subprocess.run is mocked: nothing reaches a Mini."""

    CONFIG = [
        {"id": "lap", "role": "hub", "ssh": []},
        {"id": "m1", "role": "studio", "ssh": ["host-a"], "local_model": {"kind": "cli", "path": "~/Fake Tools/runner", "args": ["--prompt", "two words", "$(bad)"]}},
        {"id": "m2", "role": "studio", "ssh": ["host-b"], "ssh_alt": [["host-a", "host-b-internal"]], "local_model": {"kind": "cli", "path": "/fake/runner", "args": []}},
        {"id": "m3", "role": "studio", "enabled": False, "ssh": ["host-c"], "local_model": {"kind": "cli", "path": "/fake/runner"}},
    ]

    def setUp(self):
        super().setUp()
        self.use_config(self.CONFIG)

    def fake_run(self, results):
        """A subprocess.run stand-in that answers each call from results (a CompletedProcess-like or an exception)."""
        calls = []
        queue = list(results)

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return subprocess.CompletedProcess(argv, item[0], stdout=item[1], stderr=item[2])
        return run, calls

    def test_the_route_runs_the_cli_and_the_prompt_goes_on_stdin(self):
        run, calls = self.fake_run([(0, "fake reply\n", "")])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            out = lm.run("m1", PROMPT_TOKEN + " please", 4000, timeout=30)
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("host-a", argv)
        remote = argv[-1]
        self.assertTrue(remote.startswith("~/"), "the Mini's shell expands the home path")
        self.assertEqual(remote, "~/'Fake Tools/runner' --prompt 'two words' '$(bad)'")
        self.assertEqual(kwargs["input"], PROMPT_TOKEN + " please")
        self.assertFalse(any(PROMPT_TOKEN in a for a in argv), "the prompt is never in argv")
        self.assertEqual(out["text"], "fake reply")
        self.assertEqual(out["model"], "runner")
        self.assertNotIn("/", out["model"])

    def test_the_cli_model_label_is_recorded_and_the_binary_name_is_the_fallback(self):
        self.use_config([{"id": "m1", "role": "studio", "ssh": ["host-a"],
                          "local_model": {"kind": "cli", "path": "/fake/runner", "args": [], "model": " Fake-Model-9B "}},
                         {"id": "m2", "role": "studio", "ssh": ["host-b"],
                          "local_model": {"kind": "cli", "path": "/fake/runner", "args": [], "model": ""}}])
        run, _ = self.fake_run([(0, "a", ""), (0, "b", "")])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            self.assertEqual(lm.run("m1", "hi", 100)["model"], "Fake-Model-9B")
            self.assertEqual(lm.run("m2", "hi", 100)["model"], "runner")

    def test_a_non_text_cli_model_label_is_refused(self):
        self.use_config([{"id": "m1", "role": "studio", "ssh": ["host-a"],
                          "local_model": {"kind": "cli", "path": "/fake/runner", "model": 9}}])
        with self.assertRaisesRegex(lm.RunnerError, "invalid cli model label"):
            lm.run("m1", "hi", 100)

    def test_a_connect_failure_falls_back_to_the_next_route(self):
        # ssh exit 255 means it could not connect, so nothing ran on the Mini: the next route is safe to try.
        run, calls = self.fake_run([(255, "", "connection refused"), (0, "second route reply", "")])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            out = lm.run("m2", "hi", 500, timeout=30)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][-2], "host-b", "the primary route first")
        self.assertTrue(any("host-b-internal" in a for a in calls[1][0]), "then the route through the other hop")
        self.assertEqual(out["text"], "second route reply")

    def test_a_model_error_on_the_primary_route_stops_and_is_not_retried(self):
        # Any other exit may mean the job started: the second route reaches the same Mini, so it must not run.
        run, calls = self.fake_run([(1, "", "model crashed"), (0, "would be a second run", "")])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            with self.assertRaises(lm.RunnerError) as ctx:
                lm.run("m2", "hi", 500, timeout=30)
        self.assertEqual(len(calls), 1, "no second route after a model error")
        self.assertIn("not retried", str(ctx.exception))

    def test_a_timeout_on_the_primary_route_stops_and_is_not_retried(self):
        run, calls = self.fake_run([subprocess.TimeoutExpired(cmd="ssh", timeout=1), (0, "second run", "")])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            with self.assertRaises(lm.RunnerError) as ctx:
                lm.run("m2", "hi", 500, timeout=1)
        self.assertEqual(len(calls), 1, "no second route after a timeout")
        self.assertIn("timed out", str(ctx.exception))

    def test_every_route_failing_to_connect_is_a_runner_error(self):
        run, calls = self.fake_run([(255, "", "no"), (255, "", "unreachable")])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            with self.assertRaises(lm.RunnerError) as ctx:
                lm.run("m2", "hi", 500, timeout=30)
        self.assertEqual(len(calls), 2)
        self.assertIn("no route could connect", str(ctx.exception))

    def test_a_single_route_timeout_is_a_runner_error(self):
        run, _ = self.fake_run([subprocess.TimeoutExpired(cmd="ssh", timeout=1)])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            with self.assertRaises(lm.RunnerError) as ctx:
                lm.run("m1", "hi", 500, timeout=1)
        self.assertIn("timed out", str(ctx.exception))

    def test_the_reply_is_clipped_to_max_chars(self):
        run, _ = self.fake_run([(0, "y" * 300, "")])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            out = lm.run("m1", "hi", 50, timeout=30)
        self.assertEqual(out["text"], "y" * 50)

    def test_a_disabled_machine_has_no_route_and_runs_nothing(self):
        run, calls = self.fake_run([])
        with mock.patch.object(lm.subprocess, "run", side_effect=run):
            with self.assertRaises(lm.RunnerError) as ctx:
                lm.run("m3", "hi", 500, timeout=30)
        self.assertEqual(calls, [])
        self.assertIn("no ssh route", str(ctx.exception))

    def test_the_home_path_stays_unquoted_and_other_paths_are_quoted(self):
        self.assertEqual(lm._remote_command("~/Fake Tools/runner", ["-p"]), "~/'Fake Tools/runner' -p")
        self.assertEqual(lm._remote_command("/fake/runner", []), "/fake/runner")


if __name__ == "__main__":
    unittest.main()
