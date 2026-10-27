#!/usr/bin/env python3
"""The board as a web app, so any device on the owner's Tailscale network (the Dell,
a phone) can watch the studio without installing anything.

    web.py --studio DIR --herdr "herdr --session dev-studio" [--port 8790] [--name "Mini 1"] [--peer URL]

Serves:  /               the board (ui/web/index.html + app.js + app.css)
         /api/state      everything the page draws, as JSON (polled every ~1.5 s)
         /api/agent?name=X   one agent's live terminal tail, messages and history
         /health         JSON: agents, last route, free memory
         /board          the text board with ANSI colours turned into HTML (fallback)

A poller thread refreshes the model once a second so state changes between page
polls are never missed. Standard library only; must run on Python 3.9 (Mini 2
uses Xcode's). Answers only loopback and Tailscale (100.64.0.0/10) addresses with
a matching Host header: the owner's tailnet is the door, and the LAN, the
internet and DNS-rebinding pages stay out. It accepts no input.
"""
import argparse
import html
import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
ALLOWED_HOST_SUFFIXES = (".ts.net",)
ANSI = re.compile(r"\x1b\[([0-9;]*)m")
COLORS = {"205": "#ff5faf", "51": "#5fffff", "220": "#ffd700", "84": "#5fff87", "203": "#ff5f5f",
          "141": "#af87ff", "75": "#5fafff", "244": "#8a8a8a"}


# --- access ---------------------------------------------------------------------

