# Fake Dev Studio

A tiny stand-in project for tests. It is not a real studio.

<!-- whats-in-the-box -->
- `bin/studio`: prints the version line.
- `install.sh`: copies `bin/studio` into a prefix (default `./out`).
- `test/run.sh`: checks the syntax and the output.
- `.github/workflows/test.yml`: runs `test/run.sh` on push.
