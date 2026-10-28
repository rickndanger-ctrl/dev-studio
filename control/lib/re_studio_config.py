#!/usr/bin/env python3
"""Machine config for re-studio: which machines exist and how to reach them over SSH.

Reads ~/.config/re-studio/machines.json (or $RE_STUDIO_CONFIG), falling back to the repo's
config/machines.example.json. Standard library only.

Python:  sys.path.insert(0, <repo>/lib); import re_studio_config as cfg
         cfg.machine("m2"), cfg.routes("m2", "bash -s"), cfg.route_label(hops), cfg.get("viewer_port")
Shell:   re_studio_config.py get viewer_port
         re_studio_config.py field m1 architect
         re_studio_config.py ids [role]
         re_studio_config.py run m2 "<remote command>"   next route only on ssh exit 255; else return that exit
         re_studio_config.py path
         re_studio_config.py remote-path                 PATH prefix for remote machines
         re_studio_config.py studio-dirs m2              shell-quoted folders to look for a studio on m2
"""
import json, os, shlex, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
EXAMPLE = os.path.join(REPO, "config", "machines.example.json")
USER = os.path.expanduser("~/.config/re-studio/machines.json")
DEFAULT_REMOTE_PATH = "$HOME/.local/bin:/opt/homebrew/bin"
DEFAULT_STUDIO_DIRS = ["~/DevStudio"]


def config_path():
    for p in (os.environ.get("RE_STUDIO_CONFIG"), USER):
        if p and os.path.exists(p):
            return p
    return EXAMPLE


def load():
    with open(config_path()) as f:
        return json.load(f)


def get(key, default=None):
    return load().get(key, default)


def state_dir():
    """Where the viewer, auditor and helpers keep their files (default: <repo>/state)."""
    return os.path.expanduser(get("state_dir") or os.path.join(REPO, "state"))


def audit():
    """The audit block {app, data_dir, mac_data_dir, project}, or None when the owner has not set one.
    Without a data folder and a project there is nothing to audit."""
    a = get("audit")
    return a if isinstance(a, dict) and a.get("data_dir") and a.get("project") else None


def remote_path():
    """PATH prefix for remote machines. The value keeps $HOME, which the remote shell expands."""
    return get("remote_path") or DEFAULT_REMOTE_PATH


def studio_dirs(mid=None):
    """Folders searched for a studio on a remote machine. The machine's own project folder comes first when mid is given."""
    dirs = list(get("studio_dirs") or DEFAULT_STUDIO_DIRS)
    if mid is not None:
        own = machine(mid).get("project")
        dirs = ([own] if own else []) + dirs
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def machines(role=None):
    return [m for m in load().get("machines", []) if role is None or m.get("role") == role]


def machine(mid):
    for m in machines():
        if m.get("id") == mid:
            return m
    raise KeyError(mid)


def hop_lists(mid):
    """Every way to reach a machine, primary first. [] = nothing (local, or not set up)."""
    m = machine(mid)
    if m.get("enabled") is False:
        return []
    return [h for h in [m.get("ssh") or []] + list(m.get("ssh_alt") or []) if h]


def ssh_command(hops, remote, timeout=6):
    """argv that runs `remote` on the last hop, jumping through the earlier ones."""
    opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}"]
    cmd = remote
    for h in reversed(hops[1:]):
        cmd = " ".join(["ssh"] + opts + [h, shlex.quote(cmd)])
    return ["ssh"] + opts + [hops[0], cmd]


def routes(mid, remote, timeout=6):
    """[(argv, hops), ...] for each route to a machine, primary first."""
    return [(ssh_command(h, remote, timeout), h) for h in hop_lists(mid)]


def route_label(hops):
    """'direct' for one hop, else 'via <label of the first hop's machine>'."""
    if len(hops) <= 1:
        return "direct"
    for m in machines():
        if (m.get("ssh") or [])[:1] == hops[:1] and len(m.get("ssh") or []) == 1:
            return "via " + m.get("label", hops[0])
    return "via " + hops[0]


# ssh's own exit status when it could not connect: the only failure where nothing ran on the host.
SSH_CONNECT_FAILED = 255


def _main(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    op, args = argv[0], argv[1:]
    try:
        if op == "path":
            print(config_path())
        elif op == "remote-path":
            print(remote_path())
        elif op == "studio-dirs":
            print(" ".join(shlex.quote(d) for d in studio_dirs(args[0])))
        elif op == "get":
            v = get(args[0])
            if v is None:
                return 1
            print(v if not isinstance(v, (dict, list)) else json.dumps(v))
        elif op == "field":
            v = machine(args[0]).get(args[1])
            if v is None:
                return 1
            print(v if not isinstance(v, (dict, list)) else json.dumps(v))
        elif op == "ids":
            print("\n".join(m["id"] for m in machines(args[0] if args else None)))
        elif op == "run":
            rs = routes(args[0], args[1], timeout=int(args[2]) if len(args) > 2 else 8)
            if not rs:
                print(f"{args[0]}: no ssh route configured", file=sys.stderr)
                return 2
            # Only ssh's own connect failure (exit 255) moves to the next route. Any other exit, including a
            # failing script, is returned at once: the job may have started, and another route reaches the same host.
            for i, (cmd, _) in enumerate(rs):
                last = i == len(rs) - 1
                r = subprocess.run(cmd, stderr=None if last else subprocess.PIPE)
                if r.returncode != SSH_CONNECT_FAILED or last:
                    if not last and r.stderr:
                        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
                    return r.returncode
        else:
            print(f"unknown command: {op}", file=sys.stderr)
            return 2
    except KeyError as e:
        print(f"no machine {e} in {config_path()}", file=sys.stderr)
        return 2
    except IndexError:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
