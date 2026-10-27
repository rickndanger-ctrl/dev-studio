#!/usr/bin/env python3
"""Check a camera-motion preset catalog against schema.json.

Stdlib only. The rules come from schema.json at run time, so this file
never holds its own copy of them. If schema.json starts using a keyword
this checker does not implement, validation stops with an error instead
of quietly skipping that rule.

Usage:
    python3 presets/validate.py catalog.json
    python3 presets/validate.py catalog.json --schema path/to/schema.json

A catalog is either a JSON list of presets, or an object {"presets": [...]}.

Exit status: 0 when every preset is valid, 1 for any error (bad preset,
duplicate id, unreadable or non-JSON file, unsupported schema keyword).
"""

import argparse
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(SCRIPT_DIR, "schema.json")

# Keywords that describe the schema without constraining a value.
ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description"}
# Every keyword this checker implements.
KEYWORDS = ANNOTATIONS | {
    "type", "enum", "const", "pattern", "minLength", "minimum", "maximum",
    "exclusiveMinimum", "not", "required", "properties", "additionalProperties",
    "allOf", "if", "then",
}
JSON_TYPES = {"object", "string", "number", "array", "boolean", "null"}


class SchemaError(Exception):
    """schema.json uses something this checker does not implement."""


def _reject_constant(name):
    # json accepts NaN and Infinity by default, but they are not JSON and no
    # preset value can be non-finite, so refuse them at parse time.
    raise ValueError("%s is not allowed (numbers must be finite)" % name)


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle, parse_constant=_reject_constant)


def resolve_catalog_path(path):
    """Use the path as given. If it is not found, try the script's own folder."""
    if os.path.exists(path) or os.path.isabs(path):
        return path
    beside_script = os.path.join(SCRIPT_DIR, path)
    return beside_script if os.path.exists(beside_script) else path


def _json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):  # bool is a subclass of int, so test it first
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _check_keywords(schema):
    unknown = set(schema) - KEYWORDS
    if unknown:
        raise SchemaError("keyword(s) not supported by validate.py: %s" % ", ".join(sorted(unknown)))
    if "type" in schema and schema["type"] not in JSON_TYPES:
        raise SchemaError("unsupported type %r" % (schema["type"],))
    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        raise SchemaError("only additionalProperties: false is supported")


def _errors(value, schema, path):
    """Return a list of human-readable errors for value at path. Empty means valid."""
    _check_keywords(schema)
    errors = []

    if "type" in schema and _json_type(value) != schema["type"]:
        return ["%s: must be %s, got %s (%r)" % (path, schema["type"], _json_type(value), value)]

    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: must be one of %s, got %r" % (path, schema["enum"], value))

    if "const" in schema and value != schema["const"]:
        errors.append("%s: must be %r, got %r" % (path, schema["const"], value))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: must be at least %d character(s) long, got %r"
                          % (path, schema["minLength"], value))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append("%s: must match pattern %s, got %r" % (path, schema["pattern"], value))

    if _json_type(value) == "number":
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("%s: must be >= %s, got %r" % (path, schema["minimum"], value))
        if "maximum" in schema and value > schema["maximum"]:
            errors.append("%s: must be <= %s, got %r" % (path, schema["maximum"], value))
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append("%s: must be > %s, got %r" % (path, schema["exclusiveMinimum"], value))

    if "not" in schema and not _errors(value, schema["not"], path):
        forbidden = schema["not"]
        if "const" in forbidden:
            errors.append("%s: must not be %r" % (path, forbidden["const"]))
        else:
            errors.append("%s: value %r matches a forbidden form" % (path, value))

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing required field '%s'" % (path, key))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append("%s: unknown field '%s' (allowed: %s)"
                                  % (path, key, ", ".join(sorted(props)) or "none"))
        for key, sub in props.items():
            if key in value:
                errors.extend(_errors(value[key], sub, "%s.%s" % (path, key)))

    for sub in schema.get("allOf", []):
        errors.extend(_errors(value, sub, path))

    if "if" in schema and not _errors(value, schema["if"], path):
        errors.extend(_errors(value, schema.get("then", {}), path))

    return errors


def _presets_of(catalog):
    """Return the preset list from either catalog form, or None if the shape is wrong."""
    if isinstance(catalog, list):
        return catalog
    if isinstance(catalog, dict):
        return catalog.get("presets")
    return None


def validate_catalog(catalog, schema):
    """Return every error in the catalog as a string. An empty list means valid.

    Raises SchemaError if schema.json uses a keyword this checker does not implement.
    """
    errors = []

    if isinstance(catalog, dict):
        for key in catalog:
            if key != "presets":
                errors.append("catalog: unknown field '%s' (only 'presets' is allowed)" % key)
    elif not isinstance(catalog, list):
        return ["catalog: must be a list of presets or an object with a 'presets' list"]

    presets = _presets_of(catalog)
    if not isinstance(presets, list):
        return errors + ["presets: must be a list"]
    if not presets:
        errors.append("presets: catalog is empty")

    first_index = {}
    for index, preset in enumerate(presets):
        path = "presets[%d]" % index
        errors.extend(_errors(preset, schema, path))
        preset_id = preset.get("id") if isinstance(preset, dict) else None
        if isinstance(preset_id, str):
            if preset_id in first_index:
                errors.append("%s.id: duplicate id '%s' (already used by presets[%d])"
                              % (path, preset_id, first_index[preset_id]))
            else:
                first_index[preset_id] = index
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a camera-motion preset catalog.")
    parser.add_argument("catalog", help="path to the catalog JSON file")
    parser.add_argument("--schema", default=SCHEMA_PATH, help="schema file (default: schema.json next to this script)")
    args = parser.parse_args(argv)
    catalog_path = resolve_catalog_path(args.catalog)

    try:
        schema = load_json(args.schema)
        catalog = load_json(catalog_path)
    except (OSError, ValueError) as exc:
        print("error: %s: %s" % (catalog_path, exc), file=sys.stderr)
        return 1

    try:
        errors = validate_catalog(catalog, schema)
    except SchemaError as exc:
        print("error: schema.json: %s" % exc, file=sys.stderr)
        return 1

    if errors:
        for message in errors:
            print("error: %s" % message, file=sys.stderr)
        print("%d error(s) in %s" % (len(errors), catalog_path), file=sys.stderr)
        return 1

    print("OK: %d preset(s) valid in %s" % (len(_presets_of(catalog)), catalog_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
