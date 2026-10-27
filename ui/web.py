#!/usr/bin/env python3
"""The board as a web page, so any device on the owner's network (a laptop, a
phone) can watch the studio without installing anything.

    web.py --studio DIR --herdr "herdr --session dev-studio" [--port 8787] [--name "Mini 1"] [--peer URL]

Serves:  /        the board, refreshing every 2 s
         /board   the board as text with ANSI colours turned into HTML
         /health  JSON: agents, last route, free memory
Standard library only. Answers only loopback and Tailscale (100.64.0.0/10)
addresses with a matching Host header: the owner's tailnet is the door, and the
LAN, the internet and DNS-rebinding pages stay out. It accepts no input.
"""
import argparse
import html
import ipaddress
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board  # noqa: E402

ANSI = re.compile(r"\x1b\[([0-9;]*)m")
COLORS = {"205": "#ff5faf", "51": "#5fffff", "220": "#ffd700", "84": "#5fff87", "203": "#ff5f5f",
          "141": "#af87ff", "75": "#5fafff", "244": "#8a8a8a"}


def ansi_to_html(text):
    out, open_span = [], False
    pos = 0
    for m in ANSI.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        pos = m.end()
        codes = m.group(1).split(";")
        if open_span:
            out.append("</span>"); open_span = False
        if codes == ["0"] or codes == [""]:
            continue
        style = []
        if "1" in codes:
            style.append("font-weight:bold")
        if "2" in codes:
            style.append("opacity:.7")
        if len(codes) >= 3 and codes[0] == "38" and codes[1] == "5":
            style.append("color:%s" % COLORS.get(codes[2], "#ddd"))
        out.append('<span style="%s">' % ";".join(style)); open_span = True
    out.append(html.escape(text[pos:]))
    if open_span:
        out.append("</span>")
    return "".join(out)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>%(name)s · Dev Studio</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{margin:0;background:#0b0f14;color:#ddd;font:13px/1.35 ui-monospace,Menlo,monospace}
 header{display:flex;gap:16px;align-items:center;padding:10px 16px;border-bottom:1px solid #223;background:#0e131a;position:sticky;top:0}
 header b{color:#fff;font-size:15px} header a{color:#5fafff;text-decoration:none} header .ok{color:#5fff87}
 pre{margin:0;padding:12px 16px;white-space:pre;overflow-x:auto}
</style></head><body>
<header><b>%(name)s</b><span id="stamp" class="ok">live</span>%(peer)s</header>
<pre id="b">loading…</pre>
<script>
async function tick(){try{const r=await fetch('/board?cols='+Math.max(100,Math.floor((window.innerWidth-32)/7.8)));
document.getElementById('b').innerHTML=await r.text();document.getElementById('stamp').textContent='live · '+new Date().toLocaleTimeString();}
catch(e){document.getElementById('stamp').textContent='reconnecting…'}}
tick();setInterval(tick,2000);
</script></body></html>"""


TAILNET = ipaddress.ip_network("100.64.0.0/10")
ALLOWED_HOST_SUFFIXES = (".ts.net",)


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


def make_handler(a):
    ns = argparse.Namespace(studio=a.studio, herdr=a.herdr, kind=a.kind, arch_model=a.arch_model,
                            main_model=a.main_model, worker_model=a.worker_model)
    frame = {"n": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *x):
            pass

        def send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not allowed_client(self.client_address[0]) or not allowed_host(self.headers.get("Host")):
                return self.send(403, "forbidden", "text/plain")
            path, _, query = self.path.partition("?")
            if path == "/":
                peer = ' · <a href="%s">%s</a>' % (html.escape(a.peer), html.escape(a.peer_name)) if a.peer else ""
                return self.send(200, PAGE % {"name": html.escape(a.name), "peer": peer})
            if path == "/board":
                cols = 160
                for part in query.split("&"):
                    if part.startswith("cols="):
                        try:
                            cols = max(100, min(300, int(part[5:])))
                        except ValueError:
                            pass
                frame["n"] += 1
                rows = board.render(ns, frame["n"], cols, 0)
                return self.send(200, "\n".join(ansi_to_html(r) for r in rows))
            if path == "/health":
                live = board.agents(a.herdr)
                log = board.tail_log(os.path.join(a.studio, "router.log"))
                last = next((r for r in reversed(log) if r.get("kind") == "route"), None)
                return self.send(200, json.dumps({"name": a.name, "agents": live, "last_route": last,
                                                  "mem_free_mb": board.mem_free_mb(), "t": time.time()}), "application/json")
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
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), make_handler(a))
    print("studio web · %s · http://0.0.0.0:%d" % (a.name, a.port), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
