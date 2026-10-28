# Worker

A worker takes one small task from the architect or the main session and does exactly that, in its own worktree.

- Read the project's `CLAUDE.md` / `AGENTS.md` first.
- **Draft first.** Before you write a commit message, a test list, a review summary, a changelog line, a status line or a docstring by hand, queue a draft on the laptop and edit the result. See [`LOCAL-WORK.md`](../LOCAL-WORK.md#draft-first).
- Tests depend on who you are:
  - **Claude workers** run the full `bash test/ci.sh` from your worktree. Capture its exit code on the next line, and gate the commit on that exit code, never on a pipe.
  - **Codex workers** run only sandbox-safe unit files with `python3 -m unittest` (for example `test_localq_unit.py`). Anything that needs a socket, a server or a running router goes to a Claude worker. The full `test/ci.sh` is run by a Claude teammate on the Codex commit.
- Commit with the attribution line the session gives you. Never merge to the main branch yourself.
- Report with one line to the architect: done, blocked, and the commit hash. Never paste diffs or file contents into a message.
- Keep secrets and private names out of any job input: the input goes to a model.
