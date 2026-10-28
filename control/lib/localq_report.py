#!/usr/bin/env python3
"""Report: one screen about the local-model jobs in a time window. Pure functions over plain data; bin/localq wires them."""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import localq_share

STATUSES = ("done", "failed", "blocked", "running", "queued")
MAX_LINES = 24


def job_time(job):
    """Return the most relevant timestamp for a job, or None.

    Prefers finished_at, then started_at, then queued_at. Only numeric (epoch) values are accepted.
    """
    for key in ("finished_at", "started_at", "queued_at"):
        t = job.get(key)
        if isinstance(t, (int, float)):
            return t
    return None


AUDIT_KEYS = ("passes", "skipped", "local", "fallback", "haiku", "sampled", "agreed", "confirmed", "overturned", "confirm_failed", "sample_failed", "offscope", "local_seconds")


def auditor_block(stats, since=None, day=None):
    """Sum bin/re-audit's hourly counters (audit-stats.json) over a window: hours from `since` (unix time) on, or
    every hour of `day` (YYYY-MM-DD) when since is None. Returns None when there is nothing in the window.
    local counts attempts, so the local verdicts used are local - fallback - (sampled - agreed) - overturned - failed confirms; haiku is fallback + samples + confirms.
    The counters are hourly, so a window that starts mid-hour includes that whole hour."""
    total = dict.fromkeys(AUDIT_KEYS, 0)
    floor = time.strftime("%Y-%m-%dT%H", time.localtime(since)) if since is not None else None
    seen = False
    for hour, row in (stats or {}).items():
        if not isinstance(row, dict) or not isinstance(hour, str):
            continue
        if floor is not None and hour < floor:
            continue
        if floor is None and day is not None and not hour.startswith(day):
            continue
        seen = True
        for key in AUDIT_KEYS:
            if isinstance(row.get(key), (int, float)):
                total[key] += row[key]
    if not seen:
        return None
    # local attempts that gave the verdict that was used: not the fallbacks, and not the sampled or confirmed ones where Haiku disagreed
    total["local_used"] = max(0, total["local"] - total["fallback"] - max(0, total["sampled"] - total["agreed"]) - total["overturned"] - total["confirm_failed"])
    total["mean_local_seconds"] = round(total["local_seconds"] / total["local"], 1) if total["local"] else None
    return total


def auditor_line(stats, day):
    """The viewer tooltip line for one day: "today: N local, M Haiku (K fallback), S skipped, agreement A/B"; "" when empty."""
    a = auditor_block(stats, day=day)
    if not a:
        return ""
    return (f"today: {a['local_used']:g} local, {a['haiku']:g} Haiku ({a['fallback']:g} fallback), "
            f"{a['skipped']:g} skipped, agreement {a['agreed']:g}/{a['sampled']:g}")


def build(jobs, paid, since, auditor=None):
    """Aggregate raw jobs into a report dict.

    jobs: list of job dicts. paid: the localq_share.paid_today dict, or None when unknown.
    since: epoch cutoff; jobs older than it are left out (None keeps all).
    """
    rows = []
    for job in jobs:
        t = job_time(job)
        if t is not None and (since is None or t >= since):
            rows.append(job)
    by_kind = {}
    verdicts = {"accept": 0, "fix": 0, "reject": 0, "unreviewed": 0}
    output = 0
    for job in rows:
        status = job.get("status") or "?"
        kind = by_kind.setdefault(job.get("kind") or "?", {})
        kind[status] = kind.get(status, 0) + 1
        if status == "done":
            output += int(job.get("chars_out") or 0)
            verdict = (job.get("review") or {}).get("verdict")
            verdicts[verdict if verdict in verdicts else "unreviewed"] += 1
    timed = [j for j in rows if isinstance(j.get("seconds"), (int, float))]
    timed.sort(key=lambda j: (-j["seconds"], j.get("id", "")))
    slowest = [{"id": j.get("id"), "kind": j.get("kind"), "seconds": j["seconds"]} for j in timed[:5]]
    return {"since": since, "jobs": len(rows), "by_kind": by_kind, "verdicts": verdicts,
            "share": localq_share.share(output, paid), "slowest": slowest, "auditor": auditor}


def render(report):
    """Turn a build() dict into at most 24 lines of text for the terminal."""
    since = report.get("since")
    window = "since " + time.strftime("%Y-%m-%d %H:%M", time.localtime(since)) if since is not None else "all time"
    lines = [f"localq report, {window}: {report['jobs']} jobs"]
    if not report["by_kind"]:
        lines.append("  (no jobs in this window)")
    for kind in sorted(report["by_kind"]):
        counts = report["by_kind"][kind]
        names = [s for s in STATUSES if s in counts] + sorted(s for s in counts if s not in STATUSES)
        lines.append(f"  {kind:<13} " + ", ".join(f"{counts[s]} {s}" for s in names))
    v = report["verdicts"]
    lines.append(f"verdicts: {v['accept']} accept, {v['fix']} fix, {v['reject']} reject, {v['unreviewed']} unreviewed")
    sh = report["share"]
    pct = "n/a" if sh["pct"] is None else f"{sh['pct']}%"
    paid = "unknown" if sh["paid_chars"] is None else str(sh["paid_chars"])
    lines.append(f"output share (estimate): {pct}; local {sh['output_chars']} chars, paid {paid} chars")
    a = report.get("auditor")
    if a:
        mean = "n/a" if a["mean_local_seconds"] is None else f"{a['mean_local_seconds']:g}s"
        lines.append(f"auditor: {a['passes']:g} passes, {a['skipped']:g} skipped, {a['local_used']:g} local, "
                     f"{a['haiku']:g} Haiku ({a['fallback']:g} fallback, {a['sampled']:g} sampled), "
                     f"agreement {a['agreed']:g}/{a['sampled']:g}, local {mean} mean"
                     + (f", confirms {a['confirmed']:g} ({a['overturned']:g} overturned" + (f", {a['confirm_failed']:g} failed" if a["confirm_failed"] else "")
                        + (f", {a['sample_failed']:g} samples failed" if a["sample_failed"] else "") + ")"
                        if any(a[k] for k in ("confirmed", "overturned", "confirm_failed", "sample_failed")) else "")
                     + (f", off-scope checks {a['offscope']:g}" if a["offscope"] else ""))
    if report["slowest"]:
        lines.append("slowest:")
        for s in report["slowest"]:
            lines.append(f"  {s['id']}  {s['kind']}  {s['seconds']:g}s")
    return "\n".join(lines[:MAX_LINES])
