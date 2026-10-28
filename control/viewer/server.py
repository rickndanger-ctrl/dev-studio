#!/usr/bin/env python3
"""Studio viewer: one live picture of Claude, the hub machine's local-model app, and the studios on the other machines.

  python3 server.py [port]      then open http://127.0.0.1:<port>  (default 8765)

Standard library only. Read-only everywhere: it reads the local-model app's data folder on this laptop (the audit.data_dir
config key; it never runs the app), the auditor's state, Claude's event log, and on each studio machine runs `herdr agent list` and
reads the JEV router log over SSH.

Events Claude logs: one JSON line per action in ~/Projects/re-studio/state/claude-events.jsonl,
{"t": <unix>, "to": "<node id>", "text": "..."}; bin/ev writes them.
"""
import glob, hashlib, re, http.server, json, os, queue, shlex, socketserver, subprocess, sys, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
import re_studio_config as cfg
import localq_report
import localq_share
import studio_teams

HOME = os.path.expanduser("~")
STATE = cfg.state_dir()
MAIL_DIR = localq_share.mail_dir()   # the laptop studio's delivered mail, for the local share estimate
HERDR_SESSION = cfg.get("herdr_session", "dev-studio")
HEALTH_PORT = int(cfg.get("model_health_port", 18081))
# Everything owner-specific comes from the config (see docs/CONTROL-CONFIG-CONTRACT.md); these are the generic defaults.
DEFAULT_LABELS = {"claude": "Claude", "lap.member": "Member", "lap.model": "Local model", "lap.haiku": "Auditor"}
LABELS = dict(DEFAULT_LABELS, **{str(k): str(v) for k, v in (cfg.get("labels") or {}).items()})
STUDIO_DIRS = [str(d) for d in (cfg.get("studio_dirs") or ["~/DevStudio"])]
STUDIO_FOLDER_NAMES = {str(n) for n in (cfg.get("studio_folder_names") or ["DevStudio", "worker-1", "worker-2", "worker-3"])}
REMOTE_PATH = str(cfg.get("remote_path") or "$HOME/.local/bin:/opt/homebrew/bin")
_audit = cfg.get("audit")
AUDIT = None   # None hides the audit panel and the app-specific reads below
if isinstance(_audit, dict) and _audit.get("data_dir"):
    AUDIT = {"app": str(_audit.get("app") or ""), "data_dir": str(_audit["data_dir"]),
             "mac_data_dir": str(_audit.get("mac_data_dir") or ""), "process": [str(n) for n in (_audit.get("process") or [])]}
DATA = os.path.expanduser(AUDIT["data_dir"]) if AUDIT else None   # the app's data folder on this machine
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(cfg.get("viewer_port", 8765))

