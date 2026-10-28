"""Side-by-side local vs Haiku verdicts for the studio teams (used by bin/audit-compare)."""
import time

import audit_review


def _checked(verdict, allowed, keys):
    """The Haiku verdict held to the same shape as a local one: a dict with every key and an allowed status."""
    if not isinstance(verdict, dict):
        raise ValueError("haiku response is not a JSON object")
    verdict = audit_review.normalize(verdict, allowed)
    if any(key not in verdict for key in keys):
        raise ValueError("missing required key in haiku response")
    if not isinstance(verdict.get("status"), str) or verdict["status"] not in allowed:
        raise ValueError("invalid status in haiku response")
    return {key: verdict[key] for key in keys}


def compare(units, plan_fn, allowed, keys, local, haiku, fix=lambda text, plan: text):
    """One row per (key, label, snapshot) unit. plan_fn(label, snap) gives the plan with local_prompt and haiku_prompt; the local reply
    goes through fix(text, plan) (status fixed by code) before the usual check. A row holds each side's verdict (or error text),
    its seconds and its prompt characters."""
    rows = []
    for key, label, snap in units:
        plan = plan_fn(label, snap)
        row = {"key": key, "label": label, "state": plan.get("state", ""),
               "local_chars": len(plan["local_prompt"]), "haiku_chars": len(plan["haiku_prompt"])}
        for side, ask in (("local", lambda: audit_review._local_verdict(fix(local(plan["local_prompt"]), plan), allowed, keys)),
                          ("haiku", lambda: _checked(haiku(plan["haiku_prompt"]), allowed, keys))):
            started = time.monotonic()
            try:
                row[side] = ask()
            except Exception as exc:
                row[side] = {"error": str(exc).strip() or type(exc).__name__}
            row[side + "_seconds"] = round(time.monotonic() - started, 1)
        rows.append(row)
    return rows


def _cell(verdict):
    if "error" in verdict:
        return "ERROR: " + verdict["error"], ""
    return str(verdict.get("status", "")), str(verdict.get("summary", ""))


def render(rows):
    """Plain text: per team the two statuses and summaries, then agreed/comparable (pairs where both sides answered) and the error count."""
    lines, agree, comparable = [], 0, 0
    for r in rows:
        (ls, lsum), (hs, hsum) = _cell(r["local"]), _cell(r["haiku"])
        valid = "error" not in r["local"] and "error" not in r["haiku"]
        same = valid and ls == hs
        comparable += valid
        agree += same
        verdict = ("SAME" if same else "DIFFERENT") if valid else "NOT COMPARED"
        lines += [f"{r['key']} ({r['label']}, code says {r.get('state') or '-'}): {verdict}",
                  f"  local ({r.get('local_chars', 0)} chars, {r.get('local_seconds', 0):.1f}s): {ls} - {lsum}",
                  f"  haiku ({r.get('haiku_chars', 0)} chars, {r.get('haiku_seconds', 0):.1f}s): {hs} - {hsum}"]
    lines.append(f"agreement: {agree}/{comparable} comparable, {len(rows) - comparable} with an error")
    return "\n".join(lines)
