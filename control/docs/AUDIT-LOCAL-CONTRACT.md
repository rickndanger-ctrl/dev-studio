# Local-first auditor (architect, job audit-local)

`bin/re-audit` asks a model for a verdict JSON on each changed studio team and on the laptop's local-model task.
Today every ask is a paid Haiku call. After this job: the laptop's local model answers first, Haiku only as fallback and
as a 1-in-10 quality sample, and an unchanged team costs nothing.

## `lib/audit_review.py` (standard library; runners injected so tests never call a model)
`review(prompt, allowed, keys, local, haiku, rng=random.random, sample=0.1, stats=None) -> (verdict, source)`
- `local(prompt) -> str` and `haiku(prompt) -> dict` are callables; `allowed` is the set of valid `status` values,
  `keys` the required keys.
- Local first: parse the first `{...}` in the text; valid = a JSON object with every key in `keys` and `status` in
  `allowed`. Valid -> `source = "local"`. Exception, timeout or invalid -> call `haiku`, `source = "haiku-fallback"`
  (and the local failure reason goes to stats).
- Sample: after a valid local verdict, with probability `sample` also call `haiku`; `source = "local+sample"`;
  record agreement = same `status`. The verdict returned is Haiku's when they disagree (safer), else the local one.
- The verdict dict keeps exactly today's keys (no new fields); where it came from lives only in `source` and stats.
- `stats` (a dict, saved by the caller) counts per hour `"YYYY-MM-DDTHH"`: `passes`, `skipped`, `local`, `haiku`,
  `fallback`, `sampled`, `agreed`, `local_seconds`, `local_fail` (reason -> count). Helpers: `bump(stats, key, n=1)`.

## `bin/re-audit`
- Both asks (laptop task and studio teams) go through `review`. `local` = `localq_models.run("lap", prompt,
  max_chars=1200, timeout=audit_local_timeout)` after `localq_gate.check("lap")` (once per pass; a failed gate means Haiku for that
  pass, counted as fallback with reason `gate: <reason>`). The timeout is the config key `audit_local_timeout`
  (default 150 s, so a draft sharing the model does not push a normal ask to Haiku); each ask logs
  `local ask ok|failed <seconds>s (limit <n>s)` in `audit.log`. The Haiku call is today's `claude -p` call.
- Skip: a team whose signature (agent names + states, that team's route times, that team's commits) did not change
  costs no model call; counted as `skipped`. A team must not see another team's commits in its signature.
- Stats live in `<state_dir>/audit-stats.json` (one file, atomic writes).

## Reporting
- `localq report` gains one block "auditor" for the window: passes, skipped, local, Haiku (fallback + samples),
  agreement on samples (`agreed/sampled`), mean local seconds.
- The viewer's Haiku node tooltip adds one line: `today: N local, M Haiku (K fallback), S skipped, agreement A/B`.
- The verdict JSON in `latest.json` and everything the viewer and the glasses page read stay identical.

## Quality round (job audit-quality)

Run 1 of `bin/audit-compare` showed the local model answering with valid states but misjudging a waiting, all-done team as
`progressing` (4 of 5 teams agreed). The fix is to compute the facts in Python and let the model only name the state.

- **Facts** (`lib/audit_facts.py`, `facts(snap, now)`): agents by state (`working` / `idle` / `done` / `other`: count and up to 3
  names, each sanitised to 16 characters of letters, digits, `.`, `_`, `-`, space), `minutes_since_route`, `minutes_since_commit`
  (null when unknown), `routes_last_15_min`, `commits_last_15_min`, `repeated_message` (the same sender sent the same text twice),
  `commits_touch_task_files` (`yes` / `no` / `unknown`: a recent commit path matches a file named in the whole architect task
  text; `unknown` without commits, paths or file names) and the last 3 architect task lines (80 characters each in the prompt).
  A time up to 5 minutes ahead of now (clock skew) counts as now; further ahead it is unknown and ignored.
  `bin/studio-snap.py` commits carry `t` (commit time) and `paths` (up to 30 file names); the signature still uses only hashes.
- **Status is derived in Python** (`derive_status`): no agent working -> `idle`; else `repeated_message` -> `looping`; else a
  route or commit in the last 15 min -> `progressing`; else `stalled`. `needs_claude` is true for stalled, looping, off-scope.
