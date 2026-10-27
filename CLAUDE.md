# Dev Studio

Read LESSONS.md before doing anything else in this repo, and add a lesson whenever you make a mistake.

- Scripts must run on the bash 3.2 that ships with macOS (no associative arrays, mapfile, or ${var,,}).
- Before committing: `shellcheck` every script, run `/bin/bash -n` on each script (on a Mac), and run `test/run.sh`.
- Never put heredocs, `case`, or comments with apostrophes inside `$( ... )`: bash 3.2 misparses them.
- The owner is a creative lead, not a programmer: explain in plain language.
