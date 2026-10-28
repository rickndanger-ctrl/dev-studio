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


def build(jobs, paid, since):
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
            "share": localq_share.share(output, paid), "slowest": slowest}


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
    if report["slowest"]:
        lines.append("slowest:")
        for s in report["slowest"]:
            lines.append(f"  {s['id']}  {s['kind']}  {s['seconds']:g}s")
    return "\n".join(lines[:MAX_LINES])
