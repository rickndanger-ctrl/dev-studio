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
