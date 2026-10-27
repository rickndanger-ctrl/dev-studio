#!/usr/bin/env python3
"""Dev Studio message router.

Every message between agents crosses this process, so the board can show the
routing. One JSON file per message in <studio>/mail/outbox; the daemon asks the
router model (Jev by default) three typed questions, decides in code, delivers
with `herdr agent prompt`, and appends one JSON line per step to router.log.

    router.py send   --studio DIR --from NAME --to NAME|auto --text "..."
    router.py daemon --studio DIR --herdr "herdr --session dev-studio"
    router.py ask    --studio DIR --from NAME --to auto --text "..."   (dry run, prints the decision)

Standard library only, so it runs on the Mac's own python3.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

JEV_URL = os.environ.get("TYPESAFE_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
CLEF_URL = os.environ.get("CLEF_URL", "")
ROUTER = os.environ.get("ROUTER", "jev")
MAX_NOTE = 400  # characters a trimmed message keeps

# Confidence gates (see docs.typesafe.ai/confidence): below the floor we do not guess.
PEER_FLOOR = 0.5
DROP_P = 0.9
LEAK_P = 0.7
BROADCAST_P = 0.7   # below this a "broadcast" is a forward to the chosen peer, not a storm


def now():
    return time.time()


def ts(t=None):
    return time.strftime("%H:%M:%S", time.localtime(t or now()))


class Studio:
    def __init__(self, path):
        self.dir = path
        self.outbox = os.path.join(path, "mail", "outbox")
        self.delivered = os.path.join(path, "mail", "delivered")
        self.held = os.path.join(path, "mail", "held")
        self.log = os.path.join(path, "router.log")
        self.peers_file = os.path.join(path, "peers.json")
        for d in (self.outbox, self.delivered, self.held):
            os.makedirs(d, exist_ok=True)

    def peers(self):
        try:
            with open(self.peers_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def write_log(self, row):
        row.setdefault("t", now())
        with open(self.log, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# --- sending -----------------------------------------------------------------

def cmd_send(a):
    s = Studio(a.studio)
    text = a.text if a.text is not None else sys.stdin.read()
    text = text.strip()
    if not text:
        sys.exit("studio send: empty message")
    # Sign the message with the secret the studio put in this pane's environment
    # at launch (STUDIO_TOKEN, one per agent). The router holds every token and
    # checks the signature, so one agent cannot speak as another. A message
    # without a valid signature is still delivered, but marked UNVERIFIED.
    msg = {"id": uuid.uuid4().hex[:8], "t": now(), "from": a.sender, "to": a.to.lstrip("@"), "text": text, "tries": 0}
    token = os.environ.get("STUDIO_TOKEN", "")
    if token:
        msg["sig"] = sign(token, msg)
    tmp = os.path.join(s.outbox, "." + msg["id"] + ".tmp")
    final = os.path.join(s.outbox, "%d-%s.json" % (int(msg["t"] * 1000), msg["id"]))
    with open(tmp, "w") as f:
        json.dump(msg, f)
    os.rename(tmp, final)   # atomic: the daemon never sees a half-written file
    print("queued %s -> %s" % (msg["id"], msg["to"]))


MAX_AGE = 600       # seconds: an older signed message is a replay, not a note


def sign(token, msg):
    payload = "\x1f".join([msg["id"], msg["from"], msg["to"], msg["text"], "%.3f" % float(msg["t"])]).encode()
    return hmac.new(token.encode(), payload, hashlib.sha256).hexdigest()


def tokens_from_env():
    """Name -> token, from STUDIO_TOKENS (JSON) in the router's own environment."""
    try:
        return json.loads(os.environ.get("STUDIO_TOKENS", "") or "{}")
    except ValueError:
        return {}


# --- judging -----------------------------------------------------------------

