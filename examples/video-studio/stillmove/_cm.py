"""Locate and import camera_motion without copying it.

Order: an installed or already-importable camera_motion wins. Otherwise the sibling
checkout is found by walking up from this file to a folder that holds
camera-motion/camera_motion/. Nothing is copied.
"""

import os
import sys

_PKG = "camera_motion"


def find_repo_checkout(start=None):
    """Return the camera-motion folder (containing camera_motion/) found by walking up, or None."""
    here = os.path.abspath(start or os.path.dirname(__file__))
    while True:
        candidate = os.path.join(here, "camera-motion")
        if os.path.isfile(os.path.join(candidate, _PKG, "__init__.py")):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def import_camera_motion():
    """Import camera_motion, adding the sibling checkout to sys.path if needed."""
    try:
        import camera_motion  # noqa: F401
        return camera_motion
    except ImportError:
        pass
    root = find_repo_checkout()
    if root is None:
        raise ImportError(
            "camera_motion is not installed and no camera-motion checkout was found above "
            "video-studio/stillmove. Install camera-motion or run from its repository."
        )
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import camera_motion  # noqa: F811
    except ImportError as exc:
        raise ImportError(f"could not import camera_motion from {root}: {exc}") from exc
    return camera_motion
