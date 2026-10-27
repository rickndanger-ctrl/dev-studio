# Handoff: Dev Studio → Claude Code on Mini One

**Read `LESSONS.md` and `CLAUDE.md` first.** Then this file.

## Who you're working with
The owner is a creative lead, not a programmer. Explain things plainly, take over the
technical work, and pick sensible defaults instead of asking lots of questions. Be honest
about what is and isn't tested.

## What Dev Studio is
A Dock app that opens a ready-made team of Claude Code sessions for a project, inside
[Herdr](https://herdr.dev) (a terminal multiplexer for coding agents):

- 🏛 **Architect**: Opus, plans, delegates, and signs off before merges
- 🔨 **Builders**: Sonnet, each in its own `--worktree`; launched through `jev-claude` when it's installed
- ✨ **Dashboard**: `ui/dashboard.sh`, live agent status and free memory
- 📣 **Messenger**: Haiku, optional (`MESSENGER=1`), off by default to save memory

Sessions talk to each other with Claude Code's cross-session messaging (`/peers`,
SendMessage). See `settings/peer.json` and
https://code.claude.com/docs/en/cross-session-messaging.

**Modes:** `solo` (default: the whole team on one Mac), plus `hq` / `workshop` (split
across two Macs). The owner chose **solo on both Mac minis, run independently**, because
memory is limited.

## Where things stand
- Repo: `rickndanger-ctrl/dev-studio`, branch `studio-v1`, draft PR #1.
- Installed on Mini One at `~/dev-studio` via `./install.sh` (solo; `BUILDERS=3` in studio.conf,
  capped by free memory at launch).
- Matches the "Opus architects · Haiku relays · Sonnet executes" blueprint: the Haiku relay is on
  by default (`MESSENGER=0` to save ~600MB), role briefs follow the split, and the dashboard is a
  board (role cards, session log from `.studio/status.md`, peers footer).
- Found on this Mac: `~/.local/bin/herdr`, `~/.local/bin/claude`, `/opt/homebrew/bin/jq`.
  `jev-claude` is **not** installed.
- **First Dock launch crashed:** `bin/studio: line 308: syntax error near unexpected token '('`.
  Cause: bash 3.2 misparsed an AppleScript heredoc inside `$( )`. **Fixed in commit
  `59963df`** (picker moved to `macos/pick-project.applescript`). Not yet confirmed on
  the Mac.

## Do this first
```bash
cd ~/dev-studio && git pull
for f in bin/studio ui/dashboard.sh lib/common.sh install.sh macos/make-app.sh; do /bin/bash -n "$f" && echo "ok $f"; done
PATH="/bin:$PATH" /bin/bash test/run.sh        # 27 checks, under the Mac's real bash 3.2
studio doctor
```
Then click **Dev Studio** in the Dock (or run `STUDIO_GUI=1 studio up`) and watch
`~/.dev-studio/logs/launch.log`.

## Verified against real Herdr 0.9.3 (2026-10-27, Mini One)
Checked with a throwaway workspace (`studio-probe`), then closed:

1. **`workspace list`**: objects carry `.label` and `.workspace_id` — `find_workspace` is right.
2. **`agent list`**: `{"agent":"claude","agent_status":"blocked","pane_id":"w3:p3",...}`.
   The field is `agent_status` (not `status`), and `name` only appears after
   `herdr agent rename <pane> <name>`, which `start_agent` now does once Herdr detects the
   session. The dashboard and mock use the real shape.
3. **`pane split --ratio`**: the share the ORIGINAL pane keeps. `split()` converts, so the
   layouts still say "new pane's share".
4. **Server start**: the server was already running (the TUI starts it); `ensure_server` is a fallback.
5. **`pane run` right after a split**: works — the command ran and printed its output.
6. **Terminal opener**: `macos/open-terminal.applescript` opens a 220x58 window titled
   "Dev Studio" and sizes the TAB it created. The studio runs in its own Herdr session
   (`herdr --session dev-studio`, `STUDIO_SESSION` in `lib/common.sh`) and the client gets a
   private `XDG_STATE_HOME`, otherwise it opens on the last saved SSH machine (Mini 2).

## Open questions for the owner
- **Clef**: what command starts it? Its free tier runs locally. Set `ROUTER_CMD` in
  `~/.dev-studio/studio.conf` (or change the default in `lib/common.sh`) so builders use it.
- **RAM per Mini**: `MB_PER_AGENT=600` is a guess. Measure a real Claude session's
  memory, for example `ps -o rss= -p <pid>`, and tune `MB_PER_AGENT` / `BUILDERS`.

## Rules for changes
- Bash 3.2 only: no associative arrays, `mapfile`, `${var,,}`, or heredoc/`case`/apostrophes inside `$( )`.
- `shellcheck` every script, `/bin/bash -n` on each one, then `test/run.sh`, before every commit.
- Add a `LESSONS.md` entry for every mistake.
- Push to `studio-v1`. CI (`.github/workflows/test.yml`) runs the tests on macOS bash 3.2.
- Merge to `main` only when the owner confirms the Dock app works.

## File map
| Path | What |
|---|---|
| `bin/studio` | main command: `up`, `dash`, `doctor`, `role`, `stop`; layouts; memory cap |
| `lib/common.sh` | config defaults, colors, `mem_available_mb`, helpers |
| `ui/dashboard.sh` | live dashboard pane |
| `install.sh` | per-Mac installer |
| `macos/make-app.sh` | builds `~/Applications/Dev Studio.app` and pins it to the Dock |
| `macos/pick-project.applescript` | the "which project?" dialog |
| `roles/*.md` | each agent's role brief (`<project>` gets filled in; LESSONS.md is appended) |
| `settings/peer.json` | cross-session inbound policy |
| `test/run.sh`, `test/mock-herdr` | end-to-end tests against a mock Herdr |
| `~/.dev-studio/studio.conf` | per-Mac settings (role, builders, models, router, sounds) |
