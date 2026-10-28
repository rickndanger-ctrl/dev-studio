#!/usr/bin/env bash
# Privacy guard. Fails on: tailnet IPs (the 100.64/10 range), private LAN IPs, /home/<name>/ or /Users/<name>/
# (only runner, you, user and example are allowed), *.ts.net names, real-looking e-mail addresses, and any word
# listed in .privacy-words (untracked and gitignored, so the owner's own names never land in the repo).
#
# Modes:
#   test/privacy.sh [REPO]               fast tree scan: every git-tracked file, text as it is, binaries by strings
#   test/privacy.sh --revs <args...>     history scan of the commits `git rev-list <args...>` lists, in the current repo:
#                                        author and committer lines, commit messages, path names, and the content of
#                                        every blob those commits add or change (binaries by strings).
#                                        E-mail addresses pass only for example.* placeholders, *@users.noreply.github.com
#                                        and noreply@anthropic.com (the same rule in both modes).
#   test/privacy.sh --history            --revs --all
# Hits print as <location>: <rule>: <text>, and the script exits 1 if there is any hit. Exit 2 means bad usage.
# PRIVACY_WORDS=<file> overrides the word list (default: .privacy-words in this script's project folder).
# PRIVACY_ALLOW=<file> overrides the allow list (default: packaging/privacy-allow.txt in the project, if present).
# The allow list has one entry per line ('#' starts a comment): 'TOKEN' applies everywhere, 'PATH :: TOKEN' only in
# that exact repo-relative file. Tokens are exact and case-sensitive, and are masked out of a line before any check,
# so a word inside a longer allowed token passes while the same word elsewhere still fails. An entry equal to a
# word-list entry, or an e-mail address, is refused with exit 2.
# The project may be a subfolder of another git repo: the tree scan then covers the whole outer repo, and this
# project's own exemptions (SELF) and word list are matched by their path inside that repo.
set -uo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

MODE=tree
REVS=()
case "${1:-}" in
  --history)
    [ $# -eq 1 ] || { echo "usage: test/privacy.sh --history" >&2; exit 2; }
    MODE=revs
    REVS=(--all)
    ;;
  --revs)
    MODE=revs
    [ $# -ge 2 ] || { echo "usage: test/privacy.sh --revs <rev-list args...>" >&2; exit 2; }
    shift
    REVS=("$@")
    set --
    ;;
esac

PROJECT="$(dirname "$HERE")"
if [ "$MODE" = revs ]; then
  REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "privacy: not inside a git repo" >&2; exit 2; }
else
  REPO="${1:-$PROJECT}"
fi
WORDS="${PRIVACY_WORDS:-$PROJECT/.privacy-words}"
if [ -n "${PRIVACY_ALLOW:-}" ] && [ ! -f "$PRIVACY_ALLOW" ]; then
  echo "privacy: allow list not found: $PRIVACY_ALLOW" >&2
  exit 2
fi
ALLOW="${PRIVACY_ALLOW:-$PROJECT/packaging/privacy-allow.txt}"
[ -f "$ALLOW" ] || ALLOW=""

exec python3 - "$REPO" "$WORDS" "$MODE" "$PROJECT" "$ALLOW" "${REVS[@]+"${REVS[@]}"}" <<'PY'
import os, re, subprocess, sys

repo, words_path, mode, project, allow_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
revs = sys.argv[6:]
# The guard, its test, and the word-list template describe or hold the patterns on purpose, so they are not scanned.
SELF_REL = {"test/privacy.sh", "test/test_privacy.py", ".privacy-words.example",
            "packaging/privacy-allow.txt"}  # relative to this project; the allow list itself is not scanned
ALLOWED_USERS = {"runner", "you", "user", "example"}
ALLOWED_DOMAINS = {"example.com", "example.org", "example.net"}


def project_prefix(base):
    """Where this project sits inside base, as a repo-relative prefix ('' when it is base itself or outside it)."""
    rel = os.path.relpath(os.path.realpath(project), os.path.realpath(base)).replace(os.sep, "/")
    return "" if rel in (".", "..") or rel.startswith("../") else rel


PREFIX = project_prefix(repo)
SELF = {f"{PREFIX}/{p}" if PREFIX else p for p in SELF_REL}
WORDS_REL = f"{PREFIX}/.privacy-words" if PREFIX else ".privacy-words"

