#!/usr/bin/env python3
"""One JSON snapshot of every studio: agents, recent JEV routes, commits from the last hour, per studio team.

  studio-snap.py                 every machine in the config: the laptop here, Minis over ssh
  studio-snap.py --local         this machine only (what the Minis run; the script arrives on stdin)
  studio-snap.py --session NAME  herdr session (default: herdr_session from the config)
  --remote-path P / --studio-dirs JSON   (with --local) PATH prefix and studio folders; the config's remote_path / studio_dirs

Per machine: {"online", "project", "agents": [{name, status}], "routes": [last 5 route rows],
"commits": [{hash, subject, files}], "teams": [one snapshot per studio team]}. A team snapshot is
{"team", "kind", "idle", "agents", "routes" (its last 5 route rows), "commits" (on the branches its agents have checked out)}.
Read-only. Runs on Python 3.8+ with the standard library (the Minis' system Python). The Minis get
lib/studio_teams.py prepended to this script on stdin, so with --local it must not import re_studio_config.
"""
import glob, json, os, shlex, subprocess, sys

if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
    print("snapshot every studio's agents, routes, and recent commits as JSON, one snapshot per team")
    sys.exit(0)

try:
    import studio_teams
except ImportError:    # on a Mini the library arrives prepended to this script on stdin (see everyone())
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lib"))
    import studio_teams


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


def agent_rows(session):
    """The herdr agent rows as they are (name, agent kind, agent_status, cwd, ...)."""
    try:
        r = subprocess.run(["herdr", "--session", session, "agent", "list"], capture_output=True, text=True, timeout=8)
        rows = json.loads(r.stdout).get("result", {}).get("agents", [])
    except Exception:
        return []
    return [a for a in rows if a.get("name")]


def agents(session):
    return [{"name": a.get("name"), "status": a.get("agent_status", "?")} for a in agent_rows(session)]


def commits(repo, ref="--all"):
    """Commits of the last hour reachable from ref (default: every branch of the repo)."""
    if not repo or not ref:
        return []
    try:
        r = subprocess.run(["git", "-C", repo, "log", ref, "--since=1 hour ago", "--format=@@%h\t%ct\t%s", "--name-only"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    out = []
    for ln in r.stdout.splitlines():
        if ln.startswith("@@"):
            h, _, rest = ln[2:].partition("\t")
            when, _, s = rest.partition("\t")
            out.append({"hash": h, "subject": s, "files": 0, "t": int(when) if when.isdigit() else None, "paths": []})
        elif ln.strip() and out:
            out[-1]["files"] += 1
            if len(out[-1]["paths"]) < 30:
                out[-1]["paths"].append(ln.strip())
    return out


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def head_of(cwd):
    """(repo, ref) an agent works on: its checkout's top folder, and the branch it has checked out (the commit when
    detached). ('', '') when the agent is not in a repo. A worktree is its own repo here."""
    if not cwd:
        return "", ""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return "", ""
    ref = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if ref == "HEAD":
        ref = _git(cwd, "rev-parse", "HEAD")
    return top, ref


def team_snapshots(rows, routes):
    """One snapshot per studio team: its agents, its last 5 route rows, and its commits: the last hour on the branch
    each of its agents has checked out. A team sees only its own branches, so a commit by another team in the same
    repo does not change this team's snapshot. An agent with no checkout contributes no commits."""
    cache = {}

    def ref_commits(repo, ref):
        if (repo, ref) not in cache:
            cache[(repo, ref)] = commits(repo, ref)
        return cache[(repo, ref)]

    out = []
    for g in studio_teams.group(rows):
        heads = []
        for a in g["agents"]:
            head = head_of(a.get("cwd"))
            if head[0] and head not in heads:
                heads.append(head)
        seen, team_commits = set(), []
        for repo, ref in heads:
            for c in ref_commits(repo, ref):
                if c["hash"] not in seen:
                    seen.add(c["hash"])
                    team_commits.append(c)
        mine = [r for r in routes if studio_teams.team_of_route(r) == g["team"]]
        out.append({"team": g["team"], "kind": g["kind"], "idle": g["idle"],
                    "agents": [{"name": a.get("name"), "status": a.get("agent_status", "?")} for a in g["agents"]],
                    "routes": mine[-5:], "commits": team_commits})
    return out


def local(session, is_laptop, dirs=None):
    log = active_log(is_laptop, dirs)
    repo = os.path.dirname(os.path.dirname(log)) if log else ""
    rows = agent_rows(session)
    routes = _routes(log) if log else []
    return {"online": True, "project": os.path.basename(repo),
            "agents": [{"name": a.get("name"), "status": a.get("agent_status", "?")} for a in rows],
            "routes": routes[-5:], "commits": commits(repo) if repo else [],
            "teams": team_snapshots(rows, routes)}


def library_prelude(lib_src):
    """Code that puts studio_teams into sys.modules on a Mini, where the script arrives on stdin."""
    return ("import sys as _sys, types as _types\n"
            "_teams = _types.ModuleType('studio_teams')\n"
            "exec(compile(%r, 'studio_teams.py', 'exec'), _teams.__dict__)\n"
            "_sys.modules['studio_teams'] = _teams\n") % lib_src


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
    with open(os.path.join(os.path.dirname(here), "lib", "studio_teams.py")) as f:
        src = library_prelude(f.read()) + src
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
