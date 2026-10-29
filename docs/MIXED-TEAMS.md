# Mixed teams: using more than one AI tool at once

This page is for people who are not programmers. It explains why Dev Studio can run a team that
mixes tools, how to start one, and what to do when something gets stuck.

## The short version

Dev Studio can run one team with more than one kind of AI helper in it. A helper that is working
for you is called an **agent**. Some agents run on Claude (from Anthropic). Some run on Codex
(from OpenAI, which comes with a ChatGPT plan). In future, some can run on a model on your own Mac.
The idea: give each job to the cheapest tool that can do it well, and move up to a stronger tool
only when the job needs it.

## Why mix tools at all

Each tool has a usage limit. Hit the limit on one and that tool stops until the limit resets. A
team that uses only one tool stops when that tool does. Spreading the work across two subscriptions
keeps the team going, and it lets you match each job to the tool that suits it.

1. **Less waiting.** When one tool is busy or out of room, the other can keep going.
2. **Lower cost for small jobs.** Rewording a note or tidying a line of text does not need the
   biggest model. Send those to the cheapest tool that does them well.
3. **Better fit.** Some tools are better at planning, some at writing code in a folder, and some
   at quick checks.

## The three kinds of helper

| Helper | Where it comes from | Good for | What it costs you |
|---|---|---|---|
| **Claude** | A Claude subscription (Claude Code) | Planning, careful code changes, the full test suite | Counts against your Claude usage limit |
| **Codex** | A ChatGPT plan (the Codex CLI) | Writing code in a project folder, sending results | Counts against your ChatGPT usage limit |
| **Local model** | A model that runs on your own Mac | Small drafts: a note, a summary, a line to tidy | No per-use cost, but slower and less capable |

## The rule: the cheapest capable tool first

Think of it as a ladder. Start at the bottom. Move up only when the job is too hard for the step
you are on.

1. **Small drafts** (a note, a summary, a short rewrite): a local model first, if you have one.
   A reviewer checks the draft before anyone uses it. The link for the local queue is coming soon.
2. **Routine coding and checks** (a small change, reading a file): a Codex worker. Codex sends its
   results with `studio send`, and a Claude teammate runs the full test suite on them.
3. **Careful coding and reviews** (a change across several files, a bug you cannot reproduce): a
   Claude main session.
4. **Planning and sign-off** (deciding what to build, approving a change before it is merged): the
   architect, a stronger Claude model.

If a helper fails twice, or its answer is wrong, move the job one step up. Do not keep retrying the
cheap helper on a hard job.

## Starting a mixed team

A standard team has one **architect** (plans and checks the work), one **main session** (builds the
feature), and **workers** (small jobs, each in its own copy of the project). You choose the tool for
each role with these flags to `studio up`:

- `--architect-kind claude|codex` sets the architect's tool.
- `--main-kind claude|codex` sets the main session's tool.
- `--worker-kind claude|codex` sets every worker's tool.
- `--workers-mixed` alternates the workers: worker-1 codex, worker-2 claude, and so on.

Examples:

```bash
studio up --architect-kind claude --main-kind claude --workers-mixed
studio up --architect-kind claude --main-kind claude --worker-kind codex
```

`--worker-kind` and `--workers-mixed` cannot be used together.

Check who is doing what at any time:

```bash
studio status
```

It lists each role and the tool it runs. The board's title shows the kind of team: a Claude team,
a Codex team, or a **MIXED** team.

## Reviewer

A **reviewer** is an optional extra agent that reads each finished piece of work before you merge
it. It never edits code. When a worker reports `done <commit>`, the studio asks the reviewer to read
that commit and reply with one of three verdicts, which the architect receives:

- `REVIEW <commit>: PASS` means nothing needs fixing.
- `REVIEW <commit>: CHANGES` means there are fixable findings, listed in the same message.
- `REVIEW <commit>: BLOCK` means the change must not land as it is.

If the reviewer does not answer in time, the architect gets `REVIEW <commit>: TIMEOUT` instead.
Nothing waits for the reviewer; the team keeps working.

Turn it on with the flag, which accepts `codex`, `claude` or `none` (the default):

```bash
studio up --reviewer codex
```

A codex reviewer always runs with a read-only sandbox, whatever `CODEX_SANDBOX` says. A claude
reviewer runs in plan mode, which asks before any change, and it still never edits. The reviewer
has no way to send messages itself; the studio relays its verdict. Starting a team without the flag
(or with `--reviewer none`) starts no reviewer.

## How each helper signs its messages

Every helper signs what it sends, so the router can tell who really wrote a message. A Claude
helper keeps its secret in its own pane. A Codex helper's secret is kept in a file instead:
`.studio/seals/<name>` inside the project's studio folder. That folder is private (readable only
by your user account), the file is rewritten each time you start the team, and the reviewer never
gets one. Codex runs its commands in a shared background process whose environment can still hold
an old pane's secret, so when Codex sends a message the studio reads its file and ignores the
environment. Stopping the studio deletes these files.

## Settings in ~/.dev-studio/studio.conf

Open the file in any text editor. These lines control the Codex side and the reviewer:

- `CODEX_WORKER_MODEL` picks the model Codex workers use. You can point it at a cheaper model from
  your Codex plan for routine jobs.
- `CODEX_SANDBOX` picks the Codex sandbox (the limits on what Codex may write). Leave it blank to
  use Codex's own default.
- `REVIEWER=codex` (or `claude`) starts a reviewer on every team, as `--reviewer` does. Leave it at
  `none` for no reviewer. The `--reviewer` flag overrides this line for one team.

Restart the team after changing these (see below).

## What Codex may write, and what still asks you

A Codex worker can write to these places without asking:

- the outbox, so `studio send` works;
- the project's git objects, refs and logs, and its own worktree's git folder, so it can add,
  commit, reset and create branches;
- the folders listed in `.studio/codex-writable` in the project. The README explains how to list
  them.

Codex still asks before it does these:

- deleting a branch or a tag;
- anything that needs the network, such as a test that starts a local server.

## Good to know before you start

- **Restart to change tools.** A role keeps its tool until the team is started again. Stop the team
  with `studio stop`, then start it with `studio up` and the new flags.
- **The Studio does not count your spending.** It does not know what a job costs, and it does not
  choose a tool for you. You choose the tools with the flags above. Watch your own usage on each
  subscription.
- **Each tool has its own limits.** If one stops, move its jobs to another helper, or wait for it
  to reset.

## When something gets stuck

- **A trust prompt appears on the first start.** Open the agent's pane and approve the folder. You
  only need to do this once per project.
- **Codex stops at a sandbox prompt.** Either approve the action in the Codex pane, or, if the same
  folder keeps being asked about, list it in `.studio/codex-writable` and restart the team.
- **Herdr does not start right after `studio stop`.** Wait a few seconds, then run `studio up`
  again. Herdr needs a moment to shut down.

## Words you may meet

- **Agent**: one AI helper with a job in the team.
- **Role**: the job an agent has: architect, main session or worker.
- **Pane**: the box in the Dev Studio window where one agent runs.
- **Router (JEV)**: the part that reads each message and passes it to the right agent.
- **Worktree**: a separate copy of the project for one worker, so workers do not overwrite each other.
- **Sandbox**: the limits Codex runs under. It decides which folders Codex can write to.
