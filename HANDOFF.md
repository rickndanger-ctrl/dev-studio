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
- Installed on Mini One at `~/dev-studio` via `./install.sh` (solo, 2 builders).
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

## Never verified against real Herdr (check these first)
Everything below was built from Herdr's docs and tested only against `test/mock-herdr`.
Run each command for real and compare its output with what `bin/studio` expects:

1. **`herdr workspace list` JSON**: `find_workspace` looks for any object whose `.label`
   or `.name` matches and reads `.workspace_id // .id`. If this is wrong, a second click
   builds a duplicate team instead of reopening the existing one.
2. **`herdr agent list` JSON**: the dashboard reads `.name // .agent_name // .label // .pane_id`
   and `.status // .state`. If this is wrong, the dashboard shows "waiting for agents to check in…" forever.
3. **`herdr pane split --ratio`**: is the ratio the new pane's share or the old pane's?
   The layouts assume it's the new pane's share. Check that the panes look balanced.
4. **Server start**: `ensure_server` runs `herdr server` in the background if
   `workspace list` fails. It may not be needed.
5. **`herdr pane run`** right after a split: confirm the shell is ready and the
   `claude …` command actually runs.
6. **The terminal opener** (`open_window`): Ghostty → iTerm → Terminal.app. Confirm
   that `herdr` attaches and shows the right workspace.

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