RULES = [
    # No lookarounds: any address-shaped text is a hit, even inside a longer number, so an allow-list
    # mask that did not apply cannot let a lookalike address pass.
    ("tailnet-ip", re.compile(r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}")),
    ("lan-ip", re.compile(r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
                          r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}")),
    ("home-path", re.compile(r"/home/([A-Za-z0-9_.-]+)/|/Users/([A-Za-z0-9_.-]+)/")),
    ("tailnet-name", re.compile(r"[A-Za-z0-9-]+\.ts\.net\b")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")),
]


def allowed(rule, m):
    if rule == "home-path":
        return (m.group(1) or m.group(2)) in ALLOWED_USERS
    if rule == "email":
        addr = m.group(0).lower()
        domain = m.group(1).lower()
        return domain in ALLOWED_DOMAINS or domain.endswith(".example") \
            or domain == "users.noreply.github.com" or addr == "noreply@anthropic.com"
    return False


words = []
if os.path.exists(words_path):
    with open(words_path, encoding="utf-8") as f:
        words = [w.strip() for w in f if w.strip() and not w.lstrip().startswith("#")]
else:
    print(f"warning: no word list at {words_path}; owner names are not checked", file=sys.stderr)
word_rx = [re.compile(re.escape(w), re.IGNORECASE) for w in words]

# Allow list: 'TOKEN' applies everywhere; 'PATH :: TOKEN' applies only in that exact repo-relative file.
# Tokens are exact and case-sensitive. They are masked out of a line before any check, the word list included.
EMAIL_RX = re.compile(r"[^\s@]+@[^\s@]+")
GLOBAL_TOKENS = set()
FILE_TOKENS = {}
if allow_path:
    with open(allow_path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            path, tok = (None, line)
            if "::" in line:
                path, tok = (part.strip() for part in line.split("::", 1))
            if not tok:
                continue
            if EMAIL_RX.search(tok):
                print(f"privacy: allow list line {n} is an e-mail address; addresses cannot be allowed", file=sys.stderr)
                sys.exit(2)
            if any(tok.lower() == w.lower() for w in words):
                print(f"privacy: allow list line {n} equals a word-list entry; word-list hits cannot be allowed",
                      file=sys.stderr)
                sys.exit(2)
            if path:
                FILE_TOKENS.setdefault(path, set()).add(tok)
            else:
                GLOBAL_TOKENS.add(tok)


WORD_CH = re.compile(r"[A-Za-z0-9_]")


def at_boundary(text, start, end, tok):
    """A token only masks where it is not glued to a longer word or number."""
    if WORD_CH.match(tok[0]) and start > 0 and WORD_CH.match(text[start - 1]):
        return False
    if WORD_CH.match(tok[-1]) and end < len(text) and WORD_CH.match(text[end]):
        return False
    if tok[0].isdigit() and start >= 2 and text[start - 1] == "." and text[start - 2].isdigit():
        return False  # the token is the tail of a longer dotted number
    if tok[-1].isdigit() and end + 1 < len(text) and text[end] == "." and text[end + 1].isdigit():
        return False  # the token is the head of a longer dotted number
    return True


def mask(text, path):
    """Blank out every allowed token that applies to this file (longest first), keeping the line's length."""
    toks = set(GLOBAL_TOKENS) | (FILE_TOKENS.get(path, set()) if path is not None else set())
    for tok in sorted(toks, key=len, reverse=True):
        start = text.find(tok)
        while start != -1:
            end = start + len(tok)
            if at_boundary(text, start, end, tok):
                text = text[:start] + " " * len(tok) + text[end:]
            start = text.find(tok, start + 1)
    return text


hits = []  # (location, rule, text)


def scan(loc, text, path=None):
    text = mask(text, path)
    for rule, rx in RULES:
        for m in rx.finditer(text):
            if not allowed(rule, m):
                hits.append((loc, rule, m.group(0)))
    for rx in word_rx:
        if rx.search(text):
            hits.append((loc, "privacy-word", "matches an entry in the word list"))


def git(*args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True)


def scan_content(loc_prefix, data, path):
    if b"\0" in data[:8000]:
        # Binary: scan the printable runs, numbered like `strings` output.
        for n, run in enumerate(re.findall(rb"[\x20-\x7e]{4,}", data), 1):
            scan(f"{loc_prefix}:strings#{n}", run.decode("ascii"), path)
    else:
        for n, line in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
            scan(f"{loc_prefix}:{n}", line, path)


def finish(ok_text):
    for loc, rule, text in hits:
        print(f"{loc}: {rule}: {text}")
    if hits:
        print(f"privacy: {len(hits)} hit(s)", file=sys.stderr)
        sys.exit(1)
    print(ok_text)
    sys.exit(0)


def tree_mode():
    listing = subprocess.run(["git", "-C", repo, "ls-files", "-z"], capture_output=True, check=True).stdout
    files = [p for p in listing.decode("utf-8", "replace").split("\0") if p]
    if WORDS_REL in files:
        hits.append((f"{WORDS_REL}:1", "privacy-words-tracked", "the word list must stay untracked"))
    for path in files:
        if path in SELF:
            continue
        scan(f"{path}:1", path, path)  # path names are checked too
        with open(os.path.join(repo, path), "rb") as f:
            scan_content(path, f.read(), path)
    finish(f"privacy: OK ({len(files)} tracked file(s) scanned)")


def revs_mode():
    r = git("rev-list", *revs)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
        sys.exit(2)
    commits = r.stdout.decode().split()
    if not commits:
        print("privacy: OK (0 commits)")
        sys.exit(0)
    seen = set()  # (blob, path) pairs already scanned, so a blob shared by many commits is read once
    for sha in commits:
        short = sha[:12]
        who = git("show", "-s", "--format=%an%n%ae%n%cn%n%ce", sha).stdout.decode("utf-8", "replace").split("\n")
        scan(f"{short}:(author):1", who[0])
        scan(f"{short}:(author):1", who[1])
        scan(f"{short}:(committer):1", who[2])
        scan(f"{short}:(committer):1", who[3])
        msg = git("show", "-s", "--format=%B", sha).stdout.decode("utf-8", "replace").split("\n")
        for n, line in enumerate(msg, 1):
            scan(f"{short}:(message):{n}", line)
        raw = git("diff-tree", "-r", "--root", "--no-commit-id", "-m", "--no-renames", sha)
        for line in raw.stdout.decode("utf-8", "replace").splitlines():
            meta, _, path = line.partition("\t")
            if not path:
                continue
            fields = meta.split()
            dst, status = fields[3], fields[4][:1]
            scan(f"{short}:{path}:1", path, path)
            if status in ("A", "M", "T") and set(dst) != {"0"} and path not in SELF and (dst, path) not in seen:
                seen.add((dst, path))
                scan_content(f"{short}:{path}", git("cat-file", "blob", dst).stdout, path)
    finish(f"privacy: OK ({len(commits)} commits)")


if mode == "revs":
    revs_mode()
else:
    tree_mode()
PY
