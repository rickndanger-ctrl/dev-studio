# Control room

The control room runs on your own machine and watches the studios on your other machines over SSH. It has a live
viewer page that shows what each machine is doing and who relayed what to whom, and an auditor that reads each
studio's state and labels it progressing, stalled, looping or off-scope.

You hand work to a studio yourself with `dispatch`, which gives a brief to that studio's architect. A bar widget
shows a quick count of who is working. Small text jobs for local models go through `localq`, a queue that waits for
your approval before it runs anything on a remote machine. The viewer, auditor and bar widget only read from the machines they watch; `dispatch` and `localq` act on a
remote machine only when you run them. Every owner-specific name, host and folder comes from one config file.

## Parts

| Part | Where | What it does |
|---|---|---|
| Viewer | `viewer/`, `bin/viewer` | A live page with one panel per enabled machine in your config, in config order, and the JEV routing lines between agents. Runs as the `studio-viewer` user service on port 8765. Labels, studio folders and the audit panel come from the config; `python3 test/demo_server.py` shows it with demo data and no machines. |
| Auditor | `bin/re-audit`, `systemd/re-audit.service` | A reviewer model (Haiku) reads each studio's state, the recent routes and commits, and says whether it is progressing, stalled, looping or off-scope. It runs a cheap code check first and only asks the model when something changed. It writes `state/latest.json` and `state/alerts.log`. Without an `audit` block in the config it says "no audit target configured" and exits 0. |
| Dispatch | `bin/dispatch`, `bin/onmini` | `dispatch <machine-id> <brief-file>` hands a brief to that studio's architect. `onmini <machine-id> < script` runs a script on a remote machine, through the routes in your config. |
| Studio watch | `bin/studio-watch` | Config-driven watcher for the studios, built on `bin/studio-snap.py` (one JSON snapshot of every studio: agents, recent routes, commits of the last hour). |
| Event log | `bin/ev`, `bin/builder` | Log one action, or one build agent, so the viewer can show it. |
| Bar widget | `bin/barstat`, `omarchy-plugin/` | Prints the viewer's working count, a tooltip and the local-work totals for the Omarchy bar. |
| Local jobs | `bin/localq`, `lib/localq_*.py` | See "Local jobs and the approval gate" below. |
| Setup | `install.sh`, `bin/bootstrap` | Check the tools, write the config, install the services. |
| Publish tools | `hooks/pre-push`, `test/privacy.sh`, `bin/publish-export` | Keep private names out of what you publish. |

## Local jobs and the approval gate

`localq` is a queue of small jobs (a draft, a description, test cases, a summary) for models that run on your own
machines. Add a job with `localq add <machine> <job.json>`, run the queue with `localq run <machine>`, and read the
results with `localq results`. Every result gets a review, `localq review <id> accept|fix|reject`, and nothing a local
model wrote ships unreviewed. `localq draft <kind> <input-file>` runs one laptop draft and prints it, and
`localq report` prints one screen of today's jobs, verdicts, output share (an estimate) and slowest jobs.

A job runs only when the gate says yes. On this machine the gate checks that the model answers and that memory is free.
On a remote machine it also needs **your approval**: `localq allow <machine> <hours>` opens a window, `localq deny
<machine>` closes it, and while the window is closed every queued job is marked blocked with the reason and nothing runs.
The gate also refuses while any process named in `local_block_apps` (default `ChatGPT`, `Claude`) is running there, and it
never opens or quits an app. How to choose jobs is in [`docs/LOCAL-WORK.md`](docs/LOCAL-WORK.md); the exact rules are in
[`docs/LOCALQ-CONTRACT.md`](docs/LOCALQ-CONTRACT.md).

`localq report --since HH:MM` (or a date and time) limits the report to jobs from that time on. It still aggregates them:
jobs by kind and status, the verdict mix and the output share, with only the five slowest jobs listed one by one.
Two more draft kinds are available through `localq draft`: `brief` turns the architect's bullet notes into one worker
brief, and `docstrings` asks the model for the worker's code with docstrings and comments added and the code lines
unchanged. The worker must diff the result and keep only the comment and docstring lines; Codex reviews that one.
A draft is a starting point, never shipped unedited.