def questions_for(msg, peers):
    """Three narrow judgments over the same state, asked together."""
    criteria = {name: p.get("role", name) for name, p in peers.items() if name != msg["from"]}
    criteria["nobody"] = "no peer needs this message"
    return {
        "which_peer": {
            "type": "choice",
            "instructions": "Which peer must receive this message first? Consider who owns the files or decision it talks about.",
            "criteria": criteria,
        },
        "action": {
            "type": "choice",
            "instructions": "What should the message router do with this message?",
            "criteria": {
                "forward": "deliver it to the one peer who needs it",
                "broadcast": "every peer needs it (a breaking change to something shared)",
                "escalate": "the architect must decide or approve something here",
                "drop": "nobody needs it: chatter, an acknowledgement, or a duplicate",
            },
        },
        "token_leak": {
            "type": "noul",
            "instructions": "Does the message paste conversation history, whole files, diffs or secrets instead of a short plain-text note?",
            "criteria": {"true": "pasted history, file contents, diffs, logs or credentials", "false": "a short note saying what changed and who should care"},
        },
    }


def ask_model(url, key, msg, peers):
    state = {"from": msg["from"], "addressed_to": msg["to"], "text": msg["text"][:4000],
             "peers": [{"name": n, "role": p.get("role", ""), "status": p.get("status", "")} for n, p in peers.items()]}
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions_for(msg, peers)}).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    t0 = now()
    with urllib.request.urlopen(req, timeout=15) as r:
        answers = json.load(r)["answers"]
    return answers, (now() - t0) * 1000


def judge(msg, peers, router=None):
    """Return (decision, evidence). decision: {action, to:[names], note, router}."""
    router = router or ROUTER
    peers_live = {n: p for n, p in peers.items()}
    architect = next((n for n, p in peers_live.items() if p.get("role", "").startswith("architect")), None)
    explicit = msg["to"] if msg["to"] != "auto" and msg["to"] in peers_live else None
    ev = {"router": router}

    answers = None
    if router in ("jev", "clef") and len(peers_live) > 0:
        url = CLEF_URL if router == "clef" else JEV_URL
        key = os.environ.get("CLEF_API_KEY" if router == "clef" else "TYPESAFE_API_KEY", "")
        if not url or not key:
            ev["note"] = "%s not configured (%s); used rules" % (router, "URL" if not url else "API key")
            router = "rules"
        else:
            try:
                answers, ev["ms"] = ask_model(url, key, msg, peers_live)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                ev["note"] = "%s unreachable (%s); used rules" % (router, str(e)[:80])
                router = "rules"
    ev["router"] = router

    text = msg["text"]
    if answers:
        wp, ac, leak = answers["which_peer"], answers["action"], answers["token_leak"]["noul"]
        ev.update({"peer": wp["choice"], "p_peer": round(wp["probabilities"].get(wp["choice"], 0), 2),
                   "c_peer": round(wp["confidence"], 2), "action": ac["choice"],
                   "p_action": round(ac["probabilities"].get(ac["choice"], 0), 2), "leak": round(leak, 2),
                   "probabilities": {"peer": wp["probabilities"], "action": ac["probabilities"]}})
        if leak > LEAK_P:
            text = text[:MAX_NOTE].rstrip() + " …[trimmed by the router: send a short note, not a dump]"
            ev["trimmed"] = True
        action = ac["choice"]
        if action == "broadcast" and ac["probabilities"].get("broadcast", 0) < BROADCAST_P:
            action = "forward"
            ev["softened"] = "broadcast→forward"
        if explicit:
            # The sender named a peer. Honour it unless the router is sure it is noise.
            if action == "drop" and ac["probabilities"].get("drop", 0) > DROP_P:
                return {"action": "drop", "to": [], "note": text}, ev
            if action == "broadcast" and ac["probabilities"].get("broadcast", 0) > DROP_P:
                return {"action": "broadcast", "to": [n for n in peers_live if n != msg["from"]], "note": text}, ev
            return {"action": "forward", "to": [explicit], "note": text}, ev
        if action == "drop":
            return {"action": "drop", "to": [], "note": text}, ev
        if action == "broadcast":
            return {"action": "broadcast", "to": [n for n in peers_live if n != msg["from"]], "note": text}, ev
        if action == "escalate" or wp["confidence"] < PEER_FLOOR or wp["choice"] == "nobody":
            target = architect or next(iter(peers_live))
            return {"action": "escalate", "to": [target] if target != msg["from"] else [], "note": text}, ev
        return {"action": "forward", "to": [wp["choice"]], "note": text}, ev

    # rules: no model. Explicit addresses go through; everything else to the architect.
    if explicit:
        return {"action": "forward", "to": [explicit], "note": text}, ev
    target = architect or next(iter(peers_live), None)
    if not target or target == msg["from"]:
        return {"action": "drop", "to": [], "note": text}, ev
    return {"action": "escalate", "to": [target], "note": text}, ev


