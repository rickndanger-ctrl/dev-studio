# Handoff: Dev Studio → the next Claude Code session

**Read `LESSONS.md`, `CLAUDE.md` and `DESIGN.md` first.** Then this file.

## Who you're working with
The owner is a creative lead, not a programmer. Explain things plainly, take over the
technical work, and pick sensible defaults instead of asking lots of questions. Be honest
about what is and isn't tested.

## What Dev Studio is (v2, 2026-10-27)
A Dock app that opens a team of coding-agent sessions for a project inside Herdr, with a
**board** that shows JEV routing every message between them (modelled on the
"Claude Code cross-session" picture the owner gave us). See `DESIGN.md` for the shape.

- Tab **board**: `studio board` — architect card, main session, JEV decision bars, worker
  cards, peer mailbox, session log. Read-only. Python, `ui/board.py`.
- Tab **team**: the terminals: architect (Opus), main (Sonnet), workers (Haiku ×N), and the
  router pane (`studio router`, a restart loop around `router/router.py`).
- `studio up <project> --codex` builds the same team with Codex (`codex -m <model>`, role
  brief as the first prompt). `AGENT_KIND` and models live in `~/.dev-studio/studio.conf`;
  the team actually launched for a project is in `<project>/.studio/team.json`.
- Agents talk only through `studio send <me> @peer|auto "note"`. The router asks Jev
  (TypeSafe, `TYPESAFE_API_KEY` in `~/.zshrc`) which peer / what action / token leak, gates
  on confidence, delivers with `herdr agent prompt`, and logs every step to
  `<project>/.studio/router.log`. `ROUTER=jev|clef|rules`; Clef is a slot (`CLEF_URL`)
  because nothing called Clef is installed and the owner has not said what starts it.
- The sender of a message is verified against the Herdr pane it was sent from
  (`HERDR_PANE_ID`); anything else is delivered marked UNVERIFIED.

## Where things stand
- Repo `rickndanger-ctrl/dev-studio`, branch `studio-v1`, draft PR #1. Installed on Mini One
  at `~/dev-studio`; the Dock app runs `~/dev-studio/bin/studio` directly.
- The studio runs in its own Herdr session (`herdr --session dev-studio`); the TUI client
  gets a private `XDG_STATE_HOME` so it cannot reopen on the owner's saved SSH machine.
- Verified for real on Mini One: a Claude team on this repo, a message sent by a worker,
  judged by Jev (0.91 sharp), delivered to the main session, which then replied through the
  same path; the board screenshot matches the picture. Codex: see the end of the launch log.
- `test/run.sh` (51 checks) runs the studio against `test/mock-herdr` and a fake TypeSafe
  endpoint (`test/fake-typesafe.py`). CI runs it on macOS bash 3.2.

## Added 2026-10-27 (afternoon)
- **Live feed on the board**: `agent_now()` reads each agent's last meaningful terminal line
  (`herdr agent read`), state changes are noted between frames, and the feed merges them with
  router rows. "talking to" on the architect card is its last route.
- **launchd supervision** (`dev.studio.herdr`, `dev.studio.web`, KeepAlive). Reason: a server
  started from the Dock app's launcher died when the launcher exited. `STUDIO_SUPERVISOR=nohup`
  is for tests only.
- **Rebuild on a dead space**: `team_is_live` checks the architect is a named agent; a Herdr
  restart restores panes and even resumes Claude, but names are lost, so the team is rebuilt.
- **Web view** `ui/web.py`, port 8790, loopback + 100.64.0.0/10 only, Host checked. Mini 1
  100.64.0.1, Mini 2 100.64.0.2; the owner's Dell is `linux-laptop` on the tailnet (Linux).
- Both Minis run Herdr 0.9.5. OMAC theme on both: obsidian + emerald glass. The OMAC tile
  opener falls back to a Terminal window when OMAC refuses ("resume the page").
- A Python 3.15 DeprecationWarning once scrolled the board; the board silences warnings and
  sends stderr to `.studio/board.err`. Mini 2 runs Xcode's Python 3.9: keep the code 3.9-safe.

## The board app (2026-10-27, evening)
- `ui/web.py` serves `ui/web/{index.html,app.css,app.js}` and `/api/state` (+ `/api/agent?name=`).
  A poller thread refreshes the model every second (agents, "now" lines, router log, events).
- `app.js` lays the team out in four layers and draws SVG wires agent ↔ JEV (the architect's
  swings out to the left so it does not cross the main session). Pulses are SMIL
  `animateMotion` circles on each hop; drawn synchronously, never via rAF (hidden tabs stall it).
- The Dock opens the board in Chrome app mode (`open_board_window`): start Chrome via
  LaunchServices first, then hand `--app=URL` to the running instance. `BOARD_WINDOW=none` turns it off.
- `test/run.sh` starts the web app against the mock on port 8797 and checks the API.

## Known limits / next ideas
- One team per project at a time (Claude or Codex): both would share `<project>/.studio`.
- The board is a terminal pane. The owner asked whether it "has to be Herdr": the plumbing
  (pane states, `agent prompt` delivery) does; the board itself could become a browser page.
- Jev's drop sensitivity is mild: polite acks get forwarded. Tune `DROP_P`/questions in
  `router/router.py` with real traffic before changing anything else.
- Clef: ask the owner what command starts it, then set `CLEF_URL` (System One-compatible).

## Do this first
```bash
cd ~/dev-studio && git pull
for f in bin/studio lib/common.sh install.sh macos/make-app.sh; do /bin/bash -n "$f" && echo "ok $f"; done
shellcheck -s bash bin/studio lib/common.sh test/run.sh test/mock-herdr
PATH="/bin:$PATH" /bin/bash test/run.sh
studio doctor
```

## Rules for changes
- Bash 3.2 only: no associative arrays, `mapfile`, `${var,,}`, or heredoc/`case`/apostrophes inside `$( )`.
- Python: standard library only (the Mac's own python3). Keep emoji out of the board (it pads by code point).
- `shellcheck` every script, `/bin/bash -n` on each one, then `test/run.sh`, before every commit —
  and gate the commit on the test's exit code, never on grep's.
- Add a `LESSONS.md` entry for every mistake. Push to `studio-v1`. Merge to `main` only when the
  owner confirms the Dock app works.

## File map
| Path | What |
|---|---|
| `bin/studio` | `up`, `board`, `router`, `send`, `ask`, `doctor`, `stop`; the layout; memory cap |
| `lib/common.sh` | config defaults, `hd` (Herdr in the studio's session), helpers |
| `router/router.py` | the JEV message router: mailbox → judgment → delivery → log |
| `ui/board.py` | the board |
| `roles/{architect,main,worker}.md` | role briefs (`<project>`, `<me>` get filled in; LESSONS.md appended) |
| `settings/peer.json` | Claude cross-session inbound policy |
| `macos/open-terminal.applescript` | the studio window (sized by the tab's tty) |
| `macos/pick-project.applescript` | the "which project?" dialog |
| `test/run.sh`, `test/mock-herdr`, `test/fake-typesafe.py` | end-to-end tests |
| `~/.dev-studio/studio.conf` | per-Mac settings (agent kind, router, workers, models, sounds) |
| `<project>/.studio/` | mailbox, `peers.json`, `team.json`, `router.log` (kept out of git) |
