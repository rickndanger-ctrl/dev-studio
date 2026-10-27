#!/usr/bin/env python3
"""The Dev Studio board: the cross-session picture, drawn from live data.

Reads `herdr agent list` (who is working), <project>/.studio/router.log (what JEV
decided), the mailbox folders (what is queued), and memory. Nothing here is
invented: every number on the board comes from one of those.

    board.py --studio DIR --herdr "herdr --session dev-studio" [--once] [--kind claude]
"""
import argparse
import json
import re
import os
import shutil
import subprocess
import unicodedata
import warnings
import sys
import time

ESC = "\033["
RESET, BOLD, DIM = ESC + "0m", ESC + "1m", ESC + "2m"
PINK, CYAN, GOLD, GREEN, RED, PURPLE, BLUE, GREY = (ESC + "38;5;%dm" % c for c in (205, 51, 220, 84, 203, 141, 75, 244))


CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean(s):
    """Agents and the log wrote this text: drop control characters so nothing it
    contains can repaint the terminal."""
    return CONTROL.sub("", str(s))


def c(color, s):
    return color + s + RESET


def char_width(ch):
    """Terminal columns one character takes. Wide and fullwidth are 2; everything
    else 1. Keep the board to plain and box-drawing characters (no emoji, no
    exotic brackets): one terminal drew U+2997 two columns wide and every
    rewrite of that line scrolled the pane."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def plain_len(s):
    """Visible width: strip ANSI codes, count wide characters as two."""
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\033":
            j = s.find("m", i)
            i = j + 1 if j > 0 else len(s)
            continue
        out += char_width(s[i])
        i += 1
    return out


def pad(s, width):
    n = plain_len(s)
    if n > width:
        # cut visible characters, keeping escapes
        res, seen, i = "", 0, 0
        while i < len(s) and seen + char_width(s[i]) <= width - 1:
            if s[i] == "\033":
                j = s.find("m", i)
                res += s[i:j + 1]
                i = j + 1
                continue
            res += s[i]
            seen += char_width(s[i])
            i += 1
        return res + RESET + "…"
    return s + " " * (width - n)


def bar(p, width=12, color=GREEN):
    p = max(0.0, min(1.0, p))
    full = int(round(p * width))
    return c(color, "█" * full) + c(GREY, "░" * (width - full))


def box(title, lines, width, color=GREY, height=None):
    """Return a list of rows: a titled box of exactly `width` columns."""
    inner = width - 2
    top = c(color, "┌ ") + c(BOLD + color, title) + c(color, " " + "─" * max(0, inner - plain_len(title) - 2) + "┐")
    body = [c(color, "│") + pad(" " + ln, inner) + c(color, "│") for ln in lines]
    if height:
        body = body[: height - 2] + [c(color, "│") + " " * inner + c(color, "│")] * max(0, height - 2 - len(body))
    bottom = c(color, "└" + "─" * inner + "┘")
    return [top] + body + [bottom]


def columns(blocks, gap=2):
    """Lay several row-lists side by side."""
    h = max(len(b) for b in blocks)
    widths = [max(plain_len(r) for r in b) if b else 0 for b in blocks]
    rows = []
    for i in range(h):
        rows.append((" " * gap).join(pad(b[i], w) if i < len(b) else " " * w for b, w in zip(blocks, widths)))
    return rows


# --- data ---------------------------------------------------------------------

def agents(herdr):
    try:
        out = subprocess.run(herdr.split() + ["agent", "list"], capture_output=True, text=True, timeout=10)
        return {clean(a["name"]): clean(a.get("agent_status", "unknown")) for a in json.loads(out.stdout)["result"]["agents"] if a.get("name")}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return {}


TAIL_CACHE = {}   # name -> (frame, text)
NOISE = ("❯", "⏵⏵", "shift+tab", "esc to interrupt", "? for shortcuts", "Try \"", "auto mode", "PR #", "▝", "▘", "Claude Code v", "/memory to free")


def agent_now(herdr, name, frame):
    """The last meaningful line on an agent's terminal: what it is doing right now.
    Read every other frame so four agents cost two Herdr calls a second."""
    hit = TAIL_CACHE.get(name)
    if hit and frame - hit[0] < 2:
        return hit[1]
    text = ""
    try:
        out = subprocess.run(herdr.split() + ["agent", "read", name, "--source", "recent-unwrapped", "--lines", "20"],
                             capture_output=True, text=True, timeout=5).stdout
        for ln in reversed(out.splitlines()):
            ln = clean(ln).strip()
            if not ln or ln.count("─") + ln.count("═") > 4 or set(ln) <= set("─═│┌┐└┘┤├ ·") or any(k in ln for k in NOISE):
                continue
            text = ln
            break
    except (OSError, subprocess.TimeoutExpired):
        pass
    TAIL_CACHE[name] = (frame, text)
    return text


LAST_LIVE = {}
EVENTS = []   # state changes seen by this board: {"t", "kind": "state", "who", "was", "now"}


def note_state_changes(live):
    for name, st in live.items():
        was = LAST_LIVE.get(name)
        if was is not None and was != st:
            EVENTS.append({"t": time.time(), "kind": "state", "who": name, "was": was, "now": st})
    LAST_LIVE.clear(); LAST_LIVE.update(live)
    del EVENTS[:-60]


def clean_deep(v):
    if isinstance(v, str):
        return clean(v)
    if isinstance(v, dict):
        return {clean(k): clean_deep(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clean_deep(x) for x in v]
    return v


def tail_log(path, n=400):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
        rows = []
        for ln in lines:
            try:
                rows.append(clean_deep(json.loads(ln)))
            except ValueError:
                pass
        return rows
    except OSError:
        return []


def count(d):
    try:
        return len([f for f in os.listdir(d) if f.endswith(".json")])
    except OSError:
        return 0


def mem_free_mb():
    try:
        # Absolute paths: under launchd PATH may not include /usr/sbin.
        vm = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=5).stdout
        ps = int(subprocess.run(["/usr/sbin/sysctl", "-n", "hw.pagesize"], capture_output=True, text=True).stdout)
        pages = 0
        for key in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
            for ln in vm.splitlines():
                if ln.startswith(key):
                    pages += int(ln.split(":")[1].strip().rstrip("."))
        return pages * ps // 1048576
    except (OSError, ValueError):
        return 0


# --- rendering ------------------------------------------------------------------

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
STATUS_STYLE = {
    "working": (GOLD, "on it"), "blocked": (RED, "NEEDS YOU"), "done": (GREEN, "finished"),
    "idle": (GREEN, "ready"), "unknown": (GREY, "starting"), "offline": (GREY, "offline"),
}


def status_cell(st, frame):
    color, word = STATUS_STYLE.get(st, (GREY, st))
    if st == "working":
        return c(color, SPIN[frame % 10] + " " + word)
    dot = "●" if st in ("idle", "done", "working") else "○"
    return c(color, dot + " " + word)


def short(s, n):
    s = clean(s).replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def render(a, frame, cols, rows_avail):
    """The chain: architect -> main session -> router -> workers, with the real
    channels drawn between them, and the live feed beside it."""
    peers = {}
    try:
        with open(os.path.join(a.studio, "peers.json")) as f:
            peers = clean_deep(json.load(f))
    except (OSError, ValueError):
        pass
    live = agents(a.herdr)
    note_state_changes(live)
    now_of = lambda n: agent_now(a.herdr, n, frame) if live.get(n) not in (None, "offline") else ""
    log = tail_log(os.path.join(a.studio, "router.log"))
    routes = [r for r in log if r.get("kind") == "route"]
    last = routes[-1] if routes else None
    last_model = next((r for r in reversed(routes) if "p_peer" in r), None)
    deliveries = [r for r in log if r.get("kind") in ("delivered", "held", "failed")]
    project = os.path.basename(os.path.dirname(a.studio))
    kind = a.kind
    arch = next((n for n, p in peers.items() if p.get("role", "").startswith("architect")), None)
    main = next((n for n, p in peers.items() if p.get("role", "").startswith("main")), None)
    workers = [n for n, p in peers.items() if p.get("role", "").startswith("worker")]
    names = list(peers)
    prefix = ""
    if len(names) > 1:
        first = names[0]
        n = 0
        while n < len(first) and all(len(x) > n and x[n] == first[n] for x in names):
            n += 1
        prefix = first[:n]
    prefix = prefix[: prefix.rfind("-") + 1] if "-" in prefix else ""
    name_of = lambda n: "@" + clean(n[len(prefix):] if prefix and n.startswith(prefix) else n)
    active = sum(1 for n in peers if live.get(n) in ("working", "blocked"))
    router_name = (last or {}).get("router", os.environ.get("ROUTER", "jev")).upper()
    now_t = time.time()
    fresh = lambda r: r and now_t - r.get("t", 0) < 8    # a message in flight on this channel right now

    def sent_got(n):
        sent = next((r for r in reversed(routes) if r.get("from") == n), None)
        got = next((r for r in reversed(routes) if n in r.get("to", [])), None)
        return sent, got

    def agent_lines(n, width):
        sent, got = sent_got(n)
        rows = [c(GREY, "now   ") + c(GOLD, short(now_of(n) or ("quiet" if live.get(n) not in (None, "offline") else "not running"), width - 8))]
        if sent:
            rows.append(c(GREY, "sent  ") + c(BOLD, "→ " + (", ".join(name_of(t) for t in sent.get("to", [])) or "nobody")) + c(GREY, " " + ago(sent["t"]) + " ") + short(sent.get("text", ""), max(10, width - 24)))
        if got:
            rows.append(c(GREY, "got   ") + c(BOLD, "← " + name_of(got["from"])) + c(GREY, " " + ago(got["t"]) + " ") + short(got.get("text", ""), max(10, width - 24)))
        if not sent and not got:
            rows.append(c(GREY, "no messages yet"))
        return rows

    def channel_line(width, n, towards_router):
        """One connector between a card and the router, carrying the newest message on it."""
        sent, got = sent_got(n)
        newest = max([r for r in (sent, got) if r], key=lambda r: r.get("t", 0), default=None)
        mid = width // 2
        mark = c(GREEN + BOLD, "●") if fresh(newest) else c(GREY, "│")
        if newest:
            arrow = "↑" if newest.get("from") == n else "↓"
            if not towards_router:
                arrow = "↓" if newest.get("from") == n else "↑"
            label = c(GREY, " " + arrow + " ") + c(BOLD, name_of(newest["from"]) + " → " + (", ".join(name_of(t) for t in newest.get("to", [])) or "nobody")) + c(GREY, " · " + short(newest.get("text", ""), max(8, width - mid - 30)) + " · " + ago(newest["t"]))
        else:
            label = ""
        return " " * mid + mark + label

    W = 64 if cols >= 150 else max(40, min(64, cols - 4))
    chain = []
    if arch:
        chain += box("%s · session architect" % (a.arch_model.upper()) + "  " + status_cell(live.get(arch, "offline"), frame), agent_lines(arch, W - 4), W, PURPLE)
        chain.append(channel_line(W, arch, True))
    if main:
        chain += box("%s · main session" % a.main_model.capitalize() + "  " + status_cell(live.get(main, "offline"), frame), agent_lines(main, W - 4), W, PINK)
        chain.append(channel_line(W, main, True))
    if last_model:
        lm = last_model
        leak = 1 - lm.get("leak", 0)
        rl = [pad(c(GREY, "which peer"), 14) + bar(lm["p_peer"]) + "  " + c(BOLD, "%.2f" % lm["p_peer"]) + c(GREY, " sharp" if lm.get("c_peer", 0) >= 0.5 else " soft") + c(GREY, "  → " + name_of(lm.get("peer", ""))),
              pad(c(GREY, "action"), 14) + bar(lm["p_action"]) + "  " + c(BOLD, "%.2f" % lm["p_action"]) + c(GREY, "  " + (lm.get("softened") or lm.get("action", ""))),
              pad(c(GREY, "no token leak"), 14) + bar(leak) + "  " + c(BOLD, "%.2f" % leak) + c(GREY, "  trimmed" if lm.get("trimmed") else ""),
              c(GREY, "decisions ") + c(BOLD, str(len(routes))) + c(GREY, "  latency ") + c(BOLD, "%.0f ms" % lm.get("ms", 0)) + c(GREY, "  last ") + c(BOLD, name_of(last["from"]) + " → " + (", ".join(name_of(t) for t in last.get("to", [])) or "nobody"))]
        if last is not lm:
            rl.append(c(RED, "latest: ") + c(GOLD, last.get("router", "")) + c(GREY, "  " + last.get("note", "")[:W - 16]))
    elif last:
        rl = [c(GREY, "router: ") + c(GOLD, last.get("router", "")) + c(GREY, "  " + last.get("note", "")),
              c(GREY, "decision ") + c(BOLD, last.get("decision", "")) + c(GREY, " → ") + c(BLUE, ", ".join(name_of(t) for t in last.get("to", [])) or "nobody")]
    else:
        rl = [c(GREY, "waiting for the first message…"), c(GREY, "agents send with: studio send --to @peer \"note\"")]
    chain += box("%s · message router" % router_name, rl, W, GREEN)
    if workers:
        wbox_w = max(20, min(30, (W - 2 * (len(workers) - 1)) // len(workers)))
        wboxes = []
        for w in workers:
            wl = [c(BLUE, a.worker_model.capitalize()) + "  " + status_cell(live.get(w, "offline"), frame)] + agent_lines(w, wbox_w - 4)[:3]
            wboxes.append(box(name_of(w), wl, wbox_w, BLUE))
        # fan out: ┴ under the router, ┬ above each worker
        centres = [i * (wbox_w + 2) + wbox_w // 2 for i in range(len(workers))]
        line = [" "] * W
        for x in range(centres[0], centres[-1] + 1):
            line[x] = "─"
        for x in centres:
            line[x] = "┬"
        rc = W // 2
        if centres[0] <= rc <= centres[-1]:
            line[rc] = "┼" if rc in centres else "┴"
        else:
            line[rc] = "┘" if rc > centres[-1] else "└"
            for x in range(min(rc, centres[-1]), max(rc, centres[0]) + 1):
                if line[x] == " ":
                    line[x] = "─"
        chain.append(c(GREY, "".join(line)))
        drops = [" "] * W
        for w, x in zip(workers, centres):
            sent, got = sent_got(w)
            newest = max([r for r in (sent, got) if r], key=lambda r: r.get("t", 0), default=None)
            drops[x] = "●" if fresh(newest) else "│"
        chain.append(c(GREY, "".join(drops)).replace("●", c(GREEN + BOLD, "●") + GREY))
        chain += columns(wboxes, 2)
    outbox, delivered, held = (count(os.path.join(a.studio, "mail", d)) for d in ("outbox", "delivered", "held"))
    chain.append("")
    chain.append(c(GREY, "mailbox  queued ") + c(BOLD, str(outbox)) + c(GREY, "  delivered ") + c(BOLD, str(delivered)) + c(GREY, "  held ") + c(RED if held else GREY, str(held))
                 + c(GREY, "   mem ") + c(BOLD, "%d MB" % mem_free_mb()) + c(GREY, "   %s" % time.strftime("%H:%M")))

    # the live feed: router decisions, deliveries and state changes, newest last
    feed = [r for r in log if r.get("kind") in ("route", "delivered", "held", "failed", "error", "start")] + EVENTS
    feed.sort(key=lambda r: r.get("t", 0))
    FW = max(30, cols - W - 6) if cols >= 150 else max(40, cols - 4)
    feed_rows = [c(BOLD, "live feed") + c(GREY, "  ·  who is talking to whom")]
    for r in feed[-60:]:
        t = time.strftime("%H:%M:%S", time.localtime(r.get("t", 0)))
        if r["kind"] == "state":
            colr = GOLD if r["now"] == "working" else (GREEN if r["now"] in ("done", "idle") else RED)
            feed_rows.append(c(GREY, t) + "  " + pad(c(colr, "status"), 10) + c(BOLD, name_of(r["who"])) + c(GREY, "  %s → %s" % (r["was"], r["now"])))
        elif r["kind"] == "route":
            p = ("%.2f %s" % (r["p_peer"], "sharp" if r.get("c_peer", 0) >= 0.5 else "soft")) if "p_peer" in r else short(r.get("note", ""), 30)
            head = c(GREY, t) + "  " + pad(c(GOLD, r.get("router", "jev")), 10) + c(BOLD, name_of(r.get("from", "")) + " → " + (", ".join(name_of(x) for x in r.get("to", [])) or "nobody")) + c(GREY, "  " + (r.get("softened") or r.get("decision", "")) + "  ") + c(GREEN if "sharp" in p else GREY, p)
            if not r.get("verified", True):
                head += c(RED, "  unverified")
            feed_rows.append(head)
            feed_rows.append(" " * 20 + c(GREY, short(r.get("text", ""), FW - 22)))
        else:
            feed_rows.append(c(GREY, t) + "  " + pad(c(GREEN if r["kind"] == "delivered" else RED, r["kind"]), 10) + c(GREY, "%s → %s %s" % (name_of(r.get("from", "")), name_of(r.get("to", "")), short(r.get("code", "") or r.get("error", "") or r.get("router", ""), 40))))
    if len(feed_rows) == 1:
        feed_rows.append(c(GREY, "quiet: give the architect a task and watch here"))
    if rows_avail:
        feed_rows = feed_rows[:1] + feed_rows[1:][-(max(3, rows_avail - 6)):]

    out = []
    title = "CLAUDE CODE CROSS-SESSION" if kind == "claude" else "CODEX CROSS-SESSION"
    out.append("  " + c(BOLD, title) + c(GREY, "  ·  ") + c(PINK, "SONNET BUILDS" if kind == "claude" else "MAIN SESSION BUILDS")
               + c(GREY, "  ·  ") + c(PURPLE, "OPUS AUDITS" if kind == "claude" else "ARCHITECT AUDITS") + c(GREY, "  ·  " + project)
               + c(GREY, "  ·  agents ") + c(BOLD, "%d/%d" % (sum(1 for n in peers if live.get(n, "offline") != "offline"), len(peers))) + c(GREY, " · active ") + c(BOLD, str(active)))
    out.append("")
    if cols >= 150:
        out += columns([["  " + r for r in chain], feed_rows], 3)
    else:
        out += ["  " + r for r in chain] + [""] + feed_rows
    return out[:rows_avail] if rows_avail else out


def ago(t):
    s = max(0, int(time.time() - t))
    return "%ds" % s if s < 60 else ("%dm" % (s // 60) if s < 3600 else "%dh" % (s // 3600))


def terminal_size():
    """Ask the tty itself. shutil.get_terminal_size trusts stale COLUMNS/LINES
    variables first, and a wrong height makes every frame scroll the pane."""
    for fd in (sys.stdout.fileno(), sys.stdin.fileno()):
        try:
            return os.get_terminal_size(fd)
        except OSError:
            continue
    return os.terminal_size((120, 40))


def main():
    # Nothing but the board may write to this terminal: a stray warning or
    # traceback line scrolls the pane every frame (Python 3.15's commonprefix
    # deprecation did exactly that). Warnings are silenced and stderr goes to a file.
    warnings.simplefilter("ignore")
    p = argparse.ArgumentParser()
    p.add_argument("--studio", required=True)
    p.add_argument("--herdr", default="herdr")
    p.add_argument("--kind", default="claude")
    p.add_argument("--arch-model", default="opus")
    p.add_argument("--main-model", default="sonnet")
    p.add_argument("--worker-model", default="haiku")
    p.add_argument("--once", action="store_true")
    p.add_argument("--refresh", type=float, default=1.0)
    a = p.parse_args()
    frame = 0
    if not a.once:
        try:
            sys.stderr = open(os.path.join(a.studio, "board.err"), "a")
        except OSError:
            pass
        sys.stdout.write(ESC + "?25l" + ESC + "?7l" + ESC + "2J")   # hide cursor, no autowrap, clear
    last, last_size = [], None
    try:
        while True:
            size = terminal_size()
            rows = render(a, frame, size.columns, 0 if a.once else max(1, size.lines - 1))
            if a.once:
                sys.stdout.write("\n".join(rows) + "\n")
                break
            rows = [pad(r, max(20, size.columns - 1)) for r in rows]
            # Rewrite only the lines that changed: a full repaint every second flashes
            # in some terminals. A resize repaints everything once.
            if (size.columns, size.lines) != last_size:
                sys.stdout.write(ESC + "2J")
                last, last_size = [], (size.columns, size.lines)
            out = []
            for i, r in enumerate(rows):
                if i >= len(last) or last[i] != r:
                    out.append(ESC + "%d;1H" % (i + 1) + r)
            if len(rows) < len(last):
                out.append(ESC + "%d;1H" % (len(rows) + 1) + ESC + "J")
            if out:
                sys.stdout.write("".join(out))
                sys.stdout.flush()
            last = rows
            time.sleep(a.refresh)
            frame += 1
    except KeyboardInterrupt:
        pass
    finally:
        if not a.once:
            sys.stdout.write(ESC + "?7h" + ESC + "?25h\n")


if __name__ == "__main__":
    main()