MINI_SCRIPT = r"""
export PATH=@REMOTE_PATH@:$PATH
echo '@@AGENTS'; herdr --session @HERDR_SESSION@ agent list 2>/dev/null | head -c 20000; echo
python3 - <<'PY2'
import glob, os, json
cands = []
for d in @STUDIO_DIRS@:
    base = os.path.join(os.path.expanduser(d), '.studio')
    cands += glob.glob(os.path.join(base, 'router.log'))
    cands += glob.glob(os.path.join(base, 'worktrees', '*', 'router.log')) + glob.glob(os.path.join(base, 'worktrees', '*', '.studio', 'router.log'))
cands += glob.glob(os.path.expanduser('~/Projects/*/.studio/router.log'))
best = None; bestt = -1
for rl in cands:
    try:
        lines = open(rl).read().splitlines()[-120:]
    except Exception:
        continue
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('kind') == 'route' and r.get('t', 0) > bestt:
            bestt = r.get('t', 0); best = rl
if best is None and cands:
    best = max(cands, key=os.path.getmtime)
if best:
    slug = os.path.basename(os.path.dirname(os.path.dirname(best)))
    print('@@PROJECT ' + slug)
    print('@@ROUTER')
    seen = set()
    for rl in [best] + cands:
        real = os.path.realpath(rl)
        if real in seen:
            continue
        seen.add(real)
        try:
            print('\n'.join(open(rl).read().splitlines()[-120:]))
        except Exception:
            pass
PY2
echo '@@APP'; for n in @APP_PROCESSES@; do pgrep -x "$n" >/dev/null 2>&1 && echo "$n"; done
echo '@@ROOM'; python3 - <<'PY' 2>/dev/null
import json, os, time
d = os.path.expanduser(@MAC_DATA_DIR@)
out = []
for p in json.load(open(d + "/projects.json")):
    try:
        r = json.load(open(f"{d}/rooms/{p['id']}.json"))
    except Exception:
        continue
    members = {m["id"]: m.get("name", "?") for m in r.get("members", [])}
    for t in r.get("tasks", []):
        item = {"project": p["name"], "title": t["title"], "member": members.get(t.get("assignee"), "member"),
                "status": "failed" if t.get("failed") else t.get("status", "todo"), "steps": 0, "step": ""}
        if t.get("threadId"):
            try:
                m = json.load(open(f"{d}/conversations/{t['threadId']}.json"))["messages"][-1]
            except Exception:
                m = {}
            calls = [e for e in m.get("events", []) if e.get("type") == "tool_use"]
            if m.get("state") == "writing":
                item["status"] = "running"
            item["steps"] = len(calls)
            item["calls"] = [{"name": c.get("name"), "output": str(c.get("output", ""))[:300]} for c in calls[-6:]]
            try:
                th = json.load(open(f"{d}/conversations/{t['threadId']}.json"))["messages"]
            except Exception:
                th = []
            asks = [x for x in th if x.get("role") == "user"]
            item["asks"] = len(asks)
            item["ask"] = (asks[-1].get("content", "") if asks else "")[:800]
            item["answer"] = (m.get("content", "") if m.get("state") == "done" else "")[:800]
        out.append(item)
print(json.dumps(out))
PY
echo '@@HOME'; echo "$HOME"
echo '@@PROCS'; for p in $(pgrep -x claude; pgrep -x codex); do ps -o command= -p $p | grep -q 'app-server' && continue; c=$(ps -o %cpu= -p $p 2>/dev/null); n=$(ps -o comm= -p $p 2>/dev/null); d=$(lsof -a -p $p -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1); t=$(ps -o tty= -p $p 2>/dev/null); echo "$p|$c|$(basename "$n")|$d|$t"; done
echo '@@MEM'; memory_pressure 2>/dev/null | tail -1 | grep -o '[0-9]*%'
echo '@@END'
"""



def mini_script(mid):
    """The probe script run on one studio machine; its own project folder is searched before the shared studio_dirs."""
    own = MINIS[mid].get("project")
    dirs = ([str(own)] if own else []) + [d for d in STUDIO_DIRS if d != own]
    return (MINI_SCRIPT.replace("@HERDR_SESSION@", HERDR_SESSION).replace("@REMOTE_PATH@", REMOTE_PATH)
            .replace("@STUDIO_DIRS@", json.dumps(dirs)).replace("@MAC_DATA_DIR@", json.dumps((AUDIT or {}).get("mac_data_dir", "")))
            .replace("@APP_PROCESSES@", " ".join(shlex.quote(n) for n in (AUDIT or {}).get("process", [])) or "''"))

# How to reach each Mini: every route in the config (direct first, then via another machine).
MINIS = {m["id"]: {"label": m.get("label", m["id"]), "project": m.get("project"), "ssh": cfg.routes(m["id"], "bash -s")}
         for m in cfg.machines("studio")}

# Every machine in config order (hub, studios, disabled ones too) for the page's layout.
LAYOUT = [{"id": m["id"], "label": m.get("label", m["id"]), "role": m.get("role", ""),
           "enabled": m.get("enabled", True) is not False} for m in cfg.machines()]
HUB_LABEL = next((m["label"] for m in LAYOUT if m["role"] == "hub"), "Hub")

lock = threading.Lock()
# Every studio-role machine gets a minis entry, even one with no routes (enabled:false or empty ssh).
state = {"claude": {"last": ""}, "laptop": {}, "layout": LAYOUT, "labels": LABELS, "audit": AUDIT,
         "minis": {k: {"label": v["label"], "online": False, "agents": [],
                       "note": "connecting" if v["ssh"] else "not set up yet"}
                   for k, v in MINIS.items()}}
history = []          # recent events, newest last
listeners = []


def emit(src, dst, text, kind="info", via=None):
    ev = {"t": time.time(), "from": src, "to": dst, "text": text[:900], "kind": kind}
    if via:
        ev["via"] = via
    with lock:
        history.append(ev)
        del history[:-200]
        for q in listeners:
            q.put(("event", ev))


