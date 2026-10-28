"""Gate for the local-model work queue: approval windows and the checks that run before any local job.

allow(mid, hours, who), deny(mid) and approval(mid) manage the window a Mini is open for local work.
check(mid) -> (ok, reason) runs the checks in contract order and stops at the first failure:
  1. Minis only: a valid approval window
  2. Minis only: configured blocked desktop apps are not running
  3. the local model answers (or the configured CLI binary exists)
  4. a loaded laptop model has at least 2 GiB available and low swap-out; otherwise free
     memory is at least local_min_free_pct
Mini checks are read-only commands over cfg.routes. The gate never opens or quits an app.
LOCALQ_FAKE_GATE=<file.json> ({"m1": [false, "reason"], "lap": [true, ""]}) replaces check() for tests.
Standard library only.
"""
import json, os, re, shlex, subprocess, sys, time, urllib.error, urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import re_studio_config as cfg  # noqa: E402

DEFAULT_MIN_FREE_PCT = 15
LOADED_MIN_AVAILABLE_KIB = 2 * 1024 * 1024
MAX_SWAP_OUT_PAGES_PER_SEC = 1000
DEFAULT_OPENAI = {"kind": "openai", "url": "http://127.0.0.1:18081/v1", "model": ""}
DEFAULT_BLOCK_APPS = ["ChatGPT", "Claude"]
MEMORY_PROBE = "vm_stat; sysctl hw.memsize"


class Unreachable(Exception):
    pass


def approvals_path():
    return os.path.join(cfg.state_dir(), "localq", "approvals.json")


