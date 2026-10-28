# Two teams per machine: contract (architect, job two-teams)

A studio machine can run several studio teams in one Herdr session, e.g. a Claude team (`studio-architect`,
`studio-main`, `studio-worker-N`) and a Codex team (`studio-codex-architect`, ...). The control room shows and audits
each team on its own. Read only on the studio machines: never message, rename or restart any agent there.

## `lib/studio_teams.py` (standard library, pure functions)
- `ROLE_RX = r"(?:^|-)(architect|main|messenger|worker-\d+)$"` (the role is a whole name part: `studio-domain` is not a team) (the one copy; the viewer, studio-snap and re-audit import it).
- `team_of(name) -> (team, role) | None`: `team` is everything before the role with the trailing `-` removed
  (`"studio-codex-worker-1" -> ("studio-codex", "worker-1")`, `"architect" -> ("", "architect")`); None when the
  name has no role suffix (not a studio agent).
- `group(agents) -> list[team]`, each `{"team", "kind", "idle", "agents"}`:
  `kind` = the agents' `agent` field when they all agree (`"claude"`, `"codex"`), else `"mixed"`;
  `idle` = no agent has status `working` or `blocked` (status from `agent_status`, or `status` when that is missing);
  `agents` in role order architect, main, messenger, worker-1.. (by number). Teams sorted by name.
- `team_of_route(row) -> str | None`: the team of the row's `from`, else of its `to`; None when neither parses.

## Viewer state
`minis[mid].teams = [{"team", "kind", "idle", "agents": [...], "routes": [last 30 route rows of that team],
"label": "<team or 'studio'> · <Claude|Codex|Mixed>"}]`. `minis[mid].agents` stays (all agents) for older readers.
Route rows come from every `.studio/router.log` under the machine's `project` and `studio_dirs` (and their
`.studio/worktrees/*/`), each row assigned to a team with `team_of_route`; a team with no rows shows "no routes yet".

## Drawing (viewer/index.html)
Inside a machine panel the teams sit side by side, each its own cluster with its own JEV hub and label and its own
routing band; no label may overlap another (checked on the screenshots). A team whose `idle` is true collapses to one
line: `<label>: idle (<n> agents)`. One team on a machine looks like today.

## Auditor
`bin/studio-snap.py` returns one snapshot per team; `bin/re-audit` keys verdicts `"<mid>/<team>"` with label
`"<machine label> · <team label>"`, and asks Haiku once per changed team snapshot (same rules as today per studio).

## Owners
| Part | Owner |
|---|---|
| `lib/studio_teams.py` + `test/test_studio_teams.py` | worker-1 |
| `viewer/server.py`, `viewer/index.html`, `test/demo_server.py` (two teams on one Mini, fake names), screenshots | main |
| `bin/studio-snap.py`, `bin/re-audit`, their tests, `docs/` text, `test/ci.sh` on every commit | worker-2 |
| this file, merges | architect |

Fixtures use fake names and paths only (no real user names or hosts). The real `herdr agent list` shape, read on a
studio machine on 2026-10-27: `{"result": {"agents": [{"name", "agent": "claude"|"codex", "agent_status":
"done"|"idle"|"working"|..., "cwd", "pane_id", "workspace"}]}}`.