def publish():
    with lock:
        state["page"] = os.path.getmtime(os.path.join(HERE, "index.html"))
        snap = json.loads(json.dumps(state))
        for q in listeners:
            q.put(("state", snap))


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def step_text(call, n=200, who=""):
    """A tool call as plain English: "is reading status.go, lines 169-265"."""
    raw = call.get("input") or call.get("output", "")
    try:
        a = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        a = {}
    if not isinstance(a, dict):
        a = {}
    name = call.get("name", "")
    path = a.get("path", "")
    base = os.path.basename(path.rstrip("/")) or path
    if name == "read":
        start = a.get("start") or 1
        lines = a.get("lines")
        span = f", lines {start}-{start + lines - 1}" if lines else (f", from line {start}" if a.get("start") else "")
        text = f"is reading {base}{span}"
    elif name == "list":
        text = f"is looking through the folder {path or '.'}"
    elif name in ("search", "grep", "find"):
        what = a.get("pattern") or a.get("query") or a.get("regex") or ""
        text = f'searched for "{what}"' + (f" in {path}" if path else "")
    elif name == "run":
        text = f"ran a command: {a.get('command', '')}"
    elif name == "todo":
        items = a.get("items") or []
        done = [i.get("text", "") for i in items if i.get("done")]
        nxt = [i.get("text", "") for i in items if not i.get("done")]
        text = f"updated its plan ({len(done)} of {len(items)} done)" + (f". Next: {nxt[0]}" if nxt else ". Everything on it is done.")
    elif name in ("write", "create"):
        text = f"wrote {path}"
    elif name == "edit":
        text = f"edited {path}"
    else:
        text = f"used {name}" + (f" on {path}" if path else "")
    return short((who + " " if who else "") + text, n)


def short(v, n=90):
    s = v if isinstance(v, str) else json.dumps(v)
    return " ".join(s.split())[:n]


# ---------- laptop ----------

def _role(n):
    m = re.search(studio_teams.ROLE_RX, n or "")
    return m.group(1) if m else (n or "?")


def laptop_routes():
    """Route rows from this laptop's studio router log ($LAST_PROJECT/.studio/router.log), oldest first."""
    try:
        proj = ""
        with open(os.path.expanduser("~/.dev-studio/studio.conf")) as f:
            for ln in f:
                if ln.startswith("LAST_PROJECT="):
                    proj = ln.split("=", 1)[1].strip().strip("\"'")
        with open(os.path.join(os.path.expanduser(proj), ".studio", "router.log")) as f:
            lines = f.read().splitlines()[-120:]
    except Exception:
        return []
    rows = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("kind") == "route":
            rows.append(r)
    return rows