# --- delivering --------------------------------------------------------------

def herdr_cmd(herdr):
    return herdr.split()


def live_agents(herdr):
    """Name -> status from `herdr agent list`; empty on any failure."""
    return {n: a["status"] for n, a in agent_table(herdr).items()}


def agent_table(herdr):
    """Name -> {status, pane} from `herdr agent list`; empty on any failure."""
    try:
        out = subprocess.run(herdr_cmd(herdr) + ["agent", "list"], capture_output=True, text=True, timeout=10)
        data = json.loads(out.stdout)
        return {a["name"]: {"status": a.get("agent_status", "unknown"), "pane": a.get("pane_id", "")}
                for a in data["result"]["agents"] if a.get("name")}
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return {}


def already_handled(studio, msg_id):
    """Every handled message is archived as <ts>-<id>.json, so the archive is the
    replay record and survives a router restart."""
    for d in (studio.delivered, studio.held):
        try:
            if any(n.endswith("-%s.json" % msg_id) for n in os.listdir(d)):
                return True
        except OSError:
            pass
    return False


def verify_sender(msg, tokens, studio=None):
    """Was this message signed with the claimed sender's own token?
    Returns (verified, note). An unsigned or badly signed message (a person at
    a terminal, or an impostor) is delivered marked UNVERIFIED, so recipients
    never treat the header as proof."""
    token = tokens.get(msg["from"])
    if not token:
        return False, "no token known for %s" % msg["from"]
    if not msg.get("sig"):
        return False, "unsigned: sent from outside %s's pane" % msg["from"]
    if not hmac.compare_digest(sign(token, msg), msg["sig"]):
        return False, "bad signature: not sent by %s" % msg["from"]
    if abs(now() - float(msg.get("t", 0))) > MAX_AGE:
        return False, "stale: signed %d s from now" % abs(now() - float(msg["t"]))
    if studio and already_handled(studio, msg["id"]):
        return False, "replay: id %s was already handled" % msg["id"]
    return True, ""


ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def valid_message(msg):
    """Only well-formed messages reach the judge: the id and names become file
    names and agent targets, so they are letters, digits, - and _ only."""
    return (isinstance(msg, dict) and all(isinstance(msg.get(k), str) and msg.get(k) for k in ("id", "from", "to", "text"))
            and ID_RE.fullmatch(msg["id"]) is not None and NAME_RE.fullmatch(msg["from"]) is not None
            and (msg["to"] == "auto" or NAME_RE.fullmatch(msg["to"]) is not None)
            and isinstance(msg.get("t", 0), (int, float)))


HEADER_RE = re.compile(r"\[\s*studio\s+relay", re.IGNORECASE)
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def safe_text(text):
    """One line of printable text, with no fake relay header inside it."""
    text = CONTROL_RE.sub(" ", text.replace("\n", " "))
    return HEADER_RE.sub("(relay)", text).strip()


def deliver(herdr, peer, sender, text, verified=True):
    tag = "from @%s" % sender if verified else "from @%s, UNVERIFIED" % sender
    prompt = "[studio relay · %s] %s" % (tag, safe_text(text))
    r = subprocess.run(herdr_cmd(herdr) + ["agent", "prompt", peer, prompt], capture_output=True, text=True, timeout=20)
    if r.returncode == 0:
        return "delivered", ""
    err = (r.stderr or r.stdout).strip()
    try:
        code = json.loads(err)["error"]["code"]
    except (ValueError, KeyError, TypeError):
        code = err[:80] or "error"
    return ("held" if code in ("agent_blocked", "agent_not_found") else "failed"), code


