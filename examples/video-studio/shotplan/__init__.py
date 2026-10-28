"""shotplan: plan shots in a JSON file and render them to one timeline with stillmove.

Importing the package makes camera_motion importable (stillmove._cm finds the sibling checkout)
and re-exports the command's functions: shotplan.main, check, load_json, timeline_svg and CATALOG.
"""

from shotplan.shotplan import CATALOG, check, load_json, main, timeline_svg  # noqa: F401
