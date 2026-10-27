# ✦ Dev Studio

**Your AI dev team, one click away.** Click an icon in your Dock and a terminal opens with a
full team of Claude Code sessions already running, named, and able to message each other.

![Dev Studio](assets/icon-solo.png)

Each Mac runs its own complete, **lean** team:

| Pane | Model | Job |
|---|---|---|
| 🏛 **Architect** | Opus | plans, assigns work, approves before anything merges |
| 🔨 **Builders** (2 by default) | Sonnet, or routed by Jev | write and test code, each in its own copy of the project |
| ✨ **Studio dashboard** | none (a tiny script) | who's working, who needs you, memory left |

**Built for Macs with limited memory.** Before starting, the Studio checks how much memory
is free and starts fewer builders if they wouldn't fit, so the Mac never grinds to a halt.
The messenger pane is off by default to save memory (`MESSENGER=1` turns it on).

Built on [Herdr](https://herdr.dev), which keeps every agent running in the background,
restores the layout after a restart, and shows who's working, done, or waiting on you.

## Install (once per Mac)

```bash
git clone https://github.com/rickndanger-ctrl/dev-studio.git ~/dev-studio
cd ~/dev-studio
./install.sh
```

Run it on each Mini. It installs anything missing (Herdr, jq), saves your settings, adds a
`studio` command, and puts the **Dev Studio** icon in your Dock.

<details><summary>Two-Mac mode (optional)</summary>

One Mac plans, the other builds: `./install.sh hq` on one and `./install.sh workshop` on the other.
</details>

## Every day

1. Click **Dev Studio** in the Dock.
2. Pick a project from the list.
3. Talk to the architect. It hands work to the builders, and they report back.

Click the icon again later and you're back in the same team, still running.

### The dashboard
| Light | Meaning |
|---|---|
| ⠋ spinning | working on it |
| 🔴 **NEEDS YOU** | waiting for your answer (you'll hear a ping) |
| ✅ | finished, take a look (you'll hear a chime) |
| 🟢 | ready for work |

## Commands

| Command | What it does |
|---|---|
| `studio up [project]` | open the team for a project |
| `studio doctor` | check everything is installed |
| `studio role solo\|hq\|workshop` | switch this Mac's role |
| `studio stop` | stop every agent |

Settings live in `~/.dev-studio/studio.conf`:

| Setting | Default | Meaning |
|---|---|---|
| `BUILDERS` | 2 | builders to start (capped automatically by free memory) |
| `MESSENGER` | 0 | 1 adds a Haiku messenger pane |
| `MB_PER_AGENT` | 600 | memory budgeted per Claude session |
| `MEM_RESERVE_MB` | 1536 | memory always left for macOS and your apps |
| `ROUTER_CMD` | jev-claude | the router builders launch through, when installed |
| `SOUNDS` | 1 | pings and chimes |

## How the safety rules work

- Same-Mac messages are delivered straight away (`crossSessionInbound: accept`).
- Messages from another Mac need your approval (`isolatePeerMachines: true`).
- A message from another agent is never treated as your approval, and each agent still asks
  before risky actions.
- Each builder works in its own git worktree, so builders never overwrite each other.
- Only the architect signs off on merges to `main`.

In two-Mac mode, for the Macs' Claude sessions to message each other, both need the same claude.ai
sign-in and [Remote Control](https://code.claude.com/docs/en/cross-session-messaging).

## Model routing (optional)

If `jev-claude` ([jev-router](https://github.com/gargpratyush/jev-router)) is installed,
builders launch through it, and Jev picks Haiku, Sonnet, or Opus for each turn. To use a
different router, set `ROUTER_CMD` in `~/.dev-studio/studio.conf`. The architect is pinned
to Opus and the messenger to Haiku.

## Lessons

[`LESSONS.md`](LESSONS.md) is added to every agent's role brief, so past mistakes aren't repeated.

## Development

```bash
test/run.sh               # end-to-end test against a mock Herdr
python3 assets/make_icons.py
```
