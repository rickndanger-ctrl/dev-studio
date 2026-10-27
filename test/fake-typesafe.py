#!/usr/bin/env python3
"""A fake TypeSafe System One endpoint for tests. Answers every request with canned
probabilities and records the request to $FAKE_LOG. Prints its port, then serves."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with open(os.environ["FAKE_LOG"], "a") as f:
            f.write(json.dumps(body) + "\n")
        peers = list(body["questions"]["which_peer"]["criteria"])
        text = body["state"]["text"].lower()
        pick = next((p for p in peers if "worker-1" in p), peers[0])
        action = "drop" if "thanks" in text else ("broadcast" if "schema" in text else "forward")
        probs = {p: 0.02 for p in peers}
        probs[pick] = 0.94
        aprobs = {"forward": 0.04, "broadcast": 0.02, "escalate": 0.02, "drop": 0.02}
        aprobs[action] = 0.92
        ans = {"model": "jev-fake", "usage": {"input_tokens": 1, "output_tokens": 1}, "answers": {
            "which_peer": {"type": "choice", "choice": pick, "confidence": 0.9, "probabilities": probs},
            "action": {"type": "choice", "choice": action, "confidence": 0.9, "probabilities": aprobs},
            "token_leak": {"type": "noul", "noul": 0.9 if len(text) > 600 else 0.05}}}
        out = json.dumps(ans).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), H)
print(srv.server_port, flush=True)
sys.stdout.flush()
srv.serve_forever()