def laptop_loop():
    seen = {}                 # task id -> (status, failed, steps)
    talks = {}                # task id -> (messages from Claude, answered)
    haiku_at = None
    studio_worst_seen = None
    claude_pos = None
    builder_seen = {}
    alerts_n = None
    route_seen, route_first = set(), True
    while True:
        try:
            lap = {"model": {}, "projects": [], "auditor": {}}
            try:
                h = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{HEALTH_PORT}/health", timeout=3).read())
                lap["model"] = {"name": LABELS["lap.model"], "up": True, "busy": h.get("slots_processing", 0) > 0}
            except Exception:
                # A model busy writing can answer slowly; the port being open still means it is running.
                import socket
                try:
                    socket.create_connection(("127.0.0.1", HEALTH_PORT), timeout=1).close()
                    lap["model"] = {"name": LABELS["lap.model"], "up": True, "busy": True}
                except OSError:
                    lap["model"] = {"name": LABELS["lap.model"], "up": False, "busy": False}
            for proj in (load(DATA + "/projects.json", []) or []) if DATA else []:
                room = load(f"{DATA}/rooms/{proj['id']}.json", {}) or {}
                members = {m["id"]: m.get("name", "?") for m in room.get("members", [])}
                ptasks = []
                for t in room.get("tasks", []):
                    th = load(f"{DATA}/conversations/{t['threadId']}.json") if t.get("threadId") else None
                    last = th["messages"][-1] if th and th.get("messages") else {}
                    live = last.get("state") == "writing"
                    calls = [e for e in last.get("events", []) if e.get("type") == "tool_use"]
                    status = "running" if live else ("failed" if t.get("failed") else t.get("status", "todo"))
                    who = members.get(t.get("assignee"), "member")
                    ptasks.append({"title": t.get("title"), "status": status, "member": who, "steps": len(calls),
                                   "step": step_text(calls[-1], 70) if calls else ""})
                    key = t["id"]
                    msgs = (th or {}).get("messages", [])
                    asks = [m for m in msgs if m.get("role") == "user"]
                    answer = last.get("content", "") if last.get("state") == "done" else ""
                    talk = (len(asks), bool(answer))
                    prev_talk = talks.get(key)
                    if prev_talk is not None:
                        if len(asks) > prev_talk[0]:
                            emit("claude", "lap.member", f'Claude → {who}: {short(asks[-1].get("content", ""), 700)}', "say")
                        if answer and not prev_talk[1]:
                            emit("lap.member", "claude", f'{who} → Claude: {short(answer, 700)}', "say")
                    talks[key] = talk
                    prev = seen.get(key)
                    cur = (status, len(calls))
                    if prev is not None:
                        if prev[0] != status:
                            if status == "running":
                                emit("claude", "lap.member", f'start: {t.get("title")}', "task")
                            elif status in ("review", "done"):
                                emit("lap.member", "claude", f'finished: {t.get("title")}', "done")
                            elif status == "failed":
                                emit("lap.member", "claude", f'stopped: {t.get("title")} ({t.get("note", "")})', "alert")
                            elif status == "todo" and prev[0] in ("review", "failed"):
                                emit("claude", "lap.member", f'sent back: {t.get("title")}', "task")
                        if live and len(calls) > prev[1]:
                            for c in calls[prev[1]:]:
                                emit("lap.member", "lap.model", step_text(c, who=who), "step")
                    seen[key] = cur
                lap["projects"].append({"name": proj["name"], "members": list(members.values()), "tasks": ptasks})
            try:
                r = subprocess.run(["herdr", "--session", HERDR_SESSION, "agent", "list"], capture_output=True, text=True, timeout=5)
                lap["studio"] = [{"name": a.get("name"), "status": a.get("agent_status", "?")}
                                 for a in json.loads(r.stdout).get("result", {}).get("agents", []) if a.get("name")]
            except Exception:
                lap["studio"] = []
            # This laptop's own studio: JEV routes, as feed events and the last few for the panel.
            routes = laptop_routes()
            comms = []
            for r in routes:
                tos = r.get("to") or ([r.get("addressed")] if r.get("addressed") not in (None, "auto") else [])
                comms.append({"from": _role(r.get("from")), "to": ", ".join(_role(t) for t in tos) if tos else "dropped",
                              "text": r.get("text", ""), "decision": r.get("decision", ""), "t": r.get("t", 0)})
                if r.get("id") in route_seen:
                    continue
                route_seen.add(r.get("id"))
                if route_first:
                    continue
                for to in r.get("to") or ["architect"]:
                    said = f'{r.get("from")} → {to} ({HUB_LABEL}, via JEV): {r.get("text", "")}'
                    if r.get("decision") not in ("forward", None):
                        said += f'  [JEV chose {r.get("decision")}]'
                    emit(f'lap.{r.get("from")}', f"lap.{to}", said, "route", via="lap.jev")
            route_first = False
            lap["comms"] = comms[-3:]
            # Sonnet/Claude build agents I (Claude) have delegated — so live building shows on the page.
            builders = load(STATE + "/builders.json", {}) or {}
            def _machine(w):
                w = (w or "").lower()
                for mid, v in MINIS.items():   # a studio machine whose id or label appears in "where"; else the hub
                    if mid.lower() in w or v["label"].lower().replace(" ", "") in w.replace(" ", ""):
                        return mid
                return "lap"
            blist = [{"id": k, "task": v.get("task", k), "where": v.get("where", ""), "machine": _machine(v.get("where")),
                      "status": v.get("status", "running")}
                     for k, v in sorted(builders.items(), key=lambda kv: kv[1].get("at", 0))
                     if v.get("status") == "running" or v.get("at", 0) > time.time() - 90]
            for b in blist:
                prev = builder_seen.get(b["id"])
                if prev != b["status"]:
                    if b["status"] == "running" and prev is None:
                        emit("claude", "builder." + b["id"], f'start build: {b["task"]}', "task")
                    elif b["status"] in ("done", "ok"):
                        emit("builder." + b["id"], "claude", f'build done: {b["task"]}', "done")
                    elif b["status"] == "fail":
                        emit("builder." + b["id"], "claude", f'build FAILED: {b["task"]}', "alert")
                    builder_seen[b["id"]] = b["status"]
            aud = (load(STATE + "/latest.json", {}) or {}) if AUDIT else {}
            v = aud.get("verdict") or {}
            # studio_worst (worst Mini studio, from the reviewer) wins when present; else the laptop verdict.
            sw = aud.get("studio_worst") if isinstance(aud.get("studio_worst"), dict) else None
            studios = aud.get("studios") or []
            if isinstance(studios, dict):
                studios = [dict(s, id=k) for k, s in studios.items() if isinstance(s, dict)]
            # Only the small fields: the reviewer's snapshots carry agents, routes and commits too.
            studios = [{k: s.get(k) for k in ("id", "label", "status", "summary", "concern")} for s in studios if isinstance(s, dict)]
            stats_line = localq_report.auditor_line(load(STATE + "/audit-stats.json", {}) or {}, time.strftime("%Y-%m-%d")) if AUDIT else ""
            lap["auditor"] = {} if AUDIT is None else {"stats_line": stats_line, "status": v.get("status", "idle"), "summary": v.get("summary", ""), "flags": aud.get("flags", []),
                              "studio_worst": sw, "studios": studios}
            if haiku_at is not None and aud.get("haiku_at") and aud.get("haiku_at") != haiku_at:
                emit("lap.haiku", "claude", f'{LABELS["lap.haiku"]} → Claude: {v.get("summary", "")}' + (f' Concern: {v.get("concern")}' if v.get("concern") else ""), "alert" if v.get("needs_claude") else "audit")
            haiku_at = aud.get("haiku_at")
            # Feed line when the worst studio's status or summary changes (not on the first reading).
            if sw:
                sw_key = (sw.get("status"), sw.get("summary"))
                if studio_worst_seen is not None and sw_key != studio_worst_seen:
                    st = sw.get("status") or "?"
                    emit("lap.haiku", "claude", f'{LABELS["lap.haiku"]} → Claude: {sw.get("label") or sw.get("id")} {st}: {sw.get("summary") or ""}',
                         "audit" if st in ("progressing", "idle") else "alert")
                studio_worst_seen = sw_key
            # Claude's own actions
            try:
                with open(STATE + "/claude-events.jsonl") as f:
                    lines = f.readlines()
            except FileNotFoundError:
                lines = []
            if claude_pos is None:
                claude_pos = len(lines)
            for line in lines[claude_pos:]:
                try:
                    e = json.loads(line)
                    emit("claude", e.get("to", "claude"), "Claude: " + e.get("text", ""), e.get("kind", "claude"))
                    with lock:
                        state["claude"]["last"] = e.get("text", "")
                        state["claude"]["at"] = e.get("t", time.time())
                except Exception:
                    pass
            claude_pos = len(lines)
            with lock:
                lap["local"] = (state.get("laptop") or {}).get("local", {})  # keep the local-work summary (refresh_local)
                state["laptop"] = lap
                state["builders"] = blist
            publish()
        except Exception as e:
            print("laptop:", e, file=sys.stderr)
        time.sleep(2)


