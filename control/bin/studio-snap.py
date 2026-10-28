#!/usr/bin/env python3
"""One JSON snapshot of every studio: agents, recent JEV routes, commits from the last hour.

  studio-snap.py                 every machine in the config: the laptop here, Minis over ssh
  studio-snap.py --local         this machine only (what the Minis run; the script arrives on stdin)
  studio-snap.py --session NAME  herdr session (default: herdr_session from the config)
  --remote-path P / --studio-dirs JSON   (with --local) PATH prefix and studio folders; the config's remote_path / studio_dirs

Per machine: {"online", "project", "agents": [{name, status}], "routes": [last 5 route rows],
"commits": [{hash, subject, files}]}. Read-only. Runs on Python 3.8+ with the standard library
(the Minis' system Python), so it must not import re_studio_config when run with --local.
"""
import glob, json, os, shlex, subprocess, sys

if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
    print("snapshot every studio's agents, routes, and recent commits as JSON")
    sys.exit(0)


DEFAULT_REMOTE_PATH = "$HOME/.local/bin:/opt/homebrew/bin"
DEFAULT_STUDIO_DIRS = ["~/DevStudio"]


def set_path(remote_path):
    """Put remote_path (default PATH prefix when empty) in front of PATH, expanding variables like $HOME."""
    os.environ["PATH"] = os.path.expandvars(remote_path or DEFAULT_REMOTE_PATH) + ":" + os.environ.get("PATH", "")


def _conf(key):
    try:
        with open(os.path.expanduser("~/.dev-studio/studio.conf")) as f:
            for ln in f:
                if ln.startswith(key + "="):
                    return ln.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _routes(path):
    try:
        with open(path) as f:
            lines = f.read().splitlines()[-400:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("kind") == "route":
            out.append({k: r.get(k) for k in ("from", "to", "addressed", "decision", "text", "t")})
    return out


def active_log(is_laptop, dirs=None):
    """The router.log of the project the studio is working in."""
    if is_laptop:
        p = os.path.join(os.path.expanduser(_conf("LAST_PROJECT")), ".studio", "router.log")
        return p if os.path.exists(p) else ""
    cands = glob.glob(os.path.expanduser("~/Projects/*/.studio/router.log"))
    for d in dirs or DEFAULT_STUDIO_DIRS:
        cands += glob.glob(os.path.join(os.path.expanduser(d), ".studio", "router.log"))
    best, bestt = "", -1
    for c in cands:
        rs = _routes(c)
        t = max([r.get("t") or 0 for r in rs] or [0])
        if t > bestt:
            best, bestt = c, t
    return best or (max(cands, key=os.path.getmtime) if cands else "")


def agents(session):
    try:
        r = subprocess.run(["herdr", "--session", session, "agent", "list"], capture_output=True, text=True, timeout=8)
        rows = json.loads(r.stdout).get("result", {}).get("agents", [])
    except Exception:
        return []
    return [{"name": a.get("name"), "status": a.get("agent_status", "?")} for a in rows if a.get("name")]


def commits(repo):
    try:
        r = subprocess.run(["git", "-C", repo, "log", "--all", "--since=1 hour ago", "--format=@@%h\t%s", "--name-only"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    out = []
    for ln in r.stdout.splitlines():
        if ln.startswith("@@"):
            h, _, s = ln[2:].partition("\t")
            out.append({"hash": h, "subject": s, "files": 0})
        elif ln.strip() and out:
            out[-1]["files"] += 1
    return out


def local(session, is_laptop, dirs=None):
    log = active_log(is_laptop, dirs)
    repo = os.path.dirname(os.path.dirname(log)) if log else ""
    return {"online": True, "project": os.path.basename(repo), "agents": agents(session),
            "routes": _routes(log)[-5:] if log else [], "commits": commits(repo) if repo else []}


def everyone(session=None):
    here = os.path.dirname(os.path.realpath(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(here), "lib"))
    import re_studio_config as cfg
    session = session or cfg.get("herdr_session", "dev-studio")
    remote_path = str(cfg.get("remote_path") or DEFAULT_REMOTE_PATH)
    shared_dirs = [str(d) for d in (cfg.get("studio_dirs") or DEFAULT_STUDIO_DIRS)]
    set_path(remote_path)
    with open(os.path.realpath(__file__)) as f:
        src = f.read()
    out = {}
    for m in cfg.machines():
        mid = m["id"]
        if m.get("role") == "hub":
            out[mid] = local(session, True)
            continue
        out[mid] = {"online": False, "note": "no ssh route"}
        own = m.get("project")
        dirs = ([str(own)] if own else []) + [d for d in shared_dirs if d != own]
        remote = ("python3 - --local --session " + shlex.quote(session) + " --remote-path " + shlex.quote(remote_path)
                  + " --studio-dirs " + shlex.quote(json.dumps(dirs)))
        for cmd, hops in cfg.routes(mid, remote, timeout=6):
            try:
                r = subprocess.run(cmd, input=src, capture_output=True, text=True, timeout=30)
                out[mid] = dict(json.loads(r.stdout), route=cfg.route_label(hops))
                break
            except Exception:
                out[mid] = {"online": False, "note": "unreachable"}
    return out


def main(argv):
    def opt(name):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None
    session = opt("--session")
    if "--local" in argv:
        set_path(opt("--remote-path"))
        try:
            dirs = json.loads(opt("--studio-dirs") or "null")
        except ValueError:
            dirs = None
        print(json.dumps(local(session or "dev-studio", False, dirs)))
    else:
        print(json.dumps({"machines": everyone(session)}))


if __name__ == "__main__":
    main(sys.argv[1:])
