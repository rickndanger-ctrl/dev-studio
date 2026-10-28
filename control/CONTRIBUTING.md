# Contributing

## Branches

- `main` is what runs. Nobody commits to it directly; the architect merges after review.
- Work on a branch named for the job (`viewer-polish`, `reviewer-studios`, `github-ready`). Keep one job per branch and
  commit per step with a message that says what changed.
- Touch only the files your job owns. If you change something others depend on (a config key, a state field, a script
  argument), say so in your report.

## Tests

Run the whole check before you hand a branch over:

```bash
test/ci.sh
```

It must pass on a Mac as well as Linux: scripts run on the Minis under macOS bash 3.2 and the system Python, so
check `bash -n` on every shell script and keep the Python standard library only.

To see a viewer change, `test/shot.sh <dir> <port>` starts the real server on a spare port and screenshots it at 1280x800
and 1920x1080. For a picture with no private data, use `test/demo_server.py` and `test/shot.mjs`.
Never use port 8765 (the live viewer) and never restart the live services while testing.

## Privacy rule

`./install.sh` enables `hooks/pre-push`, which runs `test/privacy.sh` on every commit and tag you push and blocks the
push on a hit. Put your own names in `.privacy-words` (copy `.privacy-words.example`; it is never committed) so the
hook can catch them too.

Every entry in `packaging/privacy-allow.txt` needs a comment line above it saying what the value is and why it is
safe, and the architect reviews each new entry: an allow entry is a hole in the guard. Use the `PATH :: TOKEN` form
so it only applies in the one file that needs it, and never allow a real name, address or host. `bash test/ci.sh` runs the same scan over the tree. See "Publishing safely" in the README.

This repo is meant to be published. No real host names, user names, IP addresses, home paths, API keys or messages from
real work may be committed, in code, docs, examples, tests or screenshots.

Before every commit, check the lines you added for your own user name, your machines' host names, home paths and IP
addresses (add any other private names you use):

```bash
git add -N . && git diff | grep '^+' | grep -iE "$(whoami)|$(hostname)|/home/|/Users/|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"
```

It must print nothing (the `+++` file headers are fine). Open each screenshot you add and check that it shows only demo data. Machine details belong in
`~/.config/re-studio/machines.json`, which is not in the repo; the example config uses placeholders such as
`mini1.local`.
