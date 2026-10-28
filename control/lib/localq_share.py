#!/usr/bin/env python3
"""Output share (estimate): how much of today's written output came from local models, against what the paid agents wrote.

  local output = chars_out of today's done localq jobs (localq/stats.json, written by bin/localq; the old
                 state/localq-stats.json is read only while it still exists)
  paid chars   = len(text) of today's messages in the laptop studio's .studio/mail/delivered
                 each message counts once in the total; by_sender tallies it as chars in, by_recipient as chars out
  pct          = local output / (local output + paid), rounded; None when there is nothing to compare, or when the
                 mail folder is not known (no lap project in the config)

Characters stand in for tokens, so this is an estimate. The mail folder is the laptop's studio only: the Minis
keep their own mail. Standard library only.
"""
import json, os, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import re_studio_config as cfg


def mail_dir():
    """<lap project>/.studio/mail/delivered, from the laptop's machine entry; None when not configured."""
    try:
        project = cfg.machine("lap").get("project")
    except KeyError:
        return None
    return os.path.join(os.path.expanduser(project), ".studio", "mail", "delivered") if project else None


def paid_today(day, folder, since=None):
    """Delivered messages in folder for day, or None when the folder is not known."""
    if not folder:
        return None
    out = {"chars": 0, "messages": 0, "by_sender": {}, "by_recipient": {}}
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                msg = json.load(f)
        except (OSError, ValueError):
            continue
        t = msg.get("t") if isinstance(msg, dict) else None
        if not isinstance(t, (int, float)):
            continue
        if since is not None:
            if t < since:
                continue
        elif time.strftime("%Y-%m-%d", time.localtime(t)) != day:
            continue
        text = msg.get("text")
        n = len(text) if isinstance(text, str) else 0
        sender, recipient = str(msg.get("from") or "?"), str(msg.get("to") or "?")
        out["chars"] += n
        out["messages"] += 1
        out["by_sender"][sender] = out["by_sender"].get(sender, 0) + n
        out["by_recipient"][recipient] = out["by_recipient"].get(recipient, 0) + n
    return out


def share(output_chars, paid):
    """The output share: local output against local output + paid. paid is None when unknown, which makes it unknown too."""
    paid_chars = None if paid is None else paid["chars"]
    total = output_chars + (paid_chars or 0)
    pct = None if paid_chars is None or total == 0 else round(100 * output_chars / total)
    return {"output_chars": output_chars, "paid_chars": paid_chars, "pct": pct}