# ---------- minis ----------

def parse_mini(out):
    parts, cur = {}, None
    for line in out.splitlines():
        if line.startswith("@@"):
            word = line[2:].split(" ", 1)
            cur = word[0]
            parts[cur] = [word[1]] if len(word) > 1 else []
        elif cur:
            parts[cur].append(line)
    agents = []
    try:
        raw = json.loads("\n".join(parts.get("AGENTS", [])) or "{}")
        for a in raw.get("result", {}).get("agents", []):
            nm = a.get("name") or ""
            if not re.search(studio_teams.ROLE_RX, nm):
                continue   # skip unnamed / pane-id agents (stale detection)
            agents.append({"name": nm, "status": a.get("agent_status", "?"), "kind": a.get("agent", ""), "agent": a.get("agent", "")})
    except Exception:
        pass
    rows = []
    for line in parts.get("ROUTER", []):
        try:
            rows.append((hashlib.sha1(line.encode()).hexdigest(), json.loads(line)))
        except Exception:
            pass
    proj = (parts.get("PROJECT") or [""])[0]
    try:
        room = json.loads("\n".join(parts.get("ROOM", [])) or "[]")
    except Exception:
        room = []
    mem = (parts.get("MEM") or [""])[0].strip()
    home = ((parts.get("HOME") or [""])[0].strip()).rstrip("/")
    procs = []
    for line in parts.get("PROCS", []):
        bits = line.split("|")
        if len(bits) < 5:
            continue
        pid, cpu, name, cwd, tty = bits[:5]
        try:
            cpu = float(cpu)
        except ValueError:
            cpu = 0.0
        procs.append({"pid": pid, "cpu": cpu, "name": name, "where": "~" if home and cwd.rstrip("/") == home else os.path.basename(cwd.rstrip("/")) or cwd, "tty": tty.strip()})
    return agents, rows, proj, [x for x in parts.get("APP", []) if x.strip()], room, mem, procs


TEAM_KIND_NAME = {"claude": "Claude", "codex": "Codex"}


def route_item(row):
    """One route row as the viewer draws it: roles only for from/to, "dropped" when it went nowhere."""
    tos = row.get("to") or ([row.get("addressed")] if row.get("addressed") not in (None, "auto") else [])
    return {"from": _role(row.get("from")), "to": ", ".join(_role(t) for t in tos) if tos else "dropped",
            "text": row.get("text", ""), "decision": row.get("decision", ""), "t": row.get("t", 0)}


