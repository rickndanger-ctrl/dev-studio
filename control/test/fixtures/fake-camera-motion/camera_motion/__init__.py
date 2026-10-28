"""Fake camera-motion package: a tiny stand-in for tests."""
__version__ = "0.1.0"


def detect(previous, current, threshold=10):
    """Return True when the mean absolute difference between two frames is above threshold."""
    diffs = [abs(a - b) for a, b in zip(previous, current)]
    return bool(diffs) and sum(diffs) / len(diffs) > threshold
