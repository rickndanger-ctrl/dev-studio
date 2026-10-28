"""A small OpenAI-compatible model server for tests: GET /v1/models and POST /v1/chat/completions.

Standard library only. Replies are canned (reply=...) or an echo of the last user message. delay=<seconds> waits
before answering; status=<code> answers every chat request with that HTTP error. Requests are recorded in
server.requests as (method, path, parsed body), so a test can check what was sent.

As a library:  with FakeModelServer(model="models/Fake-Model.gguf") as srv:  ->  srv.url is http://127.0.0.1:PORT/v1
As a script:   python3 test/fake_model_server.py [--port N] [--reply TEXT] [--delay S] [--status CODE]
"""
import argparse
import http.server
import json
import threading
import time


class FakeModelServer:
    def __init__(self, model="Fake-Model.gguf", reply=None, delay=0.0, status=None, host="127.0.0.1", port=0):
        self.model = model
        self.reply = reply
        self.delay = delay
        self.status = status
        self.host = host
        self.port = port
        self.requests = []
        self._httpd = None
        self._thread = None

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/v1"

    def _handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.rstrip("/") == "/v1/models":
                    server.requests.append(("GET", self.path, None))
                    return self._send(200, {"object": "list", "data": [{"id": server.model, "object": "model"}]})
                self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size) if size else b""
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    body = {}
                server.requests.append(("POST", self.path, body))
                if self.path.rstrip("/") != "/v1/chat/completions":
                    return self._send(404, {"error": {"message": "not found"}})
                if server.delay:
                    time.sleep(server.delay)
                if server.status:
                    return self._send(server.status, {"error": {"message": "fake error"}})
                last = ""
                for m in body.get("messages") or []:
                    if isinstance(m, dict) and m.get("role") == "user":
                        last = str(m.get("content") or "")
                text = server.reply if server.reply is not None else "echo: " + last
                self._send(200, {"id": "fake-1", "object": "chat.completion",
                                 "model": body.get("model") or server.model,
                                 "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant", "content": text}}]})

        return Handler

    def start(self):
        self._httpd = http.server.ThreadingHTTPServer((self.host, self.port), self._handler())
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fake OpenAI-compatible model server for tests.")
    ap.add_argument("--port", type=int, default=0, help="port to listen on (0 = any free port)")
    ap.add_argument("--model", default="Fake-Model.gguf")
    ap.add_argument("--reply", default=None, help="canned reply (default: echo the last user message)")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait before each chat reply")
    ap.add_argument("--status", type=int, default=None, help="answer chat requests with this HTTP error")
    args = ap.parse_args(argv)
    srv = FakeModelServer(model=args.model, reply=args.reply, delay=args.delay, status=args.status, port=args.port)
    srv.start()
    print(srv.url, flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()


if __name__ == "__main__":
    main()
