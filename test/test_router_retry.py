"""A broadcast held for one blocked peer must not re-prompt the peers that already have it,
and its retries reuse the first decision instead of asking the router model again.
Run: python3 test/test_router_retry.py"""
import importlib.util, json, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("router", os.path.join(ROOT, "router", "router.py"))
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)

prompts, judged = [], []
blocked = {"t-worker-2"}


def fake_deliver(herdr, peer, sender, text, verified=True):
    prompts.append(peer)
    return ("held", "agent_blocked") if peer in blocked else ("delivered", "")


def fake_judge(msg, peers, router=None):
    judged.append(msg["id"])
    return {"action": "broadcast", "to": ["t-architect", "t-worker-1", "t-worker-2"], "note": msg["text"]}, {"router": "fake"}


router.deliver, router.judge = fake_deliver, fake_judge
d = tempfile.mkdtemp()
s = router.Studio(d)
peers = {n: {"role": r} for n, r in (("t-main", "main"), ("t-architect", "architect"),
                                      ("t-worker-1", "worker"), ("t-worker-2", "worker"))}
path = os.path.join(s.outbox, "1-m1.json")
with open(path, "w") as f:
    json.dump({"id": "m1", "t": 1, "from": "t-main", "to": "auto", "text": "schema changed"}, f)

router.process(s, None, path, peers, {})
assert prompts == ["t-architect", "t-worker-1", "t-worker-2"], prompts
assert os.path.exists(path), "held message stays in the outbox"

# A sender writing its own recipients into the outbox file has no effect.
with open(path) as f:
    msg = json.load(f)
msg["done"] = []
msg["decision"] = {"action": "forward", "to": ["t-architect"], "note": "x"}
with open(path, "w") as f:
    json.dump(msg, f)

prompts.clear()
router.process(s, None, path, peers, {})
assert prompts == ["t-worker-2"], "retry re-prompted delivered peers: %s" % prompts
assert judged == ["m1"], "retry asked the router model again: %s" % judged

blocked.clear()
prompts.clear()
router.process(s, None, path, peers, {})
assert prompts == ["t-worker-2"], prompts
assert not os.path.exists(path), "delivered message leaves the outbox"
assert os.listdir(s.delivered), "archived as delivered"
assert not os.listdir(s.retry), "retry notes cleaned up"
print("ok   broadcast retry prompts only the held peer (3 checks)")