- **The one model judgement is off-scope**, asked only when an agent is working and `commits_touch_task_files` is `no`.
  `plan(label, snap)` returns the facts, the state, `judge`, and two prompts:
  - `local_prompt`: the state is already decided; the local model writes a summary and a concern (and may answer `off-scope` when
    `judge`). `fix_reply` then pins the status to the code's state (or off-scope when allowed) and sets `needs_claude`; a reply
    that is not a JSON object with a summary is left alone, so it fails validation and falls back to Haiku.
  - `haiku_prompt`: the rules prompt (the order of the rules, two examples, the reply shape); Haiku states its own status. The
    sampled check compares that status with the final one, so agreement measures the rules. `audit_review.review(...,
    haiku_prompt=)` sends Haiku this prompt.
  When `judge` is true both prompts also carry `commit_evidence`: the 3 newest commits' subject (60 characters) and up to 3 paths.
  The Haiku rules put the off-scope check first, then idle, looping, progressing, stalled (Python's own order is unchanged).
  The local prompt says to state only what the facts show (no guesses) in under 140 characters; `fix_reply` cuts the summary there.
  The fit keeps the task lines and the evidence longest (task lines shortened to 40 characters first, then names, paths, older
  tasks); if a task line and the evidence do not both survive in both prompts, off-scope is not offered (`judge` false: the status
  stays the code's, the model writes the summary only, the Haiku rules leave the off-scope check out).
  Both prompts stay under 1500 characters: labels cut to 40, task lines shortened then dropped, then the agent names, then the
  agents block (tested with 20 long Unicode names).
- **Synonyms**: `audit_review.normalize(verdict, allowed)` maps `working` to `progressing`, `waiting` to `idle`, `stuck` to
  `stalled` and `off_scope` to `off-scope` on both the local and the Haiku verdict, before the status check and the
  agreement count. A status that is already allowed is kept, so the laptop task keeps its own `stuck`.
- **Config** `audit_sample` (default `0.1`, accepted 0 to 1, anything else falls back to 0.1): the share of valid local verdicts
  that Haiku also checks.
- **Logging**: every sampled pair is appended to `state/audit-samples.jsonl` (time, team key, local verdict, Haiku verdict,
  prompt characters) through the `on_sample(local, haiku)` callback of `review`.
- **`bin/audit-compare`** prints, per team, the prompt characters and the seconds each side took, then
  `agreement: agreed/comparable comparable, N with an error`. A pair where either side failed or returned an invalid reply is
  `NOT COMPARED`.

## Confirming alerts (job audit-confirm)

Sampling at 10-34% never sees most false alerts, so a local verdict that would alert is checked before it does.

- `audit_review.review(..., confirm=(statuses), on_confirm=)`: a valid local verdict whose status is in `confirm` and that was not
  sampled is checked by Haiku with the rules prompt (`haiku_prompt`). Source `confirm`. Haiku's status wins when they differ
  (counted as `overturned`); when they match the local verdict stands. A failing Haiku call raises, so an unconfirmed alert is never
  reported and the team is asked again on the next pass. A sampled verdict already had its Haiku check, and a fallback verdict is
  already Haiku's.
- Every Haiku reply (confirm, sample or fallback) is held to the local shape: an object with every key and an allowed status after
  synonym normalising. A malformed confirm is a failed confirm (counted as `confirm_failed`, the ask raises and is retried next
  pass), never a confirmation; a malformed or failed sampled reply is counted as `sample_failed` only (not as sampled or compared) and a calm local verdict stands, but on an alert status it is a failed confirm (`confirm_failed` too, the ask raises): an alert goes out only with a valid Haiku status behind it, from a sample or a confirm; a malformed
  fallback reply raises.
- Aging: an unchanged team that was `progressing` with agents still working is asked again once `STALL_AFTER` (15 min) has passed
  since both its last change and its last ask; the new verdict comes from the current facts and goes through the same confirm. If
  Haiku says it is not stalled, its status stands and the team is not asked again until the snapshot changes or another
  `STALL_AFTER` passes. (The old rule that turned an unchanged team into `stalled` without a model call is gone.)
- `bin/re-audit` confirms studio verdicts in `stalled`, `looping` and `off-scope` (not the laptop task).
- Stats per hour: `confirmed` (Haiku confirm asks; also counted in `haiku`), `overturned` and `confirm_failed`. `localq report` adds
  `confirms C (O overturned)` to the auditor line when there were any, and a local verdict that was overturned or whose confirm failed is not counted as
  `local used`.
- `state/audit-samples.jsonl` rows gain `kind`: `sample` or `confirm`.
- `state/audit.log` verdict lines name the source instead of always `haiku`: `<time> <source> <team key>: {json}` with source
  `local`, `local+sample`, `confirm` or `haiku-fallback` (the laptop line is `<time> <source>: {json}`).

## Off-scope is Haiku's call (job audit-offscope)

The local model called a commit for an earlier task off-scope just because it touched none of the newest tasks' files.

- `audit_facts.plan` returns `judge` always `False`: the local prompt has the state fixed and carries no commit evidence and no
  off-scope wording; the model writes the summary only.
- `plan["offscope"]` is true when off-scope is possible: an agent is working, `commits_touch_task_files` is `no`, and a task line and
  the commit evidence survive the fit in the rules prompt. Then `bin/re-audit` asks Haiku directly with the rules prompt
  (`audit_review.ask_haiku_only`, source `haiku-offscope`, the local model is not asked at all) and Haiku's status is final;
  `needs_claude` is true for stalled, looping and off-scope. A failing or malformed reply raises and the team is retried.
- Stats: `offscope` (asks; also counted in `haiku`). `localq report` adds `off-scope checks N`. Every other team is unchanged.

## Owners
| Part | Owner |
|---|---|
| `lib/audit_review.py` + `test/test_audit_review.py` | worker-1 (unit tests only) |
| `bin/re-audit` wiring, `localq report` block, viewer tooltip, tests | main |
| skip-unchanged fix (per-team signature, today a Codex team that is idle is re-asked every pass) in `bin/re-audit` / `bin/studio-snap.py`, tests, `test/ci.sh` on every commit, the one-hour measurement after the restart | worker-2 |
| this file, merges, restarting `re-audit.service` after green CI | architect |