def process(s, herdr, path, peers, tokens):
    with open(path) as f:
        msg = json.load(f)
    if not valid_message(msg):
        raise ValueError("not a studio message")
    verified, why = verify_sender(msg, tokens, s)
    decision, ev = judge(msg, peers)
    row = {"kind": "route", "id": msg["id"], "from": msg["from"], "addressed": msg["to"], "decision": decision["action"],
           "to": decision["to"], "text": msg["text"][:120], "verified": verified}
    if not verified:
        row["unverified"] = why
    row.update(ev)
    s.write_log(row)
    outcome = "delivered"
    for peer in decision["to"]:
        if peer == msg["from"]:
            continue
        status, code = deliver(herdr, peer, msg["from"], decision["note"], verified)
        s.write_log({"kind": status, "id": msg["id"], "from": msg["from"], "to": peer, "code": code})
        if status != "delivered":
            outcome = status
    if outcome == "held" and msg.get("tries", 0) < 20:
        msg["tries"] = msg.get("tries", 0) + 1
        msg["retry_at"] = now() + 15     # the peer is busy or blocked: try again later
        with open(path, "w") as f:
            json.dump(msg, f)
        return
    # Archive under a name the router builds from the message itself, so the
    # replay record cannot be shaped by whoever wrote the outbox file.
    archive = "%d-%s.json" % (int(float(msg["t"]) * 1000), msg["id"])
    dest = os.path.join(s.delivered if outcome == "delivered" or not decision["to"] else s.held, os.path.basename(archive))
    if os.path.exists(dest):
        dest += "." + uuid.uuid4().hex[:6]   # never overwrite an archived message
    os.rename(path, dest)


def cmd_daemon(a):
    s = Studio(a.studio)
    s.write_log({"kind": "start", "router": ROUTER, "pid": os.getpid()})
    print("studio router · %s · watching %s" % (ROUTER, s.outbox), flush=True)
    last_health = now()
    handled = 0
    tokens = tokens_from_env()
    if not tokens:
        s.write_log({"kind": "warn", "note": "no STUDIO_TOKENS in the router's environment: every message will show as UNVERIFIED"})
    while True:
        peers = s.peers()
        table = agent_table(a.herdr)
        statuses = {n: t["status"] for n, t in table.items()}
        for n in peers:
            peers[n]["status"] = statuses.get(n, "offline")
        for name in sorted(os.listdir(s.outbox)):
            if name.startswith(".") or not name.endswith(".json"):
                continue
            path = os.path.join(s.outbox, name)
            try:
                with open(path) as f:
                    if json.load(f).get("retry_at", 0) > now():
                        continue
                process(s, a.herdr, path, peers, tokens)
                handled += 1
            except Exception as e:  # one bad file must never take the router down
                s.write_log({"kind": "error", "file": name, "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
                try:
                    os.rename(path, os.path.join(s.held, name))
                except OSError:
                    pass
        if a.once:
            return
        if now() - last_health > 60:
            s.write_log({"kind": "health", "handled": handled, "peers": len(peers), "live": sum(1 for v in statuses.values() if v != "offline")})
            last_health = now()
        time.sleep(float(a.interval))


def cmd_ask(a):
    s = Studio(a.studio)
    msg = {"id": "dry", "t": now(), "from": a.sender, "to": a.to.lstrip("@"), "text": a.text or sys.stdin.read()}
    decision, ev = judge(msg, s.peers())
    print(json.dumps({"decision": decision, "evidence": ev}, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("send", "ask"):
        q = sub.add_parser(name)
        q.add_argument("--studio", required=True)
        q.add_argument("--from", dest="sender", required=True)
        q.add_argument("--to", default="auto")
        q.add_argument("--text")
    d = sub.add_parser("daemon")
    d.add_argument("--studio", required=True)
    d.add_argument("--herdr", default="herdr")
    d.add_argument("--interval", default="0.5")
    d.add_argument("--once", action="store_true", help="process the outbox once and exit (tests)")
    a = p.parse_args()
    {"send": cmd_send, "ask": cmd_ask, "daemon": cmd_daemon}[a.cmd](a)


if __name__ == "__main__":
    main()