def build_teams(agents, rows):
    """Group a machine's agents into teams (lib/studio_teams) and give each its last 30 route rows and a label."""
    teams = studio_teams.group(agents)
    # route rows only, oldest first
    routes = sorted((r for _h, r in rows if r.get("kind") == "route"), key=lambda r: r.get("t", 0))
    for t in teams:
        t["routes"] = []
    by_name = {t["team"]: t for t in teams}
    for r in routes:
        # team_of_route wants one recipient, not the list
        probe = dict(r, to=(r.get("to") or [None])[0] if isinstance(r.get("to"), list) else r.get("to"))
        name = studio_teams.team_of_route(probe)
        if name in by_name:
            by_name[name]["routes"].append(route_item(r))
    for t in teams:
        t["routes"] = t["routes"][-30:]
        t["label"] = f'{t["team"] or "studio"} · {TEAM_KIND_NAME.get(t["kind"], "Mixed")}'
    return teams


def mini_loop(mid):
    mcfg = MINIS[mid]
    seen_rows, statuses, first = set(), {}, True
    room_seen = {}
    other_busy = {}
    while True:
        if not mcfg["ssh"]:
            with lock:
                state["minis"][mid].update(online=False, note="not set up yet")
            publish()
            time.sleep(30)
            continue
        out, route = None, None
        for cmd, hops in mcfg["ssh"]:
            try:
                r = subprocess.run(cmd, input=mini_script(mid), capture_output=True, text=True, timeout=25)
                if "@@END" in r.stdout:
                    out, route = r.stdout, cfg.route_label(hops)
                    break
            except Exception:
                pass
        if out is None:
            with lock:
                state["minis"][mid].update(online=False, note="unreachable")
            publish()
            time.sleep(15)
            continue
        agents, rows, proj, bt, room, mem, procs = parse_mini(out)
        all_agents = agents   # every studio agent on the machine, all teams; `agents` below is scoped to the active project
        # Keep only the active studio's agents (named "<project-slug>-role"); fall back to all if no match.
        if proj:
            scoped = [a for a in agents if str(a.get("name", "")).startswith(proj + "-")]
            if scoped:
                agents = scoped
        # Every other Claude / Codex session on the Mini (desktop apps, glasses sessions, other work), not only the studio's.
        studio_cwds = STUDIO_FOLDER_NAMES
        others = []
        for pr in procs:
            if pr["where"] in studio_cwds:
                continue
            busy = pr["cpu"] >= 1.0
            same = next((o for o in others if o["name"] == pr["name"] and o["where"] == pr["where"]), None)
            if same:
                same["count"] += 1; same["busy"] = same["busy"] or busy
            else:
                others.append({"name": pr["name"], "where": pr["where"], "busy": busy, "count": 1})
            # Report a change only once it has held for two checks, so a CPU blip is not news.
            k = f'{mid}:{pr["pid"]}'
            shown, pending_n = other_busy.get(k, (busy, 0))
            if busy != shown:
                pending_n += 1
                if pending_n >= 2:
                    if not first:
                        emit(f"{mid}.other", f"{mid}.other",
                             f'{mcfg["label"]}: {pr["name"]} in {pr["where"]} ' + ("started working" if busy else "went quiet"), "status")
                    shown, pending_n = busy, 0
            else:
                pending_n = 0
            other_busy[k] = (shown, pending_n)
        for t in room:
            key = (t["project"], t["title"])
            prev = room_seen.get(key)
            if prev is not None and not first:
                if prev[0] != t["status"]:
                    if t["status"] == "running":
                        emit("claude", f"{mid}.bt", f'{mcfg["label"]} {t["member"]}: start {t["title"]}', "task")
                    elif t["status"] in ("review", "done"):
                        emit(f"{mid}.bt", "claude", f'{mcfg["label"]} {t["member"]} finished: {t["title"]}', "done")
                    elif t["status"] == "failed":
                        emit(f"{mid}.bt", "claude", f'{mcfg["label"]} {t["member"]} stopped: {t["title"]}', "alert")
                if t["status"] == "running" and t["steps"] > prev[1]:
                    for c in t.get("calls", [])[-(t["steps"] - prev[1]):]:
                        emit(f"{mid}.bt", f"{mid}.bt", step_text(c, who=f'{t["member"]} ({mcfg["label"]})'), "step")
            if prev is not None and not first:
                if t.get("asks", 0) > prev[2]:
                    emit("claude", f"{mid}.bt", f'Claude → {t["member"]} ({mcfg["label"]}): {short(t.get("ask", ""), 700)}', "say")
                if t.get("answer") and not prev[3]:
                    emit(f"{mid}.bt", "claude", f'{t["member"]} ({mcfg["label"]}) → Claude: {short(t["answer"], 700)}', "say")
            room_seen[key] = (t["status"], t["steps"], t.get("asks", 0), bool(t.get("answer")))
            if t.get("calls"):
                t["step"] = step_text(t["calls"][-1], 60)
            t.pop("calls", None); t.pop("ask", None); t.pop("answer", None)
        for a in agents:
            node = f"{mid}.{a['name']}"
            if not first and statuses.get(node) not in (None, a["status"]):
                emit(node, node, f"{mcfg['label']} {a['name']}: {statuses[node]} → {a['status']}",
                     "alert" if a["status"] == "blocked" else "status")
            statuses[node] = a["status"]
        # Persistent record of who communicated with whom (JEV routes), so it shows even when agents are idle.
        comms = []
        for _h, _r in rows:
            if _r.get("kind") == "route":
                tos = _r.get("to") or ([_r.get("addressed")] if _r.get("addressed") not in (None, "auto") else [])
                comms.append({"from": _role(_r.get("from")),
                              "to": ", ".join(_role(t) for t in tos) if tos else "dropped",
                              "text": _r.get("text", ""), "decision": _r.get("decision", ""), "t": _r.get("t", 0)})
        comms = comms[-5:]
        teams = build_teams(all_agents, rows)
        jev = {"router": "", "handled": 0, "last": ""}
        for h, row in rows:
            k = row.get("kind")
            if k == "health":
                jev["handled"] = row.get("handled", 0)
            if k == "start":
                jev["router"] = row.get("router", "")
            if h in seen_rows:
                continue
            seen_rows.add(h)
            if first:
                continue
            if k == "route":
                tos = row.get("to") or []
                label = f'{row.get("decision")} {short(row.get("text", ""), 80)}'
                for to in tos or ["architect"]:
                    said = f'{row.get("from")} → {to} ({mcfg["label"]}, via JEV): {row.get("text", "")}'
                    if row.get("decision") not in ("forward", None):
                        said += f'  [JEV chose {row.get("decision")}]'
                    emit(f"{mid}.studio-{row.get('from')}", f"{mid}.studio-{to}", said, "route", via=f"{mid}.jev")
                jev["last"] = label
            elif k in ("failed", "undeliverable", "error", "warn"):
                emit(f"{mid}.jev", f"{mid}.jev", f'{k}: {short(row, 100)}', "alert")
        with lock:
            prev = state["minis"][mid]
            if not jev["last"]:
                jev["last"] = prev.get("jev", {}).get("last", "")
            prev.update(online=True, note=route, agents=agents, teams=teams, project=os.path.basename(proj), jev=jev, app=bt, room=room, mem=mem, others=others, comms=comms)
        publish()
        first = False
        time.sleep(8)


