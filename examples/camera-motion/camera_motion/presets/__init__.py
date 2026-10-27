"""Camera-motion preset catalog, validator and tests."""
import json
import os

HELP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "help.json")


def load_help(path=None):
    """Plain-words help per preset: {preset_id: {"when": str, "feel": str}}.

    Reads help.json beside this file unless path is given. A missing file gives {}.
    """
    try:
        with open(path or HELP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
