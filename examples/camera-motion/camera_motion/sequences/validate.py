"""Validate a camera shot sequence.

validate_sequence(seq, catalog=None) returns a list of error strings. An empty list
means valid. catalog defaults to presets/catalog.json.

Bounds, lengths and required fields come from sequences/schema.json. Each segment's
params are merged over its preset's params with merge_params(), and the merged preset
must pass presets/schema.json. That is the rule JSON Schema cannot express for
sequences, so this file enforces it. merge_params() is the one merge rule: a null
override removes that param. build() uses it too.
"""

import copy
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(HERE)

from camera_motion.presets import validate as preset_validate  # noqa: E402

SEQUENCE_SCHEMA = os.path.join(HERE, "schema.json")
PRESET_SCHEMA = os.path.join(PACKAGE_DIR, "presets", "schema.json")
CATALOG = os.path.join(PACKAGE_DIR, "presets", "catalog.json")


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, parse_constant=preset_validate._reject_constant)


def index_presets(catalog):
    """Return {id: preset} from a catalog that is either a list or {"presets": [...]}."""
    presets = catalog if isinstance(catalog, list) else catalog["presets"]
    return {preset["id"]: preset for preset in presets}


def merge_params(base, overrides):
    """Return a new params dict: base with overrides applied. A null override removes that param.

    Example: merge_params({"angle": -45, "radius": 5.0}, {"radius": None}) -> {"angle": -45}.
    The inputs are not changed.
    """
    merged = dict(base)
    for key, value in overrides.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def merged_preset(preset, overrides):
    """Return a copy of a catalog preset with the segment's overrides applied to its params."""
    merged = copy.deepcopy(preset)
    merged["params"] = merge_params(preset["params"], overrides)
    return merged


_SCHEMA = _load(SEQUENCE_SCHEMA)
_PRESET_SCHEMA = _load(PRESET_SCHEMA)
_PRESETS = index_presets(_load(CATALOG))


def _kind(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number (%r)" % (value,)
    if isinstance(value, str):
        return "string (%r)" % (value,)
    if isinstance(value, list):
        return "array"
    return "object"


def _string(value, path, rule, errors):
    if not isinstance(value, str):
        errors.append("%s: must be a string, got %s" % (path, _kind(value)))
        return
    if len(value) < rule.get("minLength", 0):
        errors.append("%s: must be at least %d character(s) long, got %r"
                      % (path, rule["minLength"], value))
    if "maxLength" in rule and len(value) > rule["maxLength"]:
        errors.append("%s: must be at most %d character(s) long, got %d"
                      % (path, rule["maxLength"], len(value)))
    if "pattern" in rule and not re.search(rule["pattern"], value):
        errors.append("%s: must match pattern %s, got %r" % (path, rule["pattern"], value))


def _integer(value, path, rule, errors):
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append("%s: must be an integer, got %s" % (path, _kind(value)))
        return
    if "minimum" in rule and value < rule["minimum"]:
        errors.append("%s: must be >= %s, got %d" % (path, rule["minimum"], value))
    if "maximum" in rule and value > rule["maximum"]:
        errors.append("%s: must be <= %s, got %d" % (path, rule["maximum"], value))


def _number(value, path, rule, errors):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append("%s: must be a number, got %s" % (path, _kind(value)))
        return
    if "exclusiveMinimum" in rule and value <= rule["exclusiveMinimum"]:
        errors.append("%s: must be > %s, got %r" % (path, rule["exclusiveMinimum"], value))
    if "maximum" in rule and value > rule["maximum"]:
        errors.append("%s: must be <= %s, got %r" % (path, rule["maximum"], value))


def _fields(obj, path, rule, errors):
    """Required and unknown field checks for an object against its schema rule."""
    allowed = rule["properties"]
    for key in rule.get("required", []):
        if key not in obj:
            errors.append("%s: missing required field '%s'" % (path, key))
    for key in obj:
        if key not in allowed:
            errors.append("%s: unknown field '%s' (allowed: %s)"
                          % (path, key, ", ".join(sorted(allowed))))


def _segment(segment, index, rule, errors, presets):
    path = "segments[%d]" % index
    if not isinstance(segment, dict):
        errors.append("%s: must be an object, got %s" % (path, _kind(segment)))
        return
    _fields(segment, path, rule, errors)
    props = rule["properties"]

    preset_id = segment.get("preset")
    if "preset" in segment:
        _string(preset_id, path + ".preset", props["preset"], errors)
        if isinstance(preset_id, str) and preset_id not in presets:
            errors.append("%s.preset: unknown preset '%s' (catalog has %d presets)"
                          % (path, preset_id, len(presets)))

    if "duration_s" in segment:
        _number(segment["duration_s"], path + ".duration_s", props["duration_s"], errors)

    overrides = segment.get("params", {})
    if "params" in segment and not isinstance(overrides, dict):
        errors.append("%s.params: must be an object, got %s" % (path, _kind(overrides)))
        return
    if preset_id in presets:
        merged = merged_preset(presets[preset_id], overrides)
        errors.extend(preset_validate._errors(merged, _PRESET_SCHEMA, path))


def _one_frame_rule(segment, index, fps, errors):
    """A segment must span at least one frame at the sequence's fps."""
    if not isinstance(segment, dict) or isinstance(fps, bool) or not isinstance(fps, int):
        return
    duration = segment.get("duration_s")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
        return
    if round(duration * fps) < 1:
        errors.append("segments[%d].duration_s: %r s is shorter than one frame at %d fps"
                      % (index, duration, fps))


def validate_sequence(seq, catalog=None):
    """Return every error in the sequence as a string. An empty list means valid."""
    rule = _SCHEMA
    presets = _PRESETS if catalog is None else index_presets(catalog)
    if not isinstance(seq, dict):
        return ["sequence: must be an object, got %s" % _kind(seq)]

    errors = []
    _fields(seq, "sequence", rule, errors)
    props = rule["properties"]

    for key in ("id", "name", "description"):
        if key in seq:
            _string(seq[key], key, props[key], errors)
    if "fps" in seq:
        _integer(seq["fps"], "fps", props["fps"], errors)

    if "segments" in seq:
        segments = seq["segments"]
        seg_rule = props["segments"]
        if not isinstance(segments, list):
            errors.append("segments: must be a list, got %s" % _kind(segments))
        elif len(segments) < seg_rule.get("minItems", 0):
            errors.append("segments: must have at least %d segment(s), got %d"
                          % (seg_rule["minItems"], len(segments)))
        else:
            for index, segment in enumerate(segments):
                _segment(segment, index, seg_rule["items"], errors, presets)
                _one_frame_rule(segment, index, seq.get("fps"), errors)
    return errors
