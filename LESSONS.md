# Lessons

Mistakes Claude made while working with the owner of this studio, and the rule that
prevents each one. **Claude reads this file at the start of every session, before doing
anything else.**

---

## 2026-10-27 — Dismissed a tool the owner actually uses
**What happened:** While fact-checking a tweet about Claude Code cross-session messaging,
Claude called "JEV" filler "mixed in to sound official," because it was not in
Anthropic's docs. JEV is a real, popular model router (jev-router, Clef, Layla), and the
owner uses it in other apps.

**Rule:** "Not in the official docs" does not mean "not real." Before calling something
hype, search the wider web. When the owner names a tool, assume they know it and look it up.

## 2026-10-27 — Shipped a script that crashed on the Mac's bash 3.2
**What happened:** The Dock app failed on first launch with "syntax error near unexpected
token `('". The AppleScript project picker was a heredoc inside `$( ... )`, and its
apostrophes ("Let's go", "AppleScript's") confuse bash 3.2's parser. Tests only ran on
Linux bash 5, so they passed.

**Rule:** Never put heredocs, `case` statements, or comments with apostrophes inside
`$( ... )`. Put AppleScript in its own `.applescript` file. Every script must pass
`/bin/bash -n` on a real Mac (CI does this on `macos-latest`) before it ships.

## 2026-10-27 — Asked too many questions before building
**What happened:** Claude asked several setup questions in a row (terminal app, RAM,
machine split) instead of picking sensible defaults and building.

**Rule:** The owner is the creative lead, not the technician. Pick safe defaults, build,
and say which defaults were chosen. Ask only when the answer truly changes the result.

## 2026-10-27 — Built against Herdr's docs, not Herdr
**What happened:** Two things the docs left vague were wrong on the real `herdr 0.9.3`:
`pane split --ratio` is the share the *original* pane keeps (0.25 left the architect with a
quarter of the screen), and `agent list` reports `agent_status`, with no `name` until the
agent is named with `agent rename` — so the dashboard would have shown "waiting for agents"
forever. The mock copied the docs, so 28 tests passed.

**Rule:** Before trusting a CLI's JSON shape, run the real command once and paste its
actual reply into the mock, with the date. Verify ratio-style arguments with a throwaway
workspace and `pane layout` instead of assuming which side they apply to.

## 2026-10-27 — The window opened on the wrong Mac, and the board measured the wrong terminal
**What happened:** Three real-run surprises. (1) `osascript … do script` put the studio in a
tab of the owner's minimized OMAC window, and "resize front window" shrank *his* window, not
ours. (2) The Herdr TUI remembers the saved SSH machine it showed last
(`~/.local/state/herdr/client`), so every new client opened on Mini 2's workspace, even in the
studio's own `--session`. (3) The dashboard read its size with `$(tput cols)`, whose stdout is
a pipe, so it always got 80x24 and wrapped inside a 44x5 pane.

**Rule:** Size the tab `do script` returns, never "front window". Give the studio's client its
own `XDG_STATE_HOME` so no saved machine can steal the view; keep the server on the normal
environment. Measure a terminal with `stty size </dev/tty`, every frame. And confirm a window
with a screenshot of the window (the pane's text read looked fine while the user would have
seen another Mac).