## Two teams on one machine

A machine can run more than one studio team in one herdr session, for example a Claude team and a Codex team. A team
is named by the part of its agent name before the role, so `team-alpha-worker-1` belongs to `team-alpha`; an architect
with no prefix is the studio team. The viewer draws each team as its own cluster inside the machine's panel, with its
own routing band. One team is always drawn open; with several, idle teams fold into one line. The auditor judges each team on its own: verdicts are
keyed `machine/team`, and Haiku is asked once per changed team.

## Setup

Requirements: `python3` (required), `ssh` (for remote machines), `herdr` (for dispatch and the studio sessions).
Optional, for screenshots: chromium and node 22+.

1. Check the tools and install (changes nothing with `--dry-run`):
   ```bash
   bin/bootstrap --dry-run
   bin/bootstrap
   ```
   This creates `~/.config/re-studio/machines.json` if it is missing, renders the user units into
   `~/.config/systemd/user/`, links the scripts into `~/.local/bin/`, and runs `systemctl --user daemon-reload`.
   `install.sh` does the work and is safe to run again; it never overwrites an existing config.
2. Edit `~/.config/re-studio/machines.json` for your machines (see the config reference below). It starts as a copy of
   [`config/machines.example.json`](config/machines.example.json), which uses fake host names.
3. Check that each machine answers: `re_studio_config.py run <machine-id> "uptime"`.
4. Start the services: `bin/bootstrap --enable`, then open the viewer with `viewer` (or http://127.0.0.1:8765).

## Config reference

The file is `~/.config/re-studio/machines.json`. If it is missing, the loader falls back to
`config/machines.example.json`. `RE_STUDIO_CONFIG=<path>` points the loader at another file. Copy the example; don't
edit it in place.

Top-level keys:

| Key | Default | Meaning |
|---|---|---|
| `herdr_session` | `"dev-studio"` | herdr session name the studio scripts use. |
| `viewer_port` | `8765` | Port the viewer serves on. |
| `model_health_port` | `18081` | Port of the local model health check. |
| `state_dir` | `<repo>/state` | Where the viewer, auditor and localq keep their files. |
| `labels` | generic names | `{node id: display name}`, e.g. `{"lap.model": "Local model"}`; node ids are `lap.model`, `lap.member`, `lap.haiku`. |
| `studio_dirs` | `["~/DevStudio"]` | Folders searched for a studio on a remote machine; a machine's own `project` is tried first. |
| `studio_folder_names` | `["DevStudio", "worker-1", "worker-2", "worker-3"]` | Folder names that mark an agent as a studio agent. |
| `remote_path` | `"$HOME/.local/bin:/opt/homebrew/bin"` | PATH prefix on remote machines. |
| `audit` | none | `{"app", "data_dir", "mac_data_dir", "process", "project"}`: display name of the local-model app, its data folder here and on a remote Mac, the process names to check, and the project to audit. Missing: the auditor exits quietly and the viewer hides the audit panel. |
| `local_block_apps` | `["ChatGPT", "Claude"]` | Process names that must not run on a remote machine during local work. |
| `machines` | list | The machines, below. |

Each entry in `machines`:

| Field | Example | Meaning |
|---|---|---|
| `id` | `"m1"` | Short name used on the command line (`dispatch m1`). |
| `label` | `"Mini 1"` | Name shown on the viewer and in route labels. |
| `role` | `"hub"` or `"studio"` | `hub` is this computer; `studio` is a remote machine with an architect. |
| `ssh` | `["mini1.local"]` | SSH hops to reach it, in order. `[]` means local or not set up. |
| `ssh_alt` | `[["mini1.local", "mini2.local"]]` | Other routes to try if `ssh` fails. |
| `project` | `"~/DevStudio"` | The project folder on that machine. |
| `architect` | `"studio-architect"` | Name of that studio's architect agent. |
| `local_model` | `{"kind": "cli", "path": "<binary>", "args": ["-p"]}` | The machine's local model. `kind` is `openai` (an OpenAI-compatible URL) or `cli` (a binary run over SSH with the prompt on stdin). No `local_model` means no local runner. |
| `enabled` | `false` | Skip this machine everywhere; it shows as a small dashed panel. |

Check what the loader resolves:

```bash
re_studio_config.py path               # which file is in use
re_studio_config.py ids studio         # machine ids with role studio
re_studio_config.py field m1 architect # one field
re_studio_config.py run m2 "uptime"    # run a command on m2, trying each route in turn
```

## Tests

Run `bash test/ci.sh` from this folder, or `bash control/test/ci.sh` from the package root. It checks the bash scripts,
compiles the Python, runs the unit tests and the privacy scan, and takes a screenshot of the viewer when chromium and
node 22+ exist. It scopes itself to this folder; `RE_STUDIO_CI_NO_SHOT=1` skips the screenshot.

## Publishing safely

This repo is meant to be pushed to a public remote, so a privacy check runs before anything leaves your machine:

- `test/privacy.sh` scans every tracked file (and, with `--revs`, every commit about to be pushed) for tailnet and
  LAN IP addresses, `/home/<name>/` and `/Users/<name>/` paths, `*.ts.net` names, e-mail addresses, and any word
  in your own list.
- `hooks/pre-push` runs it on exactly what the push would send: a new branch or tag is checked against what no
  remote has yet, an update against `<remote sha>..<local sha>`, deletes are skipped. Any hit blocks the push and is
  printed. The scan reads history, so a private value removed in a later commit is still caught.
- `./install.sh` turns the hook on in a git checkout (`git config core.hooksPath hooks`; safe to run again).
- Your own names go in `.privacy-words`: one word per line, copied from `.privacy-words.example` (fake values).
  The file is gitignored and stays on your machine. Without it the hook warns loudly and still runs the generic
  rules, but it cannot know your user name or host names.
- Fake or documented values that the rules would flag (an example tailnet address in the docs, a made-up
  home path in a test) go in the allow file, `packaging/privacy-allow.txt` (`PRIVACY_ALLOW=<file>` points
  the scan at another one). One entry per line, `#` starts a comment:
  - `TOKEN` allows that exact text everywhere, e.g. `100.64.0.1`.
  - `PATH :: TOKEN` allows it only in that one repo-relative file, e.g. `docs/net.md :: tail1234.ts.net`. Prefer this form.
  - Tokens are exact and case-sensitive, never patterns. A token is masked out of the line before any check, the word
    list included: a word that sits inside a longer allowed token passes there, and the same word anywhere else still fails.
  - Rejected, with exit code 2 and the line number: an entry that equals a word-list entry, and an e-mail address.
    Your own names and e-mail addresses can never be allowed.
  - Every entry carries a comment saying what the value is and why it is safe (see CONTRIBUTING.md).
- When the hook blocks a push because old commits carry private data, publish a clean copy with `bin/publish-export`
  instead of pushing this history.
- `git push --no-verify` skips the hook. Don't, unless you have checked by hand.

## Systemd units

`systemd/*.service` are templates. `install.sh` fills in these placeholders:

- `@REPO@`: this checkout.
- `@PYTHON3@`: the `python3` on this machine's PATH.
- `@PATH@`: the PATH the services run with. By default it is built like this:
  1. the directories of `python3`, `herdr`, `claude` and `node` as they resolve in your shell
     (duplicates removed);
  2. then `~/.local/bin`;
  3. then `/usr/bin`, always last.

  `/usr/bin` and `~/.local/bin` are never taken from the tool list, so a tool living in `/usr/bin`
  cannot push the system `node` or `python3` in front of your mise tools.

To pin the PATH yourself, set `RE_STUDIO_UNIT_PATH` (used verbatim, nothing is added):

```bash
RE_STUDIO_UNIT_PATH=/home/you/.local/share/mise/installs/node/26.12.1/bin:/home/you/.local/bin:/usr/bin ./install.sh
```

## Environment variables for `install.sh`

| Variable | Effect |
|---|---|
| `RE_STUDIO_HOME=<dir>` | Install into `<dir>` instead of `$HOME` (config, units, links). Skips `systemctl`. For tests. |
| `RE_STUDIO_UNIT_PATH=<path>` | Use this exact string for `@PATH@` in the units. See above. |

`bin/mini-room.py` is not linked into `~/.local/bin`: it is a script that `onmini` runs on a Mini.