def _load_approvals():
    try:
        with open(approvals_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_approvals(data):
    path = approvals_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def allow(mid, hours, who):
    """Open a Mini for local work for the given number of hours. Returns the stored window."""
    cfg.machine(mid)  # unknown machines raise KeyError
    if float(hours) <= 0:
        raise ValueError("hours must be positive")
    now = time.time()
    data = _load_approvals()
    data[mid] = {"who": who, "at": now, "until": now + float(hours) * 3600}
    _save_approvals(data)
    return data[mid]


def deny(mid):
    data = _load_approvals()
    data.pop(mid, None)
    _save_approvals(data)


def approval(mid):
    """The live approval window for a machine, or None when it is missing or expired."""
    entry = _load_approvals().get(mid)
    if entry and float(entry.get("until", 0)) > time.time():
        return entry
    return None


def _approval_check(mid):
    entry = _load_approvals().get(mid)
    if entry is None:
        return False, f"{mid} has no approval window; run: localq allow {mid} <hours>"
    if float(entry.get("until", 0)) <= time.time():
        return False, f"{mid}'s approval window has expired; run: localq allow {mid} <hours>"
    return True, ""


def _remote(mid, remote):
    """Run one read-only command on a machine, first route that connects. Returns (exit code, stdout)."""
    routes = cfg.routes(mid, remote, timeout=6)
    if not routes:
        raise Unreachable(f"no ssh route to {mid}")
    for argv, _hops in routes:
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode != 255:  # ssh exits 255 when it could not connect
            return r.returncode, r.stdout
    raise Unreachable(f"{mid} is unreachable")


def _apps_check(mid):
    apps = cfg.get("local_block_apps", DEFAULT_BLOCK_APPS)
    if not isinstance(apps, list) or not all(isinstance(a, str) and a.strip() and not a.startswith("-") for a in apps):
        return False, "invalid local_block_apps configuration"
    if not apps:
        return True, ""
    probe = "for a in " + " ".join(shlex.quote(a) for a in apps) + '; do pgrep -x "$a" >/dev/null && printf "%s\\n" "$a"; done; true'
    _rc, out = _remote(mid, probe)
    running = [line.strip() for line in out.splitlines() if line.strip()]
    if running:
        return False, (f"{', '.join(running)} running on {mid}; quit it there first "
                       "(the gate does not quit apps)")
    return True, ""


def _local_memory(meminfo="/proc/meminfo"):
    """Read total and available Linux memory in KiB."""
    try:
        vals = {}
        with open(meminfo, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if parts and parts[0] in ("MemTotal:", "MemAvailable:"):
                    vals[parts[0]] = int(parts[1])
        if vals["MemTotal:"] <= 0 or vals["MemAvailable:"] < 0:
            return None
        return vals
    except (OSError, IndexError, KeyError, ValueError):
        return None


def _local_free_pct(meminfo="/proc/meminfo"):
    vals = _local_memory(meminfo)
    return 100 * vals["MemAvailable:"] // vals["MemTotal:"] if vals else None


def _swap_out_rate(vmstat="/proc/vmstat"):
    """Sample Linux swap-out pages over one second; unreadable/reset counters fail closed."""
    def read_count():
        with open(vmstat, encoding="utf-8") as f:
            match = re.search(r"^pswpout\s+(\d+)\s*$", f.read(), re.MULTILINE)
        return int(match.group(1)) if match else None

    try:
        before = read_count()
        if before is None:
            return None
        started = time.monotonic()
        time.sleep(1)
        after = read_count()
        elapsed = time.monotonic() - started
        if after is None or after < before or elapsed <= 0:
            return None
        return (after - before) / elapsed
    except (OSError, ValueError):
        return None


def _free_pct(mid, mini):
    if not mini:
        return _local_free_pct()
    _rc, out = _remote(mid, MEMORY_PROBE)
    page_size = re.search(r"page size of (\d+) bytes", out)
    total = re.search(r"^hw\.memsize:\s*(\d+)\s*$", out, re.MULTILINE)
    pages = [re.search(rf"^Pages {name}:\s*(\d+)\.?\s*$", out, re.MULTILINE)
             for name in ("free", "inactive", "purgeable")]
    if not page_size or not total or not all(pages):
        return None
    page_bytes, total_bytes = int(page_size.group(1)), int(total.group(1))
    if page_bytes <= 0 or total_bytes <= 0:
        return None
    return 100 * sum(int(p.group(1)) for p in pages) * page_bytes // total_bytes


def _memory_check(mid, mini, threshold, model_loaded=False):
    if model_loaded and not mini:
        vals = _local_memory()
        if vals is None:
            return False, f"cannot read available memory on {mid}"
        available = vals["MemAvailable:"]
        if available < LOADED_MIN_AVAILABLE_KIB:
            return False, (f"available memory on {mid} is {available / (1024 * 1024):.2f} GiB, "
                           "below the 2 GiB minimum for a loaded model")
        rate = _swap_out_rate()
        if rate is None:
            return False, f"cannot read swap-out rate on {mid}"
        if rate > MAX_SWAP_OUT_PAGES_PER_SEC:
            return False, (f"swap-out on {mid} is {rate:g} pages/s, "
                           f"above the {MAX_SWAP_OUT_PAGES_PER_SEC} pages/s maximum")
        return True, ""
    pct = _free_pct(mid, mini)
    if pct is None:
        return False, f"cannot read free memory on {mid}"
    if pct < threshold:
        return False, f"free memory on {mid} is {pct}%, below the {threshold}% minimum"
    return True, ""


def local_model(mid):
    """The machine's local_model entry, falling back to the default for its role."""
    m = cfg.machine(mid)
    if m.get("local_model"):
        return dict(m["local_model"])
    return dict(DEFAULT_OPENAI) if m.get("role") == "hub" else {}


def _model_check(mid, mini):
    lm = local_model(mid)
    kind = lm.get("kind")
    if kind == "openai":
        url = str(lm.get("url", "")).rstrip("/") + "/models"
        if mini:
            _rc, out = _remote(mid, f"curl -fs -m 5 {shlex.quote(url)} >/dev/null; echo $?")
            ok = out.strip() == "0"
        else:
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    ok = resp.status == 200
            except (urllib.error.URLError, OSError, ValueError):
                ok = False
        return (True, "") if ok else (False, f"the local model server does not answer on {mid}")
    if kind == "cli":
        path, args = lm.get("path"), lm.get("args", [])
        if not isinstance(path, str) or not path.strip() or not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            return False, f"invalid cli path or args on {mid}"
        if mini:
            quoted = "~/" + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)
            _rc, out = _remote(mid, f"test -x {quoted}; echo $?")
            ok = out.strip() == "0"
        else:
            ok = os.access(os.path.expanduser(path), os.X_OK)
        return (True, "") if ok else (False, f"the cli binary is missing on {mid}")
    if not lm:
        return False, f"no local runner configured on {mid}"
    return False, f"unknown local_model kind on {mid}"


def _fake(mid, path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entry = data.get(mid)
    if entry is None:
        return False, f"no fake gate entry for {mid}"
    ok, why = entry
    return bool(ok), str(why)


def check(mid):
    """Run the pre-run checks for a machine. Returns (ok, reason); the first failing check's reason is returned.

    The approval window is always checked for real on a Mini, even when LOCALQ_FAKE_GATE is set: the fake file
    only stands in for the app, memory and model checks.
    """
    try:
        m = cfg.machine(mid)
    except KeyError:
        return False, f"unknown machine {mid}"
    mini = m.get("role") != "hub"
    if mini:
        ok, why = _approval_check(mid)
        if not ok:
            return False, why
    fake = os.environ.get("LOCALQ_FAKE_GATE")
    if fake:
        return _fake(mid, fake)
    threshold = int(m.get("local_min_free_pct", cfg.get("local_min_free_pct", DEFAULT_MIN_FREE_PCT)))
    try:
        if mini:
            ok, why = _apps_check(mid)
            if not ok:
                return False, why
        ok, why = _model_check(mid, mini)
        if not ok:
            return False, why
        loaded = not mini and local_model(mid).get("kind") == "openai"
        return _memory_check(mid, mini, threshold, model_loaded=loaded)
    except Unreachable as e:
        return False, str(e)
