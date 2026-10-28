"""Check a model file against its pinned SHA256 before it is loaded.

One helper for every model: depth_anything, matte and the doctor all call verify(). The hash is
cached per (absolute path, size, mtime), so a render that loads the model again does not hash
the file again. A file that changes size or time is hashed afresh.
"""
import hashlib
import os

_CHUNK = 1 << 20
_cache = {}


def file_sha256(path):
    """The SHA256 hex digest of a file, read in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_sha256(path):
    stat = os.stat(path)
    key = (os.path.abspath(path), stat.st_size, stat.st_mtime_ns)
    if key not in _cache:
        _cache[key] = file_sha256(path)
    return _cache[key]


def verify(path, sha256):
    """Return path if the file's SHA256 is the expected one; otherwise raise ValueError.

    The message names the expected SHA256 and the one found, so a swapped file is easy to spot.
    A missing file raises FileNotFoundError, as before.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"model file not found: {path}")
    found = _cached_sha256(path)
    if found != sha256.lower():
        raise ValueError(
            f"{path} does not match the pinned model: expected SHA256 {sha256.lower()}, found {found}"
        )
    return path
