"""Model runners for localq: run one prompt on a machine's local model and return the reply.

run(mid, prompt, max_chars, timeout=300) -> {"text", "seconds", "model"}; raises RunnerError(reason).

Two kinds, from the machine's optional "local_model" entry or the defaults:
  openai     POST <url>/chat/completions on a local OpenAI-compatible server (the laptop's default, `hub`).
  cli        a configured binary on the Mini, reached over SSH through cfg.routes(). The prompt goes on stdin.
             Never on the command line, and never by opening the app.
The recorded model is a short name (the file name without any path), so no home path lands in results.
Standard library only. Import with lib/ on sys.path (re_studio_config must be importable).
"""
import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request

import re_studio_config as cfg

DEFAULT_OPENAI_URL = "http://127.0.0.1:18081/v1"
ROUTE_TIMEOUT = 8  # seconds for SSH to connect, as cfg.routes uses it


class RunnerError(Exception):
    """A run could not produce a reply. The message is the reason, short and safe to store."""


def short_name(name):
    """'models/Fake-Model.gguf' -> 'Fake-Model.gguf': the name only, no directories."""
    return os.path.basename(str(name).rstrip("/")) or ""


def resolve(mid):
    """Resolve a configured runner, using the hub's server default when absent."""
    try:
        m = cfg.machine(mid)
    except KeyError:
        raise RunnerError(f"no machine {mid} in the config")
    spec = dict(m.get("local_model") or {})
    kind = spec.get("kind") or ("openai" if m.get("role") == "hub" and not spec else None)
    if kind == "openai":
        return {"kind": kind, "url": (spec.get("url") or DEFAULT_OPENAI_URL).rstrip("/"),
                "model": spec.get("model") or ""}
    if kind == "cli":
        path, args = spec.get("path"), spec.get("args", [])
        if not isinstance(path, str) or not path.strip() or not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise RunnerError(f"invalid cli path or args on {mid}")
        return {"kind": kind, "path": path, "args": args}
    if kind is None:
        raise RunnerError(f"no local runner configured on {mid}")
    raise RunnerError(f"unknown local_model kind: {kind}")


def _http_json(method, url, body=None, timeout=300):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:  # before URLError: HTTPError is a subclass of it
        raise RunnerError(f"model server answered HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:  # refused, DNS, timeouts (socket.timeout is an OSError)
        raise RunnerError(f"model server unreachable: {e.__class__.__name__}")
    except ValueError:
        raise RunnerError("model server reply is not JSON")


def _openai_run(spec, prompt, max_chars, timeout):
    model = spec["model"]
    if not model:  # "" means whatever the server serves: ask it, and take the first model it lists
        listing = _http_json("GET", spec["url"] + "/models", timeout=timeout)
        ids = [d.get("id") for d in (listing.get("data") or []) if isinstance(d, dict) and d.get("id")]
        if not ids:
            raise RunnerError("model server lists no models")
        model = ids[0]
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max(16, max_chars // 3),  # about 3 characters per token; the reply is clipped anyway
            "temperature": 0.2}
    reply = _http_json("POST", spec["url"] + "/chat/completions", body, timeout=timeout)
    try:
        text = reply["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RunnerError("model server reply has no text")
    return {"text": (text or "")[:max_chars], "model": short_name(model)}


def _remote_command(path, args):
    """The command the Mini runs. A leading ~/ stays unquoted so the Mini's shell expands it."""
    if path.startswith("~/"):
        command = "~/" + shlex.quote(path[2:])
    else:
        command = shlex.quote(path)
    return " ".join([command] + [shlex.quote(arg) for arg in args])


# ssh's own exit status when it could not connect: the only failure where nothing ran on the Mini.
SSH_CONNECT_FAILED = 255


def _cli_run(mid, spec, prompt, max_chars, timeout):
    """Run on the Mini. Only an SSH connect failure (exit 255) moves to the next route. Any other exit or a
    timeout may mean the job already started, and the next route reaches the same Mini, so those stop here."""
    routes = cfg.routes(mid, _remote_command(spec["path"], spec["args"]), timeout=ROUTE_TIMEOUT)
    if not routes:
        raise RunnerError(f"no ssh route to {mid}")
    last = "no route answered"
    for argv, _hops in routes:
        try:
            r = subprocess.run(argv, input=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RunnerError("cli timed out; not retried on another route (the job may have started)")
        except OSError as e:
            raise RunnerError(f"could not start ssh: {e.__class__.__name__}")
        if r.returncode == 0:
            return {"text": (r.stdout or "").strip()[:max_chars], "model": short_name(spec["path"])}
        if r.returncode != SSH_CONNECT_FAILED:
            raise RunnerError(f"cli exit {r.returncode}: {(r.stderr or '').strip()[-200:]}; "
                              "not retried on another route (the job may have started)")
        last = f"ssh could not connect (exit {SSH_CONNECT_FAILED})"
    raise RunnerError(f"no route could connect ({last})")


def run(mid, prompt, max_chars, timeout=300):
    """Run one prompt on the machine's local model. Returns {'text', 'seconds', 'model'}; raises RunnerError."""
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars < 1:
        raise RunnerError("max_chars must be a positive integer")
    t0 = time.monotonic()
    spec = resolve(mid)
    if spec["kind"] == "openai":
        out = _openai_run(spec, prompt, max_chars, timeout)
    else:
        out = _cli_run(mid, spec, prompt, max_chars, timeout)
    out["seconds"] = round(time.monotonic() - t0, 3)
    return out
