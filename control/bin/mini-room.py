import sys

if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
    print('Print the current Room state and findings of the audited app as JSON. Takes the audit block as JSON.')
    sys.exit(0)

import json, os, glob, subprocess, time

# This script runs on a Mini, which does not have the laptop's config. re-audit sends the audit block
# ({app, mac_data_dir, project}) as its one argument.
if len(sys.argv) < 2:
    print(json.dumps({"error": "no audit target given"}))
    sys.exit(0)
target = json.loads(sys.argv[1])
if not target.get("mac_data_dir") or not target.get("project"):
    print(json.dumps({"error": "no audit target configured"}))
    sys.exit(0)
PROJECT = target["project"]


def own_pids():
    """This process and its ancestors. Their command lines carry the audit target (and so the patterns): never a match."""
    pids, pid = {os.getpid()}, os.getpid()
    for _ in range(8):
        try:
            pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout)
        except ValueError:
            break
        if pid <= 1:
            break
        pids.add(pid)
    return pids


def app_running(patterns):
    """Same probe as re-audit: pgrep -x by name, else pgrep -f; None when the list is empty or pgrep is missing."""
    if not patterns:
        return None
    try:
        skip = own_pids()
        for p in patterns:
            for flag in ("-x", "-f"):
                found = subprocess.run(["pgrep", flag, p], capture_output=True, text=True).stdout.split()
                if {int(x) for x in found} - skip:
                    return True
        return False
    except OSError:
        return None


d = os.path.expanduser(target["mac_data_dir"])
projects = json.load(open(d + "/projects.json"))
matches = [p for p in projects if p["name"] == PROJECT]
if not matches:
    print(json.dumps({"error": f"no project named {PROJECT}"}))
    sys.exit(0)
pid = matches[0]["id"]
r = json.load(open(f"{d}/rooms/{pid}.json"))
out = {"tasks": [], "app_running": app_running(target.get("process"))}
for t in r.get("tasks", []):
    item = {"title": t["title"], "status": t.get("status"), "failed": t.get("failed", False), "note": t.get("note", "")}
    if t.get("threadId"):
        f = f"{d}/conversations/{t['threadId']}.json"
        m = json.load(open(f))["messages"][-1]
        calls = [e for e in m.get("events", []) if e.get("type") == "tool_use"]
        item.update(state=m.get("state"), steps=len(calls), idle=int(time.time() - os.path.getmtime(f)),
                    last=(calls[-1].get("name", "") + " " + str(calls[-1].get("output", ""))[:80]) if calls else "",
                    status_line=[str(e.get("output", ""))[:120] for e in m.get("events", []) if e.get("type") == "status"][-1:])
    out["tasks"].append(item)
ws = matches[0]["workspace"]
out["findings"] = {}
for f in sorted(glob.glob(ws + "/findings/*")):
    out["findings"][os.path.basename(f)] = open(f, errors="replace").read()[:40000]
print(json.dumps(out))
