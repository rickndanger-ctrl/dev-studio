# Control config contract (architect, job public-control)

Goal: the control room ships in the public dev-studio under `control/`. Nothing about one owner's machines may be
in the code: every name, folder, label and app comes from `~/.config/re-studio/machines.json`. The repo only holds
generic defaults and fake examples (`config/machines.example.json`). Breaking change rule: an owner's existing
config must keep working after they add the keys below; nothing may silently fall back to a private value.

## New optional top-level keys (defaults in brackets)
| Key | Used by | Meaning |
|---|---|---|
| `labels` | viewer | `{node id: display name}` merged over the viewer's generic names, e.g. `{"lap.model": "Local model"}` [generic names only] |
| `studio_dirs` | viewer remote script, `bin/dispatch` | folders searched for a studio on a remote machine [`["~/DevStudio"]`]; a machine's own `project` is tried first |
| `studio_folder_names` | viewer | folder names that mark an agent as a studio agent [`["DevStudio", "worker-1", "worker-2", "worker-3"]`] |
| `remote_path` | viewer, dispatch, studio-snap, studio-watch | PATH prefix on remote machines [`"$HOME/.local/bin:/opt/homebrew/bin"`] |
| `audit` | `bin/re-audit`, `bin/mini-room.py`, viewer | `{"app": "<display name>", "data_dir": "<folder>", "mac_data_dir": "<folder on a Mini>", "project": "<Room project>", "process": ["<pgrep -x name or -f pattern>"]}`; when missing, the auditor says "no audit target configured" and exits 0, and the viewer hides the audit panel |
| `local_block_apps` | `lib/localq_gate.py` | process names that must not run on a Mini during local work [`["ChatGPT", "Claude"]`] |

## Machines
- `local_model` kinds become `openai` and `cli`: `{"kind": "cli", "path": "<binary>", "args": ["-p"]}` runs the binary
  over SSH with the prompt on stdin. There is no app-specific kind and no default binary path: a studio machine without
  `local_model` simply has no local runner (the gate says so).
- The viewer draws one panel per enabled machine in the config, in config order, using each machine's `label`; no fixed
  `lap`/`m1`/`m2`/`m3` layout. The hub stays first.

## Owners
| Part | Owner |
|---|---|
| `lib/localq_models.py`, `lib/localq_gate.py`, their tests, the localq part of `config/machines.example.json` | worker-1 |
| `viewer/`, `systemd/*.service` descriptions, `bin/ev` comments, `bin/studio-snap.py` | main |
| `bin/re-audit`, `bin/mini-room.py`, `bin/dispatch`, `bin/studio-watch` (new, tracked, config-driven), `PACKAGING.md` "Still hardcoded" | worker-2 |
| this file, the owner's own `machines.json` (real values), merges | architect |

Never write a real name, host, path or label into the repo, a test, or a message: report only the key names you
need, and the architect fills the owner's real values into the owner's config.
