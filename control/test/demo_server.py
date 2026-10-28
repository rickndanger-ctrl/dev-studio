#!/usr/bin/env python3
"""Demo server for the Studio Routing viewer: serves viewer/index.html and /stream with a synthetic state.

Everything on the page is made up (generic agent names, fake commit messages, no real hosts or paths), so
screenshots taken from it are safe to publish.

  python3 test/demo_server.py [port]      default 8800; port 8765 (the live viewer) is refused
  node test/shot.mjs http://127.0.0.1:8800/ docs/img 1280x800 1920x1080

Standard library only.
"""
import http.server, json, os, sys, threading, time

HERE = os.path.dirname(os.path.realpath(__file__))
INDEX = os.path.join(os.path.dirname(HERE), "viewer", "index.html")


def agents(prefix, statuses, kind="claude"):
    names = ["architect", "main", "worker-1", "worker-2"]
    return [{"name": f"{prefix}-{n}", "status": s, "kind": kind, "agent": kind} for n, s in zip(names, statuses)]


def team(name, kind, statuses, routes):
    """One team as the viewer's state carries it (see docs/TWO-TEAMS-CONTRACT.md)."""
    ags = agents(name, statuses, kind)
    return {"team": name, "kind": kind, "idle": not any(s in ("working", "blocked") for s in statuses), "agents": ags,
            "routes": routes, "label": f"{name} · {kind.capitalize()}"}


def route(src, dst, text, ago):
    return {"from": src, "to": dst, "text": text, "decision": "forward", "t": time.time() - ago}


