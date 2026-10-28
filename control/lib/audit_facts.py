"""Facts about one studio team, computed in Python, and the short prompt that sends only those facts (standard library)."""
import json
import re
import time

WINDOW = 900          # seconds: "recent" for routes and commits
NAME_CHARS = 16       # characters kept per agent name
MAX_NAMES = 3         # agent names listed per state
MAX_TASKS = 3         # architect task lines in the prompt
SKEW = 300            # seconds a timestamp may lie ahead of now before it counts as unknown
MAX_EVIDENCE = 3      # commits shown as off-scope evidence
SUBJECT_CHARS = 60
PATH_CHARS = 40
SUMMARY_CHARS = 140   # the local model's summary is cut to this
MAX_LABEL = 40        # characters of the team label in the prompt
LIMIT = 1500          # the prompt stays under this many characters
TASK_CHARS = 80      # characters kept per task line
STATES = ("working", "idle", "done")


def _safe(text, n):
    """Text that needs no JSON escape, cut to n characters: quotes, backslashes and control characters become underscores."""
    return re.sub(r'[\x00-\x1f\x7f"\\]', "_", str(text))[:n]


def _minutes(now, t):
    return None if t is None else max(0, int((now - t) // 60))


def _tasks(routes):
    """The architect's task lines, oldest first, from the routes the snapshot holds."""
    return [str(r.get("text", "")) for r in routes if str(r.get("from", "")).endswith("architect")][-MAX_TASKS:]


def _touches(tasks, commits):
    """yes/no/unknown: does a recent commit touch a file named in a task line? unknown without commits, paths or file names."""
    named = {m.lower() for line in tasks for m in re.findall(r"[\w./-]+\.\w{1,5}\b", line)}
    paths = [p.lower() for c in commits for p in c.get("paths") or []]
    if not named or not paths:
        return "unknown"
    for name in named:
        if any(p == name or p.endswith("/" + name) or name.endswith("/" + p) or p.rsplit("/", 1)[-1] == name.rsplit("/", 1)[-1] for p in paths):
            return "yes"
    return "no"


def _evidence(commits):
    """What a model needs to judge off-scope: the newest commits' subjects and up to 3 paths each (escape-free, cut short)."""
    return [{"subject": _safe(c.get("subject", ""), SUBJECT_CHARS), "paths": [_safe(p, PATH_CHARS) for p in (c.get("paths") or [])[:3]]}
            for c in commits[:MAX_EVIDENCE]]


def facts(snap, now=None):
    """The facts for one team snapshot: agents by state, minutes since the last route and commit, routes in the window,
    a repeated-message flag, and whether commits touch the files the tasks name."""
    now = time.time() if now is None else now
    agents = {state: [] for state in STATES + ("other",)}
    for a in snap.get("agents") or []:
        agents[a.get("status") if a.get("status") in STATES else "other"].append(_safe(a.get("name"), NAME_CHARS))
    routes, commits = snap.get("routes") or [], snap.get("commits") or []
    # a time up to SKEW ahead is clock skew between machines (counts as now); further ahead it is unknown and left out
    times = [r["t"] for r in routes if isinstance(r.get("t"), (int, float)) and r["t"] <= now + SKEW]
    ctimes = [c["t"] for c in commits if isinstance(c.get("t"), (int, float)) and c["t"] <= now + SKEW]
    texts = [(str(r.get("from")), str(r.get("to")), str(r.get("text", ""))) for r in routes if r.get("text")]    # one text sent to several people is not a repeat
    tasks = _tasks(routes)    # whole lines: file names past the display cut still count
    touch = _touches(tasks, commits)
    return {
        "agents": {s: {"count": len(n), "names": n[:MAX_NAMES]} for s, n in agents.items() if n},
        "minutes_since_route": _minutes(now, max(times) if times else None),
        "minutes_since_commit": _minutes(now, max(ctimes) if ctimes else None),
        "routes_last_15_min": sum(1 for t in times if now - t <= WINDOW),
        "commits_last_15_min": sum(1 for t in ctimes if now - t <= WINDOW),
        "repeated_message": len(texts) != len(set(texts)),
        "commits_touch_task_files": touch,
        "tasks": [t[:TASK_CHARS] for t in tasks],
        "commit_evidence": _evidence(commits) if touch == "no" else [],
    }


ALERT = ("stalled", "looping", "off-scope")    # the states that need Claude


def derive_status(facts_):
    """The state from the facts alone: no agent working -> idle; repeated_message -> looping; a route or commit in the
    last 15 min -> progressing; otherwise stalled. (off-scope is the one judgement left to a model, see plan.)"""
    if not (facts_["agents"].get("working") or {}).get("count"):
        return "idle"
    if facts_["repeated_message"]:
        return "looping"
    return "progressing" if facts_["routes_last_15_min"] or facts_["commits_last_15_min"] else "stalled"


def _fit(label, facts_, build):
    """Build a prompt and keep it under LIMIT characters. The model needs the task lines and the commit evidence to judge
    off-scope, so they go last: first the task lines are shortened (to 40 characters), then the agent names and the commit paths go,
    then older task lines, then the evidence, the last task line and the agents block."""
    state = {"label": _safe(label, MAX_LABEL), "tasks": list(facts_["tasks"]),
             "body": {k: v for k, v in facts_.items() if k != "tasks" and not (k == "commit_evidence" and not v)}}

    def shorten():
        longest = max((len(t) for t in state["tasks"]), default=0)
        if longest <= 40:
            return False
        state["tasks"] = [t[:max(40, longest // 2)] for t in state["tasks"]]
        return True

    def body(**change):
        state["body"] = dict(state["body"], **change)
        return True

    def names():
        return body(agents={k: {"count": v["count"]} for k, v in state["body"]["agents"].items()})

    def paths():
        return "commit_evidence" in state["body"] and body(commit_evidence=[{"subject": e["subject"]} for e in state["body"]["commit_evidence"]])

    def older_tasks():
        if len(state["tasks"]) <= 1:
            return False
        state["tasks"].pop(0)
        return True

    def evidence():
        state["body"] = {k: v for k, v in state["body"].items() if k != "commit_evidence"}
        return True

    def last_task():
        state["tasks"] = []
        return True

    def agents():
        state["body"] = {k: v for k, v in state["body"].items() if k != "agents"}
        return True

    text = build(state["label"], state["body"], state["tasks"])
    for step in (shorten, shorten, shorten, names, paths, older_tasks, older_tasks, evidence, last_task, agents):
        if len(text) < LIMIT:
            break
        if step():
            text = build(state["label"], state["body"], state["tasks"])
    return text, bool(state["tasks"]) and "commit_evidence" in state["body"]


def _dump(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def prompt_kept(label, facts_, judge=True):
    """(text, kept): the rules prompt (Haiku, and the sampled check) and whether a task line and the commit evidence are still in
    it. It holds the facts and the last task lines, the rules, two examples and the reply shape, under 1500 characters.
    Without judge the off-scope check is left out."""
    def build(label, body, tasks):
        first = ("Check first: off-scope if an agent is working, commits_touch_task_files is no and commit_evidence is clearly "
                 "unrelated to the tasks.\nOtherwise: " if judge else "")
        return (
            f"Audit one AI dev studio team ({label}) from these facts only.\n"
            f"Facts: {_dump(body)}\n"
            f"Last architect tasks: {_dump(tasks)}\n"
            "Rules:\n"
            f"{first}idle = no agent working; looping = repeated_message is true;\n"
            "progressing = a route or commit in the last 15 min; stalled = otherwise.\n"
            f"needs_claude is true only for stalled, looping{', off-scope' if judge else ''}.\n"
            'Example: none working -> {"status":"idle","summary":"All agents are done.","concern":"","needs_claude":false}\n'
            'Example: 1 working, 0 routes, 0 commits -> {"status":"stalled","summary":"Nothing moved.","concern":"no routes or commits","needs_claude":true}\n'
            'Reply JSON only: {"status":"...","summary":"one sentence","concern":"","needs_claude":true|false}')
    return _fit(label, facts_, build)


def prompt(label, facts_, judge=True):
    """The rules prompt text (see prompt_kept)."""
    return prompt_kept(label, facts_, judge)[0]


def state_prompt_kept(label, facts_, state, judge):
    """(text, kept): the local model's prompt. The status is already decided (state); it writes the summary and concern, and when
    judge is true it may also answer off-scope. kept: a task line and the commit evidence survived the fit."""
    def build(label, body, tasks):
        ask = (f"The commits touch none of the files the tasks name (see commit_evidence). If they are clearly unrelated to the tasks, "
               f"set status to off-scope; otherwise keep {state}.\n" if judge else f"Keep status {state}.\n")
        return (
            f"Audit one AI dev studio team ({label}). Code already decided its status: {state}.\n"
            f"Facts: {_dump(body)}\n"
            f"Last architect tasks: {_dump(tasks)}\n"
            f"{ask}"
            "Write a one-sentence summary (under 140 characters) of what the facts show and a concern (empty if none). "
            "State only what the facts show, no guesses like 'may have stalled'.\n"
            f'Reply JSON only: {{"status":"{state}","summary":"one sentence","concern":"","needs_claude":false}}')
    return _fit(label, facts_, build)


def state_prompt(label, facts_, state, judge):
    """The local prompt text (see state_prompt_kept)."""
    return state_prompt_kept(label, facts_, state, judge)[0]


def plan(label, snap, now=None):
    """Everything one team ask needs: the facts, the code's state, whether off-scope is possible, and both prompts.
    The local model never judges off-scope: its prompt has the state fixed and no commit evidence (judge is always False), and it
    writes the summary only. When off-scope is possible (an agent is working and commits_touch_task_files is no) the team is asked
    of Haiku directly with haiku_prompt (offscope True); that needs a task line and the commit evidence to survive the fit in the
    rules prompt, otherwise off-scope is not possible and the code's state stands."""
    f = facts(snap, now)
    state = derive_status(f)
    offscope = f["commits_touch_task_files"] == "no" and state != "idle"
    haiku, kept = prompt_kept(label, f, offscope)
    if offscope and not kept:
        offscope = False
        haiku, _ = prompt_kept(label, dict(f, commit_evidence=[]), False)
    local, _ = state_prompt_kept(label, dict(f, commit_evidence=[]), state, False)
    return {"facts": f, "state": state, "judge": False, "offscope": offscope, "local_prompt": local, "haiku_prompt": haiku}


def fix_reply(text, plan_):
    """The local reply with its status fixed by code: the code's state, or off-scope when the model may judge it and chose it;
    needs_claude follows the final status. A reply that is not a JSON object with a summary is returned unchanged (it will fail
    the usual validation and fall back to Haiku)."""
    try:
        start = str(text).find("{")
        verdict, _ = json.JSONDecoder().raw_decode(str(text)[start:])
    except (ValueError, TypeError):
        return text
    if not isinstance(verdict, dict) or not isinstance(verdict.get("summary"), str) or start < 0 or str(text).lstrip().startswith("["):
        return text
    said = str(verdict.get("status", "")).strip().lower().replace("_", "-")
    status = "off-scope" if plan_["judge"] and said == "off-scope" else plan_["state"]
    verdict.setdefault("concern", "")
    verdict["summary"] = verdict["summary"].strip()[:SUMMARY_CHARS]
    return json.dumps(dict(verdict, status=status, needs_claude=status in ALERT))
