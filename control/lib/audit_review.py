"""Local-first audit review with sampled paid comparisons."""
import json
import random
import time


def _hour():
    return time.strftime("%Y-%m-%dT%H", time.localtime())


def bump(stats, key, n=1):
    """Add a numeric count to the current hour when stats are enabled."""
    if stats is not None:
        hour = stats.setdefault(_hour(), {})
        hour[key] = hour.get(key, 0) + n


def _failure(stats, reason):
    if stats is not None:
        failures = stats.setdefault(_hour(), {}).setdefault("local_fail", {})
        failures[reason] = failures.get(reason, 0) + 1


SYNONYMS = {"working": "progressing", "waiting": "idle", "stuck": "stalled", "off_scope": "off-scope"}


def normalize(verdict, allowed):
    """Map a synonym status to the allowed one (working->progressing, waiting->idle, stuck->stalled, off_scope->off-scope).
    A status that is already allowed is kept, so the laptop's own "stuck" stays. Returns the verdict (copied when changed)."""
    status = verdict.get("status") if isinstance(verdict, dict) else None
    if isinstance(status, str) and status not in allowed:
        mapped = SYNONYMS.get(status.strip().lower().replace(" ", "_"))
        if mapped in allowed:
            return dict(verdict, status=mapped)
    return verdict


def _haiku_verdict(raw, allowed, keys):
    """Haiku's reply held to the local shape: an object with every key and an allowed status (synonyms normalised).
    The whole dict is kept (it carries cost_usd); anything else raises."""
    verdict = normalize(raw, allowed)
    if not isinstance(verdict, dict):
        raise ValueError("haiku response is not a JSON object")
    if any(key not in verdict for key in keys):
        raise ValueError("missing required key in haiku response")
    if not isinstance(verdict.get("status"), str) or verdict["status"] not in allowed:
        raise ValueError("invalid status in haiku response")
    return verdict


def _local_verdict(text, allowed, keys):
    if not isinstance(text, str):
        raise ValueError("local response is not text")
    if text.lstrip().startswith("["):
        raise ValueError("local response is not a JSON object")
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in local response")
    try:
        verdict, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        raise ValueError("invalid JSON in local response")
    if not isinstance(verdict, dict):
        raise ValueError("local response is not a JSON object")
    if any(key not in verdict for key in keys):
        raise ValueError("missing required key in local response")
    verdict = normalize(verdict, allowed)
    if not isinstance(verdict.get("status"), str) or verdict["status"] not in allowed:
        raise ValueError("invalid status in local response")
    return {key: verdict[key] for key in keys}


def ask_haiku_only(prompt, allowed, keys, haiku, stats=None):
    """Ask Haiku alone (no local model): used when only Haiku may make the call. Returns (verdict, "haiku-offscope"); counts the
    ask as offscope and haiku. A failing or malformed reply raises (the ask is retried)."""
    bump(stats, "offscope")
    bump(stats, "haiku")
    return _haiku_verdict(haiku(prompt), allowed, keys), "haiku-offscope"


def review(prompt, allowed, keys, local, haiku, rng=None, sample=0.1, stats=None, on_sample=None, haiku_prompt=None,
           confirm=(), on_confirm=None):
    """Review locally, falling back or sampling Haiku; return verdict and source.

    on_sample(local_verdict, haiku_verdict) is called for every sampled pair, before a disagreement
    replaces the local verdict; an error in it is ignored. haiku_prompt, when given, is what Haiku is asked instead of prompt
    (the local model gets a prompt that fixes the status; Haiku judges from the full rules).

    confirm: statuses that must be confirmed by Haiku. A valid local verdict with one of them that was not sampled is checked
    by Haiku (source "confirm"); Haiku's status wins when they differ (counted as overturned). on_confirm(local, haiku) is
    called with the pair. A failing or invalid Haiku reply raises (counted as confirm_failed): an unconfirmed alert is not reported.
    """
    started = time.monotonic()
    bump(stats, "local")
    try:
        verdict = _local_verdict(local(prompt), allowed, keys)
    except Exception as exc:
        bump(stats, "local_seconds", time.monotonic() - started)
        reason = str(exc).strip() or type(exc).__name__
        if isinstance(exc, TimeoutError) and "timeout" not in reason.lower():
            reason = "timeout: " + reason
        _failure(stats, reason)
        bump(stats, "fallback")
        bump(stats, "haiku")
        return _haiku_verdict(haiku(haiku_prompt or prompt), allowed, keys), "haiku-fallback"
    bump(stats, "local_seconds", time.monotonic() - started)
    if (rng or random.random)() < sample:
        bump(stats, "haiku")    # a Haiku call is made; it counts as a sample only if the answer is usable
        try:
            comparison = _haiku_verdict(haiku(haiku_prompt or prompt), allowed, keys)
        except Exception:
            bump(stats, "sample_failed")    # no usable Haiku answer, so nothing is compared
            if verdict.get("status") in confirm:    # an alert the sample could not check is unconfirmed: it is not reported
                bump(stats, "confirm_failed")
                raise
            return verdict, "local"    # a calm local verdict stands, uncounted as sampled or compared
        bump(stats, "sampled")
        if on_sample is not None:
            try:
                on_sample(dict(verdict), dict(comparison))
            except Exception:
                pass    # logging the pair must never change the verdict
        if comparison.get("status") == verdict.get("status"):
            bump(stats, "agreed")
        else:
            verdict = comparison
        return verdict, "local+sample"
    if verdict.get("status") in confirm:
        bump(stats, "confirmed")
        bump(stats, "haiku")
        try:
            comparison = _haiku_verdict(haiku(haiku_prompt or prompt), allowed, keys)
        except Exception:
            bump(stats, "confirm_failed")    # the local verdict was not used: the ask fails and is retried
            raise
        if on_confirm is not None:
            try:
                on_confirm(dict(verdict), dict(comparison))
            except Exception:
                pass    # logging the pair must never change the verdict
        if comparison.get("status") != verdict.get("status"):
            bump(stats, "overturned")
            verdict = comparison
        return verdict, "confirm"
    return verdict, "local"
