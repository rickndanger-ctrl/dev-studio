"""Camera-motion preset catalog, validator and tests."""
import json
import os

HELP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "help.json")


def load_help(path=None):
    """Plain-words help per preset: {preset_id: {"when": str, "feel": str}}.

    Reads help.json beside this file unless path is given. Raises FileNotFoundError, naming
    the file, when it is missing: a broken install should be loud, not an empty help.
    """
    path = path or HELP_PATH
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"help file not found: {path} (is the install complete? reinstall camera-motion)") from None


CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.json")


def load_catalog(path=None):
    """Return the preset list from a catalog file (bundled catalog.json by default).

    Accepts both catalog forms: a JSON list of presets, or {"presets": [...]}.
    Raises ValueError if the file holds anything else.
    """
    with open(path or CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)
    if isinstance(catalog, dict):
        catalog = catalog.get("presets")
    if not isinstance(catalog, list) or not all(isinstance(p, dict) for p in catalog):
        raise ValueError(f"{path or CATALOG_PATH}: not a preset catalog (a list, or an object with a 'presets' list)")
    return catalog