# ---------- local work (localq: jobs that run on local models) ----------

def local_state(mid):
    """One machine's local-work counts from localq's job files, plus its latest blocked reason."""
    out = {"queued": 0, "running": 0, "running_id": "", "held": 0, "blocked": "", "done_today": 0, "done_today_chars": 0}
    today = time.strftime("%Y-%m-%d")
    latest_block = -1
    for path in glob.glob(os.path.join(STATE, "localq", "jobs", mid, "*.json")):
        j = load(path, {}) or {}
        status = j.get("status")
        if status == "queued":
            out["queued"] += 1
        elif status == "running":
            out["running"] += 1
            out["running_id"] = str(j.get("id") or os.path.splitext(os.path.basename(path))[0])
        elif status == "done" and time.strftime("%Y-%m-%d", time.localtime(j.get("finished_at") or 0)) == today:
            out["done_today"] += 1
            out["done_today_chars"] += int(j.get("chars_in") or 0) + int(j.get("chars_out") or 0)
        elif status == "blocked":
            out["held"] += 1  # held jobs wait for the gate; they are not queued
            if (j.get("queued_at") or 0) > latest_block:
                latest_block = j.get("queued_at") or 0
                out["blocked"] = short(j.get("reason") or "blocked", 120)
    return out


def local_stats_file():
    """localq's stats file: localq/stats.json, or the old state/localq-stats.json while that one still exists."""
    new = os.path.join(STATE, "localq", "stats.json")
    old = os.path.join(STATE, "localq-stats.json")
    return old if not os.path.exists(new) and os.path.exists(old) else new


