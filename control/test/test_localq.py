"""Localq integration tests using the fake model server."""
import json, os, sys, unittest
from unittest import mock

import test_localq_unit as unit
import localq_models as real_models
from fake_model_server import FakeModelServer


class LocalqServerTest(unittest.TestCase):
    setUp = unit.LocalqTest.setUp
    fake_gate = unit.LocalqTest.fake_gate
    cli = unit.LocalqTest.cli
    load = unit.LocalqTest.load
    draft_input = unit.LocalqTest.draft_input

    def test_new_drafts_run_against_fake_server_and_land_pending(self):
        self.fake_gate(lap=(True, ""))
        with FakeModelServer(reply="draft result") as server:
            with open(self.conf) as f:
                conf = json.load(f)
            conf["machines"][0]["local_model"] = {"kind": "openai", "url": server.url, "model": "fake"}
            with open(self.conf, "w") as f:
                json.dump(conf, f)
            with mock.patch.dict(sys.modules, {"localq_models": real_models}):
                for number, kind in enumerate(("brief", "docstrings"), 1):
                    with self.subTest(kind=kind):
                        prompt = unit.CLI.DRAFT_PROMPTS[kind]
                        self.assertLess(len(prompt), 900)
                        rc, out, err = self.cli("draft", kind, self.draft_input("sample input"))
                        self.assertEqual((rc, out), (0, "draft result\n"), err)
                        jid = err.strip()
                        job = self.load("lap", jid)
                        self.assertEqual((job["kind"], job["status"], job["reviewer"]), (kind, "done", "codex"))
                        self.assertIn(jid, self.cli("results", "--pending")[1])
                        with open(os.path.join(self.state, "localq", "stats.json")) as f:
                            self.assertEqual(json.load(f)["totals"]["lap"]["jobs"], number)
                        request = [body for method, _, body in server.requests if method == "POST"][-1]
                        self.assertIn(prompt, request["messages"][0]["content"])
                        self.assertIn("sample input", request["messages"][0]["content"])



if __name__ == "__main__":
    unittest.main()
