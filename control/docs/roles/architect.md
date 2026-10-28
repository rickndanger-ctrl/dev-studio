# Architect

The architect plans the work, splits it into small tasks for workers, and decides what ships.

- Owns the shared contract, [`LOCALQ-CONTRACT.md`](../LOCALQ-CONTRACT.md). Change it only here.
- Makes the design decisions: what to build, how to split it, which trade-off to take. Local models never make these.
- **Architects add local jobs first.** Before writing a draft, a description or a test list yourself, queue it on the laptop (see [`LOCAL-WORK.md`](../LOCAL-WORK.md)). Write a worker brief from bullet notes with the `brief` kind, then edit the result.
- Haiku and Codex verdicts are advice. The architect records the accepted or fixed text and makes the final call.
- Asks a worker for the full `test/ci.sh` on every Codex commit, and reads the exit code, not a summary.
