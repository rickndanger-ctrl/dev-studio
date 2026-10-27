# Dev Studio v2 — the board

Modelled on the "Claude Code cross-session" board: you watch JEV route every message
between your agents, and you see who is working on what. No guessing.

## The team (one project, one Herdr workspace, two tabs)

| Tab | What is in it |
|---|---|
| **board** | One full-window pane running `studio board`: the picture. Read-only. |
| **team** | The live terminals: architect (left), main session (top right), workers (right column), router log (bottom). This is where you type. |

Roles follow the picture:

- **Architect** · Opus (Claude) / `CODEX_ARCHITECT_MODEL` (Codex). Sets the rules at launch,
  evaluates contract changes, signs off before merge.
- **Main session** · Sonnet. Builds the feature, syncs schemas, patches dependents.
- **Workers** · Haiku × `WORKERS` (default 2). Small delegated tasks in their own worktrees.
- **JEV message router** · not an LLM session. A supervised Python process that reads every
  peer message, asks Jev (TypeSafe's System One model) three typed questions, and delivers.

`AGENT_KIND=claude` (default) or `codex` picks the CLI; everything else is the same.

## How a message travels (and why you can see it)

1. An agent sends with `studio send --to @backend "UserID is now a UUID"` (or `--to auto`).
   The role briefs forbid any other peer channel, so **every** message crosses the router.
2. The message lands in `<project>/.studio/mail/outbox/` as a small JSON file.
3. The router asks Jev, in one ~150 ms request, over the message plus the live peer list:
   - `which_peer` (Choice over live peers + nobody) — who must get this first?
   - `action` (Choice: forward / broadcast / escalate / drop)
   - `token_leak` (Noul) — is this pasted history, file contents or secrets instead of a note?
4. Code decides: confidence < 0.5 on the peer → escalate to the architect (never guess);
   leak > 0.7 → trim to a one-paragraph note; explicit `--to` is honoured unless Jev says drop
   with p > 0.9. Then `herdr agent prompt <peer> "[from @x] …"` delivers it into the pane.
5. Every step is one JSON line in `.studio/router.log`. The board shows the last decision's
   bars (the picture's "0.98 sharp"), the mailbox counts, and the session log.

Router backends (`ROUTER=`): `jev` (TypeSafe, needs `TYPESAFE_API_KEY`), `clef` (set
`CLEF_URL` to a System One-compatible endpoint; nothing is installed yet), `rules` (no model:
explicit addresses only, everything else goes to the architect). If Jev is unreachable the
router falls back to `rules` for that message and says so in the log.

## Supervision

The router runs in its own Herdr pane under `studio router`, a restart loop that logs every
exit. It never runs unsupervised in the background.
