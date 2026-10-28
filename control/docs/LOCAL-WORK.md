# Local work: small jobs for the local models

The studio has models running on its own machines (a model server on the laptop, a local-model app on a studio machine, set by `local_model` kind `cli`). They are
free to run and slow. `bin/localq` is how work reaches them: a queue with a gate in front and a review behind.
The shapes and the gate rules are in [`LOCALQ-CONTRACT.md`](LOCALQ-CONTRACT.md); this page is how to use it.

## The rule of thumb

**Architects add local jobs first. Haiku only reviews.** Before an architect writes a draft, a description or a list
of test cases itself, it queues the job for a local model. Haiku (the auditor) then reads the result and gives a
verdict. Claude and the architects decide what ships. A local model's output is never merged unreviewed.

## What to hand to a local model

Small, self-contained text with an obvious way to check it:

| Kind | Good for |
|---|---|
| `draft` | First drafts: a README section, a commit message, a docstring, boilerplate. |
| `describe` | A plain description of a function, file or config, from text you paste in. |
| `testcases` | A list of test cases for a function whose code you give it. |
| `summarize` | A short summary of a log, a diff or a long message. |
| `review-notes` | A first pass of notes on a change, for a human or Haiku to sharpen. |

Keep prompt plus inputs under 6144 characters. If it does not fit, it is not a small job.

## What not to hand over

- Design decisions: what to build, how to split it, which trade-off to take.
- Large edits, or anything that touches many files.
- Anything that will go out unreviewed, or that nobody will read before it ships.
- Secrets, keys or private names: the input leaves the job file and goes to a model.

## Using it

Write a job file:

```json
{"id": "readme-intro-1", "kind": "draft",
 "prompt": "Write a three-sentence intro for the README of a small queue tool.",
 "inputs": "It queues jobs, runs them one at a time, and records a review for each.",
 "max_chars": 800, "reviewer": "haiku"}
```

```bash
localq add lap readme-intro-1.json     # queue it on the laptop (or m1 / m2 for a Mini)
localq run lap                         # run what is queued, one job at a time
localq results --pending               # done but not yet reviewed
localq review readme-intro-1 accept    # accept | fix | reject, then an optional note
localq stats                           # jobs, characters, seconds, accept rate, per day and in total
```

The result text lands under `<state_dir>/localq/out/`; `output` in the job file can name the file but never a place
outside that folder. An `id` is used once.

## Draft first

Workers draft these with `localq draft <kind> <input-file>`, then edit the result:

| Text | Kind | Input file holds |
|---|---|---|
| commit message | `commit` | the task line + `git diff --cached --stat` |
| test list | `testcases` | the task line + the feature's names, flags and files |
| review summary | `review-notes` | the reviewer's verdict lines |
| changelog line | `changelog` | the task line + the commit subjects, user-facing |
| status line | `status` | the teammate's name + what they are on |
| worker brief | `brief` | the architect's bullet notes: who, goal, files, checks (architect drafts) |
| docstrings | `docstrings` | one function or file of code, with its code lines unchanged |

```bash
# from the main checkout (<main-checkout> = the repo root), so the job and its stats land in its state folder
<main-checkout>/bin/localq draft commit msg-input.txt > msg.txt   # prints the result; the job id goes to stderr
<main-checkout>/bin/localq review <job-id> accept "<note>"       # or fix / reject, once you have checked it
```

**Mini first for small drafts.** `localq draft` tries the machines in `draft_machines` in order (the owner sets it, for
example `["m2", "lap"]`; the default is the laptop only) and uses the first one whose gate passes. A Mini counts only
during an approved window: without one it is skipped at once. The small kinds (`commit`, `status`, `changelog`,
`testcases`, `review-notes`) take this route. `brief` and `docstrings` always stay on the laptop, because they are long
and the 9B model is slow per character.

A runner error on a Mini (a timeout, or the CLI refusing while its app holds the model) marks that job `failed`, and the
draft moves on to the next machine with a new job id. A blocked machine gets no job file.

**Reading the stderr line.** `<id> on m2` means job `<id>` ran on m2; `<id> on lap` means the laptop. stdout is the
result text only. Exit codes: 0 done; 2 every machine blocked (stderr lists each reason); 1 refused, or every runnable
machine failed.

Then edit the output. Never commit or send a draft unedited: it is a starting point, and the teammate owns the final
text.

**Briefs.** The architect writes the task as bullet notes (who, goal, files, checks), runs `localq draft brief`, and
edits the result into the worker brief. The model's text is a starting point; the architect owns the final brief.

**Docstrings.** The worker writes the code first. `localq draft docstrings` returns the same code with docstrings and
short comments added. The worker diffs it against the original and keeps only the comment and docstring lines. Codex
reviews the result, since it is the reviewer for this kind.

**The daily report.** `localq report` prints one screen for today: jobs by kind and status, the verdict mix (accept,
fix, reject, and how many are still unreviewed), the output share (the same estimate as `stats`), and the five slowest
jobs with their id, kind and seconds. It is read-only and at most 24 lines. `localq report --since 14:30` (or
`--since "2026-10-27 09:00"`) narrows it to a window, which gives a per-job view from that time on. `--json` prints
the same data for a script.

Reviewers (Codex included) only check. A Codex reviewer cannot run `localq`, so it sends its verdict lines by message
and a Claude teammate runs the drafts, the full `test/ci.sh`, and `localq review`. Roles are in
[`roles/`](roles/worker.md): [worker](roles/worker.md), [reviewer](roles/reviewer.md), [architect](roles/architect.md).

## The gate

`localq run` asks the gate before it touches a model. If the gate says no, every queued job on that machine is marked
`blocked` with the reason, nothing runs, and the next `run` tries again. The laptop needs no approval. A Mini needs:

1. Your approval, with an end time: `localq allow m1 2` (hours), taken back with `localq deny m1`.
2. None of the apps named in `local_block_apps` (default: the ChatGPT and Claude desktop apps) running there. The gate never opens or quits an app.
3. Enough free memory (`local_min_free_pct` in the machine config, 15 by default).
4. The model reachable.

If the approval runs out in the middle of a run, the jobs still waiting are blocked.

## Reviewing

`localq review <id> accept|fix|reject [note]` records who reviewed (`--by`, default the job's `reviewer`) and when.
Use `fix` when the result needs a small correction before use, `reject` when it is unusable. A new verdict replaces
the earlier one in the stats. Haiku's verdicts are advice; the architect owns the decision.
