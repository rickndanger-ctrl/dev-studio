# localq contract (architect, branches localq, local-more)

The shared shapes for the local-model work queue. Each part is owned by one teammate; change this file
only through the architect.

## Parts and owners
| Part | File | Owner |
|---|---|---|
| Gate: approvals and pre-run checks | `lib/localq_gate.py` + tests | worker-1 |
| Model runners | `lib/localq_models.py` + fake model server + tests | worker-2 |
| CLI, queue, results, review, stats | `bin/localq` + tests | main; `draft` shortcut: worker-1 |
| Guide | `docs/LOCAL-WORK.md` | main |
| Viewer "LOCAL WORK" line, bar widget share | `viewer/`, `bin/barstat` | worker-1 (after the gate) |

All Python is standard library only. No test ever contacts a Mini or a real model server.

## Machine config (optional fields, with defaults)
```json
"local_model": {"kind": "openai", "url": "http://127.0.0.1:18081/v1", "model": ""}
"local_model": {"kind": "cli", "path": "~/Fake Tools/runner", "args": ["-p"], "model": "Fake-Model-9B"}
"local_block_apps": ["Example App"]
"local_min_free_pct": 15
```
The `hub` machine defaults to the `openai` entry above; a `studio` machine has no runner until configured. `local_block_apps` is a top-level list; when omitted, the gate uses the defaults specified in the control config contract.
`model: ""` means "whatever the server serves". For `cli`, `model` is an optional label recorded in the job (default: the binary's file name).

## Job file (`localq add <machine> <job.json>`)
```json
{"id": "slug-1", "kind": "draft|summarize|describe|testcases|review-notes",
 "prompt": "...", "inputs": "small text", "max_chars": 4000,
 "output": "state/localq/out/slug-1.md", "reviewer": "haiku"}
```
Refused: `len(prompt) + len(inputs)` over 6144 characters, an unknown kind, an id already used, or an
`output` outside `<state_dir>/localq/out/`.

## State (all under `cfg.state_dir()`, gitignored)
- `localq/jobs/<machine>/<id>.json`: the job plus
  `status: queued|running|blocked|done|failed`, `reason`, `queued_at`, `started_at`, `finished_at`,
  `seconds`, `chars_in`, `chars_out`, `model`, and `review: {verdict: accept|fix|reject, by, note, at}`.
- `localq/out/<id>.*`: the result text.
- `localq/approvals.json`: `{"m1": {"who": "...", "at": <unix>, "until": <unix>}}`.
- `localq/stats.json` (was `localq-stats.json` before job local-more-2; moved so a sandbox granted only `localq/` can
  record reviews. The first write moves the old file once; readers fall back to the old path while it exists): `{"days": {"YYYY-MM-DD": {"<machine>": {"jobs", "chars_in", "chars_out", "seconds",
  "accepted", "fixed", "rejected"}}}, "totals": {same keys}}`.

## Gate (`lib/localq_gate.py`)
- `allow(mid, hours, who)`, `deny(mid)`, `approval(mid) -> dict | None` (None when missing or expired).
- `check(mid) -> (ok: bool, reason: str)`, in this order, stopping at the first failure:
  1. Minis only: a valid approval window.
  2. Minis only: no process listed in `local_block_apps` is running (`pgrep -x`).
  3. The model is reachable (openai: `GET <url>/models` answers; cli: the configured binary exists).
  4. Memory meets the configured threshold, or the loaded hub model meets its available-memory and swap checks.
- Mini checks go over `cfg.routes(mid, ...)`, read-only. The gate never opens or quits an app.
- `LOCALQ_FAKE_GATE=<file.json>` (`{"m1": [false, "reason"], "lap": [true, ""]}`) replaces `check()` in tests.

## Runners (`lib/localq_models.py`)
- `run(mid, prompt, max_chars, timeout=300) -> {"text", "seconds", "model"}`; raises `RunnerError(reason)`.
- openai: `POST <url>/chat/completions`, one user message, `max_tokens` from `max_chars`, temperature 0.2.
- cli: the prompt goes over SSH on stdin to the configured `path` and `args`, through `cfg.routes`. The prompt is never on the command line.
- `model` is recorded as a short name, so no home path lands in results: for `cli` the `local_model.model` label when set, else the binary's file name without the path; for `openai` the model id.

## CLI (`bin/localq`)
`add`, `allow m1|m2 <hours>`, `deny m1|m2`, `run <machine>` (gate first; on failure every queued job gets
`status: blocked` and the reason, and nothing runs; then jobs one at a time), `results [--pending]`,
`review <id> accept|fix|reject [note]`, `stats`. `run` on a Mini without a valid approval must never reach
the runner. That is tested.

## Viewer
`lap.local` and `minis[mid].local` = `{"queued", "running", "blocked": reason or "", "done_today"}`;
the bar widget adds `local_today` (jobs and characters done locally today).

## Draft-first shortcut (job local-more)

`localq draft <kind> <input-file> [--max-chars N]` queues one job on `lap`, runs only that job through the gate,
and prints the result text on stdout (the job id on stderr). The job is counted in stats like any other and lands
in `localq results --pending` for review. Exit 0 on done; exit 2 with the gate's reason when blocked; exit 1 on a
refused job or a failed run. The laptop only: a Mini still needs `add` + `run` and an approval.

Kinds for `draft` (each has a built-in prompt in `bin/localq`, so the input file is the only thing a caller writes):

| Kind | Input file holds | Output |
|---|---|---|
| `commit` | the task line + `git diff --stat` | one subject line under 72 chars, blank line, 1-3 body lines |
| `testcases` | the task line + the feature's interface (names, flags, files) | a numbered list of test cases, one line each |
| `review-notes` | the reviewer's verdict lines | a short review summary for the architect |
| `changelog` | the task line + the commit subjects | 1-3 changelog lines, user-facing |
| `status` | the teammate's name + what they are on | one `.studio/status.md` line |

`commit`, `changelog` and `status` are new kinds; `testcases` and `review-notes` already exist. The 6144-character
limit applies to prompt + input as before. Reviewer defaults to `codex`.

Owners for this job: `bin/localq` `draft` + kind prompts + tests → worker-1. `docs/roles/*.md` and the
"Draft first" section of `docs/LOCAL-WORK.md` → worker-2. This file → architect.

## Briefs, docstrings and the daily report (job local-more-2)

Two more `draft` kinds (built-in prompts in `bin/localq`, reviewer `codex`):

| Kind | Input file holds | Output |
|---|---|---|
| `brief` | the architect's bullet notes: who, goal, files, checks | one worker brief in the studio's format: a first line `Task: ...`, then goal, files to touch, rules (bash 3.2 safe, no heredoc or apostrophes inside `$( )`, tests by who you are), how to check it is done, and the closing `commit, reply with the hash`. Plain text, under 1200 characters, no Markdown headings |
| `docstrings` | one function or file of code | the same code with docstrings and short comments added; code lines unchanged. The worker diffs the result against the original and keeps only comment and docstring lines |

`localq report [--since <HH:MM | YYYY-MM-DD HH:MM>] [--json]` prints one screen for today (or from `--since`, which makes it a
per-job report): jobs by kind and status, the verdict mix (accept / fix / reject, and how many are still unreviewed),
the output share (the same estimate as `stats`, counting only jobs and mail inside the window), and the five slowest
jobs (id, kind, seconds). Read-only; at most 24 lines of text. The logic lives in `lib/localq_report.py`
(`build(jobs, paid, since) -> dict`, `render(dict) -> str`, pure functions over plain data); `bin/localq` only wires
the command. `localq_share.paid_today` gains an optional `since` (unix time) so the window applies to mail too.

Owners: `brief` + `docstrings` kinds in `bin/localq` + tests → worker-1. `lib/localq_report.py`, `localq_share` `since`,
the `report` wiring + tests → main, with every docstring drafted by `localq draft docstrings` and reviewed by Codex.
`docs/LOCAL-WORK.md` (brief, docstrings, report) + `test/ci.sh` on every merge → worker-2. This file → architect.