def state():
    now = time.time()
    lap_team = agents("demo", ["working", "working", "done", "idle"])
    return {
        "page": 1,
        "claude": {"last": "Dispatched: add a retry to the export step, then re-run the checks", "at": now - 30},
        "layout": [{"id": "lap", "label": "Laptop", "role": "hub", "enabled": True},
                   {"id": "m1", "label": "Mini 1", "role": "studio", "enabled": True},
                   {"id": "m2", "label": "Mini 2", "role": "studio", "enabled": True},
                   {"id": "m3", "label": "Mini 3", "role": "studio", "enabled": False}],
        "builders": [],
        "laptop": {
            "model": {"name": "local-model", "up": True, "busy": False},
            "projects": [{"name": "Demo Project", "members": ["Analyst"], "tasks": [
                {"title": "Map the export pipeline", "status": "review", "member": "Analyst", "steps": 12, "step": "wrote notes/pipeline.md"},
                {"title": "List the config options", "status": "done", "member": "Analyst", "steps": 7, "step": "wrote notes/config.md"},
                {"title": "Trace the retry path", "status": "failed", "member": "Analyst", "steps": 15, "step": "read export.py"}]}],
            "auditor": {"status": "progressing", "summary": "", "flags": [],
                        "studio_worst": {"id": "m1", "label": "Mini 1", "status": "progressing", "summary": "Workers are on the export retry."},
                        "studios": [{"id": "lap", "label": "Laptop", "status": "progressing", "summary": "Main is wiring the retry."},
                                    {"id": "m1", "label": "Mini 1", "status": "progressing", "summary": "Workers are on the export retry."},
                                    {"id": "m2", "label": "Mini 2", "status": "idle", "summary": "Nobody is working."}]},
            "studio": [{"name": a["name"], "status": a["status"]} for a in lap_team],
            "comms": [route("demo-architect", "demo-main", "Add a retry to export_step: three tries, 2s apart. Test: pytest tests/test_export.py", 400),
                      route("demo-main", "demo-architect", "done: retry added in export.py, 2 new tests, all pass", 140),
                      route("demo-architect", "demo-main", "Reviewed: merge it and update CHANGELOG.md", 22)],
        },
        "minis": {
            "m1": {"label": "Mini 1", "online": True, "note": "direct", "project": "demo-app", "jev": {"router": "jev", "handled": 24, "last": ""},
                   "agents": agents("alpha", ["done", "idle", "working", "working"]) + agents("alpha-codex", ["working", "idle", "working", "idle"], "codex"), "app": [], "mem": "41%",
                   "room": [{"project": "Demo Project", "title": "Draft the preset catalog", "member": "Drafter", "status": "running", "steps": 9, "step": "reading catalog.json"}],
                   "others": [{"name": "claude", "where": "demo-app", "busy": True, "count": 1}, {"name": "codex", "where": "scratch", "busy": False, "count": 1}],
                   "comms": [route("architect", "worker-1", "Job 3: write tests/test_catalog.py for the new presets", 300),
                             route("worker-2", "architect", "done: docs regenerated, commit a1b2c3d", 90),
                             route("architect", "worker-1", "Fix the failing id check, then re-run pytest", 45)],
                   "teams": [team("alpha", "claude", ["done", "idle", "working", "working"],
                                  [route("architect", "worker-1", "Job 3: write tests/test_catalog.py for the new presets", 300),
                                   route("worker-2", "architect", "done: docs regenerated, commit a1b2c3d", 90),
                                   route("architect", "worker-1", "Fix the failing id check, then re-run pytest", 45)]),
                             team("alpha-codex", "codex", ["working", "idle", "working", "idle"],
                                  [route("architect", "worker-1", "Port the exporter to the new schema", 200),
                                   route("worker-1", "architect", "done: exporter ported, 4 tests pass", 70)])]},
            "m2": {"label": "Mini 2", "online": True, "note": "via Mini 1", "project": "demo-app", "jev": {"router": "jev", "handled": 11, "last": ""},
                   "agents": agents("beta", ["done", "idle", "done", "done"]), "app": [], "mem": "66%",
                   "room": [{"project": "Demo Project", "title": "Review the export notes", "member": "Reader", "status": "review", "steps": 14, "step": "wrote findings.md"}],
                   "others": [], "comms": [route("architect", "main", "Job 2: update README with the new flags", 900),
                                           route("main", "architect", "done: README updated, commit 4e5f6a7", 700),
                                           route("architect", "main", "Signed off, merged", 620)],
                   "teams": [team("beta", "claude", ["done", "working", "done", "done"],
                                  [route("architect", "main", "Job 2: update README with the new flags", 900),
                                   route("main", "architect", "done: README updated, commit 4e5f6a7", 700),
                                   route("architect", "main", "Signed off, merged", 620)]),
                             team("beta-codex", "codex", ["idle", "idle", "idle", "idle"], [])]},
            "m3": {"label": "Mini 3", "online": False, "note": "not set up yet", "agents": []},
        },
    }


def history():
    now = time.time()
    rows = [("claude", "lap", "Claude: Dispatched: add a retry to the export step", "claude", None, 60),
            ("lap.demo-architect", "lap.demo-main", "demo-architect → demo-main (Laptop, via JEV): Reviewed: merge it and update CHANGELOG.md", "route", "lap.jev", 22),
            ("m1.alpha-architect", "m1.alpha-worker-1", "alpha-architect → alpha-worker-1 (Mini 1, via JEV): Fix the failing id check, then re-run pytest", "route", "m1.jev", 45),
            ("m1.alpha-worker-2", "m1.alpha-architect", "alpha-worker-2 → alpha-architect (Mini 1, via JEV): done: docs regenerated, commit a1b2c3d", "route", "m1.jev", 90)]
    return [dict(t=now - ago, **{"from": a, "to": b, "text": t, "kind": k}, **({"via": v} if v else {})) for a, b, t, k, v, ago in rows][::-1]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(f"event: state\ndata: {json.dumps(state())}\n\n".encode())
                self.wfile.write(f"event: history\ndata: {json.dumps(history())}\n\n".encode())
                self.wfile.flush()
                while True:
                    time.sleep(15)
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            return
        with open(INDEX, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
    if port == 8765:
        sys.exit("refusing port 8765, the live viewer")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"Demo viewer on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