def local_today():
    """Jobs and characters done on local models today, across machines (localq/stats.json), and the output share
    estimate: pct = local output chars / (local output + paid_chars), paid_chars = today's delivered mail; None when
    not known."""
    day_name = time.strftime("%Y-%m-%d")
    stats = load(local_stats_file(), {}) or {}
    day = (stats.get("days") or {}).get(day_name, {})
    jobs = chars = 0
    for m in day.values():
        jobs += int(m.get("jobs", 0))
        chars += int(m.get("chars_in", 0)) + int(m.get("chars_out", 0))
    output = sum(int(m.get("chars_out", 0)) for m in day.values())
    sh = localq_share.share(output, localq_share.paid_today(day_name, MAIL_DIR))
    return {"jobs": jobs, "chars": chars, "paid_chars": sh["paid_chars"], "pct": sh["pct"]}


def refresh_local():
    """Copy each machine's local-work summary into the shared state: the laptop as "lap", Minis by id."""
    lap = local_state("lap")
    minis = {mid: local_state(mid) for mid in MINIS}
    today = local_today()
    with lock:
        state.setdefault("laptop", {})["local"] = lap
        for mid, summary in minis.items():
            state["minis"][mid]["local"] = summary
        state["local_today"] = today


def local_loop():
    while True:
        try:
            refresh_local()
            publish()
        except Exception:
            pass
        time.sleep(5)


# ---------- glasses (Home app's Studio page) ----------

def _short_title(t, n=24):
    return t if len(t) <= n else t[:n - 1] + "…"


def glasses_summary():
    """One line per computer and the latest conversation, for the G2's 56-character lines."""
    with lock:
        st, past = json.loads(json.dumps(state)), list(history)
    machines = []
    lap = st.get("laptop") or {}
    run = [(p["name"], t) for p in lap.get("projects", []) for t in p["tasks"] if t["status"] == "running"]
    working = [a for a in lap.get("studio") or [] if a.get("status") == "working"]
    if run:
        _, t = run[0]
        parts = [f'{t["member"]} · step {t["steps"]} · {_short_title(t["title"])}']
    else:
        parts = []
    if working:
        parts.append(f"studio: {len(working)} working")
    machines.append({"name": HUB_LABEL, "line": " · ".join(parts) or "standing by", "busy": bool(parts)})
    for mid in MINIS:
        m = (st.get("minis") or {}).get(mid) or {}
        if not m.get("online"):
            if MINIS[mid]["ssh"]:
                machines.append({"name": m.get("label", mid), "line": m.get("note") or "offline", "busy": False})
            continue
        parts, busy = [], False
        t = next((x for x in m.get("room") or [] if x["status"] == "running"), None)
        if t:
            parts.append(f'{t["member"]} · step {t["steps"]} · {_short_title(t["title"])}'); busy = True
        working = [a for a in m.get("agents") or [] if a["status"] == "working"]
        if working:
            parts.append(f"studio: {len(working)} working"); busy = True
        others = [o for o in m.get("others") or [] if o.get("busy")]
        if others:
            parts.append("also " + ", ".join(f'{o["name"]} ({o["where"]})' if o["where"] not in ("~", "/") else o["name"] for o in others[:2]))
        machines.append({"name": m.get("label", mid), "line": " · ".join(parts) or "standing by", "busy": busy})
    feed = [{"t": e["t"], "text": e["text"], "kind": e["kind"]} for e in reversed(past) if e.get("kind") not in ("step", "status")][:12]
    return {"at": time.time(), "machines": machines, "feed": feed, "local_today": local_today()}


# ---------- web ----------

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = queue.Queue()
            with lock:
                listeners.append(q)
                snap, past = json.loads(json.dumps(state)), list(history[-40:])
            try:
                self.wfile.write(f"event: state\ndata: {json.dumps(snap)}\n\n".encode())
                self.wfile.write(f"event: history\ndata: {json.dumps(past)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        kind, data = q.get(timeout=15)
                        self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except Exception:
                pass
            finally:
                with lock:
                    listeners.remove(q)
            return
        if self.path == "/api/studio":
            body = json.dumps(glasses_summary()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        path = "index.html" if self.path in ("/", "/index.html") else None
        if not path:
            self.send_error(404)
            return
        with open(os.path.join(HERE, path), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    threading.Thread(target=laptop_loop, daemon=True).start()
    threading.Thread(target=local_loop, daemon=True).start()
    for mid in MINIS:
        threading.Thread(target=mini_loop, args=(mid,), daemon=True).start()
    print(f"Studio viewer on http://127.0.0.1:{PORT}")
    Server(("127.0.0.1", PORT), Handler).serve_forever()