def allowed_client(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_loopback or ip in TAILNET


def allowed_host(host):
    host = (host or "").split(":")[0].strip("[]").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(ALLOWED_HOST_SUFFIXES):
        return True
    return allowed_client(host)


# --- the model the page reads ----------------------------------------------------

def role_of(peer):
    role = (peer or {}).get("role", "")
    for key in ("architect", "main", "worker"):
        if role.startswith(key):
            return key
    return "agent"


def agent_table(herdr):
    """name -> {status, pane, kind} from `herdr agent list` (named agents only)."""
    try:
        out = subprocess.run(herdr.split() + ["agent", "list"], capture_output=True, text=True, timeout=10)
        data = json.loads(out.stdout)
        return {board.clean(a["name"]): {"status": board.clean(a.get("agent_status", "unknown")), "pane": board.clean(a.get("pane_id", "")),
                                         "kind": board.clean(a.get("agent", ""))}
                for a in data["result"]["agents"] if a.get("name")}
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return {}


def agent_tail(herdr, name, lines=40):
    try:
        out = subprocess.run(herdr.split() + ["agent", "read", name, "--source", "recent-unwrapped", "--lines", str(lines)],
                             capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = [board.clean(ln).rstrip() for ln in out.splitlines()]
    while rows and not rows[-1].strip():
        rows.pop()
    return rows[-lines:]


class Studio:
    def __init__(self, a):
        self.a = a
        self.lock = threading.Lock()
        self.tick = 0
        self.peers = {}
        self.team = {}
        self.live = {}
        self.now = {}
        self.events = []
        self.log = []
        self.mail = {"queued": 0, "delivered": 0, "held": 0}
        self.mem = 0
        self.updated = 0

    def refresh(self):
        self.tick += 1
        peers = {}
        try:
            with open(os.path.join(self.a.studio, "peers.json")) as f:
                peers = board.clean_deep(json.load(f))
        except (OSError, ValueError):
            pass
        team = {}
        try:
            with open(os.path.join(self.a.studio, "team.json")) as f:
                team = board.clean_deep(json.load(f))
        except (OSError, ValueError):
            pass
        table = agent_table(self.a.herdr)
        if not table and any(v != "offline" for v in self.live.values()):
            table = getattr(self, "table", {})   # one empty read (Herdr busy) is not "everyone went offline"
        live = {n: table.get(n, {}).get("status", "offline") for n in peers}
        events = []
        for n, st in live.items():
            was = self.live.get(n)
            if was is not None and was != st:
                events.append({"t": time.time(), "kind": "state", "who": n, "was": was, "now": st})
        now = {}
        for n in peers:
            if live.get(n, "offline") != "offline":
                now[n] = board.agent_now(self.a.herdr, n, self.tick)
        log = board.tail_log(os.path.join(self.a.studio, "router.log"), 600)
        mail = {d: board.count(os.path.join(self.a.studio, "mail", d)) for d in ("queued", "delivered", "held")}
        mail["queued"] = board.count(os.path.join(self.a.studio, "mail", "outbox"))
        mem = board.mem_free_mb() if self.tick % 5 == 1 else self.mem
        with self.lock:
            self.peers, self.team, self.table, self.live, self.now = peers, team, table, live, now
            self.events = (self.events + events)[-200:]
            self.log, self.mail, self.mem, self.updated = log, mail, mem, time.time()

    def short_name(self, n):
        names = list(self.peers)
        prefix = ""
        if len(names) > 1:
            first = names[0]
            k = 0
            while k < len(first) and all(len(x) > k and x[k] == first[k] for x in names):
                k += 1
            prefix = first[:k]
        prefix = prefix[: prefix.rfind("-") + 1] if "-" in prefix else ""
        return "@" + (n[len(prefix):] if prefix and n.startswith(prefix) else n)

    def state(self):
        with self.lock:
            peers, team, table, live, now = self.peers, dict(self.team), getattr(self, "table", {}), dict(self.live), dict(self.now)
            events, log, mail, mem = list(self.events), list(self.log), dict(self.mail), self.mem
        routes = [r for r in log if r.get("kind") == "route"]
        deliveries = [r for r in log if r.get("kind") in ("delivered", "held", "failed")]
        agents = []
        for n, p in peers.items():
            sent = next((r for r in reversed(routes) if r.get("from") == n), None)
            got = next((r for r in reversed(routes) if n in r.get("to", [])), None)
            agents.append({"name": n, "short": self.short_name(n), "role": role_of(p), "model": p.get("model", ""),
                           "desc": p.get("role", ""), "status": live.get(n, "offline"), "pane": table.get(n, {}).get("pane", ""),
                           "now": now.get(n, ""), "last_sent": sent, "last_got": got})
        last = routes[-1] if routes else None
        last_model = next((r for r in reversed(routes) if "p_peer" in r), None)
        feed = [r for r in log if r.get("kind") in ("route", "delivered", "held", "failed", "error", "start", "warn")] + events
        feed.sort(key=lambda r: r.get("t", 0))
        recent = [r for r in routes if r.get("t", 0) > time.time() - 60]
        # Every message is two hops: sender -> router -> recipient. The page draws those.
        hops = []
        for r in routes[-150:]:
            for t in r.get("to", []):
                hops.append({"id": r.get("id"), "t": r.get("t", 0), "from": r.get("from"), "to": t, "text": r.get("text", ""),
                             "decision": r.get("decision"), "p": r.get("p_peer"), "sharp": r.get("c_peer", 0) >= 0.5,
                             "verified": r.get("verified", True)})
        return {"name": self.a.name, "peer": {"name": self.a.peer_name, "url": self.a.peer} if self.a.peer else None,
                "t": time.time(), "updated": self.updated, "team": team,
                "router": {"backend": (team.get("router") or os.environ.get("ROUTER", "jev")), "decisions": len(routes),
                           "last": last, "last_model": last_model, "rate_per_min": len(recent)},
                "mail": mail, "mem_free_mb": mem, "agents": agents, "hops": hops, "feed": feed[-150:],
                "deliveries": deliveries[-50:]}

    def agent_detail(self, name):
        with self.lock:
            peers, log, events, live, now = self.peers, list(self.log), list(self.events), dict(self.live), dict(self.now)
        if name not in peers:
            return None
        tail = agent_tail(self.a.herdr, name) if live.get(name, "offline") != "offline" else []
        msgs = [r for r in log if r.get("kind") in ("route", "delivered", "held", "failed") and (r.get("from") == name or name in (r.get("to") or []) or r.get("to") == name)]
        return {"name": name, "short": self.short_name(name), "role": role_of(peers[name]), "desc": peers[name].get("role", ""),
                "model": peers[name].get("model", ""), "status": live.get(name, "offline"), "now": now.get(name, ""),
                "tail": tail, "messages": msgs[-40:], "history": [e for e in events if e.get("who") == name][-40:]}


def poll(studio, stop):
    while not stop.is_set():
        try:
            studio.refresh()
        except Exception as e:  # the poller must survive anything one refresh throws
            sys.stderr.write("refresh failed: %s\n" % e)
        stop.wait(1.0)


# --- the text board, for the fallback route -----------------------------------------

def ansi_to_html(text):
    out, open_span = [], False
    pos = 0
    for m in ANSI.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        pos = m.end()
        codes = m.group(1).split(";")
        if open_span:
            out.append("</span>")
            open_span = False
        if codes == ["0"] or codes == [""]:
            continue
        style = []
        if "1" in codes:
            style.append("font-weight:bold")
        if "2" in codes:
            style.append("opacity:.7")
        if len(codes) >= 3 and codes[0] == "38" and codes[1] == "5":
            style.append("color:%s" % COLORS.get(codes[2], "#ddd"))
        out.append('<span style="%s">' % ";".join(style))
        open_span = True
    out.append(html.escape(text[pos:]))
    if open_span:
        out.append("</span>")
    return "".join(out)


# --- http -------------------------------------------------------------------------

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png"}


def make_handler(a, studio):
    ns = argparse.Namespace(studio=a.studio, herdr=a.herdr, kind=a.kind, arch_model=a.arch_model,
                            main_model=a.main_model, worker_model=a.worker_model)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def static(self, name):
            path = os.path.join(STATIC, name)
            if not os.path.isfile(path):
                return self.send(404, "not found", "text/plain")
            with open(path, "rb") as f:
                return self.send(200, f.read(), MIME.get(os.path.splitext(name)[1], "application/octet-stream"))

        def do_GET(self):
            if not allowed_client(self.client_address[0]) or not allowed_host(self.headers.get("Host")):
                return self.send(403, "forbidden", "text/plain")
            path, _, query = self.path.partition("?")
            params = {}
            for part in query.split("&"):
                k, _, v = part.partition("=")
                if k:
                    params[k] = v
            if path == "/":
                return self.static("index.html")
            if path in ("/app.js", "/app.css"):
                return self.static(path[1:])
            if path == "/api/state":
                return self.send(200, json.dumps(studio.state()), "application/json")
            if path == "/api/agent":
                d = studio.agent_detail(board.clean(params.get("name", "")))
                return self.send(200 if d else 404, json.dumps(d or {"error": "no such agent"}), "application/json")
            if path == "/health":
                with studio.lock:
                    live, log, mem = dict(studio.live), list(studio.log), studio.mem
                last = next((r for r in reversed(log) if r.get("kind") == "route"), None)
                return self.send(200, json.dumps({"name": a.name, "agents": live, "last_route": last, "mem_free_mb": mem, "t": time.time()}), "application/json")
            if path == "/board":
                try:
                    cols = max(100, min(300, int(params.get("cols", "160"))))
                except ValueError:
                    cols = 160
                rows = board.render(ns, studio.tick, cols, 0)
                return self.send(200, "<pre style='background:#0b0f14;color:#ddd;font:13px/1.35 ui-monospace,Menlo,monospace;padding:12px'>"
                                 + "\n".join(ansi_to_html(r) for r in rows) + "</pre>")
            self.send(404, "not found", "text/plain")

    return H


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--studio", required=True)
    p.add_argument("--herdr", default="herdr")
    p.add_argument("--kind", default="claude")
    p.add_argument("--arch-model", default="opus")
    p.add_argument("--main-model", default="sonnet")
    p.add_argument("--worker-model", default="haiku")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--name", default="Dev Studio")
    p.add_argument("--peer", default="", help="URL of the other Mac's board, for the header link")
    p.add_argument("--peer-name", default="other Mac")
    a = p.parse_args()
    studio = Studio(a)
    studio.refresh()
    stop = threading.Event()
    threading.Thread(target=poll, args=(studio, stop), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), make_handler(a, studio))
    print("studio web · %s · port %d" % (a.name, a.port), flush=True)
    try:
        srv.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
