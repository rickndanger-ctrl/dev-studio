#!/usr/bin/env python3
"""Check and export a video-studio shot plan (see schema.json).

  python3 shotplan.py check PLAN.json
  python3 shotplan.py export PLAN.json OUTDIR
  python3 shotplan.py timeline PLAN.json --svg OUT.svg

check prints the timeline (start, end, shot, moves) and every rule the plan breaks,
then exits 1 on any error. export runs the same check first, then writes one file
per shot, OUTDIR/<shot id>.json, in the camera-motion JSON format (fps, width, height
and each frame's intrinsics and pose).

The sequences inside a plan are camera-motion sequences, so they are checked and built
with camera_motion.sequences (validate_sequence and build). Stdlib only.
"""

import argparse
import json
import math
import os
import re
import sys
from xml.sax.saxutils import escape

from stillmove._cm import import_camera_motion  # noqa: E402

import_camera_motion()

import camera_motion  # noqa: E402
from camera_motion.export.formats import to_json  # noqa: E402
from camera_motion.presets.validate import load_json  # noqa: E402
from camera_motion.sequences.build import build  # noqa: E402
from camera_motion.sequences.validate import index_presets, validate_sequence  # noqa: E402

CATALOG = os.path.join(os.path.dirname(camera_motion.__file__), "presets", "catalog.json")
ID = re.compile(r"^[a-z][a-z0-9_]*$")
PLACEHOLDER = "placeholder:"
PLAN_KEYS = {"id", "name", "fps", "width", "height", "shots"}
SHOT_KEYS = {"id", "image", "sequence", "duration", "transition", "notes"}
SHOT_REQUIRED = {"id", "image", "sequence", "duration", "transition"}
MAX_DURATION_S = 600


def _is_int(value, lo, hi):
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _reject_constant(name):
    raise ValueError("%s is not allowed (numbers must be finite)" % name)


def _shape_errors_shot(shot, index, errors):
    where = "shots[%d]" % index
    if not isinstance(shot, dict):
        errors.append("%s: must be an object" % where)
        return
    for key in sorted(SHOT_REQUIRED - set(shot)):
        errors.append("%s: missing required field '%s'" % (where, key))
    for key in sorted(set(shot) - SHOT_KEYS):
        errors.append("%s: unknown field '%s'" % (where, key))
    if "id" in shot and not (isinstance(shot["id"], str) and ID.match(shot["id"])):
        errors.append("%s.id: must be snake_case, got %r" % (where, shot["id"]))
    if "image" in shot and not (isinstance(shot["image"], str) and shot["image"]):
        errors.append("%s.image: must be a non-empty path or placeholder:<label>" % where)
    if "sequence" in shot:
        seq = shot["sequence"]
        if isinstance(seq, str):
            if not seq:
                errors.append("%s.sequence: path must not be empty" % where)
        elif isinstance(seq, dict):
            if set(seq) != {"segments"} or not isinstance(seq.get("segments"), list) or not seq["segments"]:
                errors.append("%s.sequence: an inline sequence is {\"segments\": [...]} with at least one segment" % where)
        else:
            errors.append("%s.sequence: must be a path or {\"segments\": [...]}" % where)
    if "duration" in shot and not (_is_num(shot["duration"]) and 0 < shot["duration"] <= MAX_DURATION_S):
        errors.append("%s.duration: must be a number above 0 and at most %d, got %r"
                      % (where, MAX_DURATION_S, shot["duration"]))
    if "notes" in shot and not isinstance(shot["notes"], str):
        errors.append("%s.notes: must be a string" % where)
    if "transition" in shot:
        tr = shot["transition"]
        if tr == "cut":
            pass
        elif isinstance(tr, dict) and set(tr) == {"crossfade"} and _is_num(tr["crossfade"]) and tr["crossfade"] > 0:
            pass
        else:
            errors.append("%s.transition: must be \"cut\" or {\"crossfade\": seconds above 0}" % where)


def shape_errors(plan):
    """The rules JSON Schema states for the plan's fields (types, ranges, required keys)."""
    errors = []
    if not isinstance(plan, dict):
        return ["plan: must be a JSON object"]
    for key in sorted(PLAN_KEYS - set(plan)):
        errors.append("plan: missing required field '%s'" % key)
    for key in sorted(set(plan) - PLAN_KEYS):
        errors.append("plan: unknown field '%s'" % key)
    if "id" in plan and not (isinstance(plan["id"], str) and ID.match(plan["id"])):
        errors.append("plan.id: must be snake_case, got %r" % (plan["id"],))
    if "name" in plan and not (isinstance(plan["name"], str) and plan["name"]):
        errors.append("plan.name: must be a non-empty string")
    if "fps" in plan and not _is_int(plan["fps"], 1, 120):
        errors.append("plan.fps: must be an integer from 1 to 120, got %r" % (plan["fps"],))
    for key in ("width", "height"):
        if key in plan and not _is_int(plan[key], 16, 8192):
            errors.append("plan.%s: must be an integer from 16 to 8192, got %r" % (key, plan[key]))
    if "shots" in plan:
        shots = plan["shots"]
        if not isinstance(shots, list) or not shots:
            errors.append("plan.shots: must be a non-empty list")
        else:
            for index, shot in enumerate(shots):
                _shape_errors_shot(shot, index, errors)
    return errors


def _resolve_sequence(shot, index, plan, plan_dir, errors):
    """The shot's sequence as a camera-motion sequence dict, or None (with errors recorded)."""
    where = "shots[%d]" % index
    fps = plan["fps"]
    seq = shot["sequence"]
    if isinstance(seq, dict):
        return {"id": shot["id"], "name": shot["id"], "description": "inline sequence of shot %s" % shot["id"],
                "fps": fps, "segments": seq["segments"]}
    if os.path.isabs(seq):
        errors.append("%s.sequence: a sequence path must be relative to the plan, got %r" % (where, seq))
        return None
    path = os.path.join(plan_dir, seq)
    if not os.path.isfile(path):
        errors.append("%s.sequence: no sequence file at %s" % (where, seq))
        return None
    try:
        loaded = load_json(path)
    except (OSError, ValueError) as exc:
        errors.append("%s.sequence: %s: %s" % (where, seq, exc))
        return None
    if not isinstance(loaded, dict):
        errors.append("%s.sequence: %s must hold a sequence object" % (where, seq))
        return None
    if loaded.get("fps") != fps:
        errors.append("%s.sequence: %s has fps %r but the plan is %r" % (where, seq, loaded.get("fps"), fps))
    return loaded


def check(plan, plan_dir, catalog=None):
    """Return (errors, rows, sequences). rows are (start, end, shot id, moves, transition).

    sequences maps each shot id to its validated camera-motion sequence (empty if any error).
    """
    errors = shape_errors(plan)
    if errors:
        return errors, [], {}
    catalog = load_json(CATALOG) if catalog is None else catalog  # the raw catalog, as build() takes it
    fps = plan["fps"]
    half_frame = 0.5 / fps
    shots = plan["shots"]

    ids = {}
    for index, shot in enumerate(shots):
        if shot["id"] in ids:
            errors.append("shots[%d].id: duplicate shot id '%s' (already used by shots[%d])"
                          % (index, shot["id"], ids[shot["id"]]))
        else:
            ids[shot["id"]] = index

    sequences, durations, crossfades = {}, [], []
    for index, shot in enumerate(shots):
        where = "shots[%d]" % index
        seq = _resolve_sequence(shot, index, plan, plan_dir, errors)
        total = None
        if seq is not None:
            seq_errors = validate_sequence(seq, catalog)
            errors.extend("%s: %s" % (where, msg) for msg in seq_errors)
            if not seq_errors:
                try:
                    build(seq, catalog)
                    sequences[shot["id"]] = seq
                    total = sum(segment["duration_s"] for segment in seq["segments"])
                except ValueError as exc:
                    errors.append("%s: sequence does not build: %s" % (where, exc))
        if total is not None and abs(shot["duration"] - total) > half_frame + 1e-9:
            errors.append("%s.duration: %.3f s does not match its sequence's %.3f s (more than half a frame)"
                          % (where, shot["duration"], total))

        image = shot["image"]
        if image.startswith(PLACEHOLDER):
            if not image[len(PLACEHOLDER):].strip():
                errors.append("%s.image: placeholder needs a label, e.g. placeholder:title card" % where)
        elif os.path.isabs(image) or not os.path.isfile(os.path.join(plan_dir, image)):
            errors.append("%s.image: no file at %s (paths are relative to the plan)" % (where, image))

        tr = shot["transition"]
        crossfades.append(tr["crossfade"] if isinstance(tr, dict) else 0.0)
        durations.append(shot["duration"])

    last = len(shots) - 1
    if shots and isinstance(shots[last]["transition"], dict):
        errors.append("shots[%d].transition: the last shot must be a cut" % last)
    for index in range(last):
        cf = crossfades[index]
        if cf <= 0:
            continue
        if cf >= durations[index] or cf >= durations[index + 1]:
            errors.append("shots[%d].transition: crossfade %.3f s must be shorter than this shot (%.3f s) "
                          "and the next (%.3f s)" % (index, cf, durations[index], durations[index + 1]))

    starts = [0.0]
    for index in range(last):
        starts.append(starts[index] + durations[index] - crossfades[index])
    ends = [starts[i] + durations[i] for i in range(len(shots))]
    for i in range(len(shots)):
        for j in range(i + 2, len(shots)):
            if starts[j] < ends[i] - 1e-9:
                errors.append("shots[%d] overlaps shots[%d] beyond its crossfade" % (j, i))

    rows = []
    for i, shot in enumerate(shots):
        seq = sequences.get(shot["id"])
        moves = " > ".join(move_text(seg) for seg in seq["segments"]) if seq is not None else "?"
        transition = "cut" if crossfades[i] == 0 else "crossfade %.2f s" % crossfades[i]
        rows.append((starts[i], ends[i], shot["id"], moves, transition))
    return errors, rows, sequences


def move_text(segment):
    """'dolly_in 2s': a segment's preset and duration, as the table and the timeline show it."""
    return "%s %gs" % (segment["preset"], segment["duration_s"])


def print_table(rows):
    print("%8s  %8s  %-20s  %-s  %s" % ("start", "end", "shot", "moves", "transition"))
    for start, end, shot_id, moves, transition in rows:
        print("%8.3f  %8.3f  %-20s  %s  %s" % (start, end, shot_id, moves, transition))


def load_plan(path):
    """Return (plan, plan_dir, error) for a plan file."""
    plan_dir = os.path.dirname(os.path.abspath(path))
    try:
        with open(path, encoding="utf-8") as handle:
            plan = json.load(handle, parse_constant=_reject_constant)
    except (OSError, ValueError) as exc:
        return None, plan_dir, str(exc)
    return plan, plan_dir, None


def cmd_check(args):
    plan, plan_dir, error = load_plan(args.plan)
    if error:
        print("error: %s: %s" % (args.plan, error), file=sys.stderr)
        return 1
    errors, rows, _ = check(plan, plan_dir)
    if rows:
        print_table(rows)
    for message in errors:
        print("error: %s" % message, file=sys.stderr)
    if errors:
        print("%d error(s) in %s" % (len(errors), args.plan), file=sys.stderr)
        return 1
    print("OK: %d shot(s), %.3f s, in %s" % (len(rows), rows[-1][1] if rows else 0.0, args.plan))
    return 0


def cmd_export(args):
    plan, plan_dir, error = load_plan(args.plan)
    if error:
        print("error: %s: %s" % (args.plan, error), file=sys.stderr)
        return 1
    errors, _, sequences = check(plan, plan_dir)
    if errors:
        for message in errors:
            print("error: %s" % message, file=sys.stderr)
        print("%d error(s); nothing exported" % len(errors), file=sys.stderr)
        return 1
    catalog = load_json(CATALOG)
    os.makedirs(args.outdir, exist_ok=True)
    for shot in plan["shots"]:
        frames = build(sequences[shot["id"]], catalog)
        data = to_json(frames, width=plan["width"], height=plan["height"], fps=plan["fps"])
        out = os.path.join(args.outdir, shot["id"] + ".json")
        with open(out, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        print("wrote %s (%d frames)" % (out, len(frames)))
    return 0


def cmd_serve(args):
    from shotplan.web.server import serve  # the page server: stdlib only; it never imports numpy

    return serve(args.port)


def cmd_render(args):
    plan, plan_dir, error = load_plan(args.plan)
    if error:
        print("error: %s: %s" % (args.plan, error), file=sys.stderr)
        return 1
    errors, rows, sequences = check(plan, plan_dir)
    if errors:
        for message in errors:
            print("error: %s" % message, file=sys.stderr)
        print("%d error(s); nothing rendered" % len(errors), file=sys.stderr)
        return 1
    try:
        from shotplan import shotrender  # stdlib at import; numpy, Pillow and stillmove load inside render_plan
        manifest = shotrender.render_plan(plan, plan_dir, args.outdir, rows, sequences, load_json(CATALOG),
                                          depth=args.depth, scale=args.scale, mp4=args.mp4)
    except (ValueError, RuntimeError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("rendered %d frame(s) at %d fps to %s (gif: %s)" % (manifest["frames"], manifest["fps"],
                                                               args.outdir, manifest["gif"]))
    if manifest.get("note"):
        print("note: %s" % manifest["note"], file=sys.stderr)
    return 0


TYPE_ORDER = ("dolly", "dolly_zoom", "crane", "orbit", "pan", "tilt")
TYPE_COLORS = {"dolly": "#9ecae1", "dolly_zoom": "#fdae6b", "crane": "#a1d99b",
               "orbit": "#d0bfff", "pan": "#ffe38f", "tilt": "#f4b6b6"}
UNKNOWN_COLOR = "#dddddd"
WIDTH, LEFT, RIGHT, TOP = 960, 170, 24, 56
LANE_H, BLOCK_H = 52, 26
CHAR_W = 6.6  # rough width of 11 px sans-serif text, in px


def _nice_step(total):
    """A tick spacing in seconds that gives at most 12 ticks."""
    for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
        if total / step <= 12:
            return step
    return 600


def _fit(text, width):
    """The text cut to fit width px with '...', or '' when fewer than four characters fit."""
    room = int((width - 8) / CHAR_W)
    if len(text) <= room:
        return text
    if room < 4:
        return ""
    return text[: room - 3] + "..."


def timeline_svg(plan, rows, sequences, catalog):
    """An SVG of a checked plan: one lane per shot at its start and end, one block per move
    coloured by preset type, and each crossfade shaded across the lanes."""
    presets = catalog if isinstance(catalog, list) else catalog["presets"]
    preset_type = {p["id"]: p["type"] for p in presets}
    shots = plan["shots"]
    total = max(end for _, end, _, _, _ in rows)
    plot_w = WIDTH - LEFT - RIGHT
    n = len(rows)
    axis_y = TOP + n * LANE_H + 8
    legend_y = axis_y + 44
    height = legend_y + 40

    def x_of(t):
        return LEFT + t * plot_w / total

    def esc(text):
        return escape(str(text), {'"': "&quot;"})

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
           'font-family="Helvetica, Arial, sans-serif">' % (WIDTH, height, WIDTH, height),
           '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (WIDTH, height),
           '<text x="10" y="26" font-size="15" font-weight="bold">%s timeline: %d shot(s), %.2f s</text>'
           % (esc(plan["name"]), n, total)]

    for i in range(n):  # lane stripes
        y = TOP + i * LANE_H
        out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>'
                   % (LEFT, y, plot_w, LANE_H, "#fafafa" if i % 2 == 0 else "#f1f1f1"))

    step = _nice_step(total)
    tick = 0.0
    while tick <= total + 1e-9:  # grid lines
        out.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" stroke="#e0e0e0"/>'
                   % (x_of(tick), TOP, x_of(tick), axis_y))
        tick += step

    crossfades = []
    for i in range(n - 1):  # the overlap of shot i and shot i+1
        tr = shots[i]["transition"]
        if isinstance(tr, dict):
            band_x0, band_x1 = x_of(rows[i + 1][0]), x_of(rows[i][1])
            crossfades.append((tr["crossfade"], band_x0, band_x1))
            out.append('<rect class="crossfade" x="%.2f" y="%d" width="%.2f" height="%d" fill="#555555" '
                       'fill-opacity="0.22"><title>%s</title></rect>'
                       % (band_x0, TOP, band_x1 - band_x0, n * LANE_H, esc("crossfade %.2f s" % tr["crossfade"])))
            label = _fit("fade %gs" % tr["crossfade"], band_x1 - band_x0)
            if label:
                out.append('<text x="%.2f" y="%d" font-size="10" text-anchor="middle" fill="#333333">%s</text>'
                           % ((band_x0 + band_x1) / 2, TOP - 8, esc(label)))

    for i, (start, end, shot_id, _, _) in enumerate(rows):
        lane_y = TOP + i * LANE_H
        out.append('<rect class="span" x="%.2f" y="%d" width="%.2f" height="%d" fill="none" '
                   'stroke="#999999" stroke-dasharray="3 3"/>'
                   % (x_of(start), lane_y + 3, x_of(end) - x_of(start), LANE_H - 6))
        seq = sequences.get(shot_id)
        offset = start
        for seg in (seq["segments"] if seq is not None else []):
            ptype = preset_type.get(seg["preset"], "")
            color = TYPE_COLORS.get(ptype, UNKNOWN_COLOR)
            x0, x1 = x_of(offset), x_of(offset + seg["duration_s"])
            label = move_text(seg)
            out.append('<rect class="move" x="%.2f" y="%d" width="%.2f" height="%d" fill="%s" '
                       'stroke="#333333" stroke-width="0.6"><title>%s (%s)</title></rect>'
                       % (x0, lane_y + (LANE_H - BLOCK_H) / 2, x1 - x0, BLOCK_H, color,
                          esc(label), esc(ptype or "unknown type")))
            text = _fit(label, x1 - x0)
            if text:
                out.append('<text x="%.2f" y="%.2f" font-size="11" fill="#1b1b1b">%s</text>'
                           % (x0 + 4, lane_y + LANE_H / 2 + 4, esc(text)))
            offset += seg["duration_s"]
        out.append('<text x="10" y="%d" font-size="13" font-weight="bold">%s</text>'
                   % (lane_y + 22, esc(shot_id)))
        out.append('<text x="10" y="%d" font-size="10" fill="#555555">%.2f to %.2f s</text>'
                   % (lane_y + 38, start, end))

    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333333"/>'
               % (LEFT, axis_y, LEFT + plot_w, axis_y))
    tick = 0.0
    while tick <= total + 1e-9:
        out.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" stroke="#333333"/>'
                   % (x_of(tick), axis_y, x_of(tick), axis_y + 4))
        out.append('<text x="%.2f" y="%d" font-size="10" text-anchor="middle">%s</text>'
                   % (x_of(tick), axis_y + 16, esc("%gs" % tick)))
        tick += step

    used = {preset_type.get(seg["preset"], "") for seq in sequences.values() for seg in seq["segments"]}
    legend = [(t, TYPE_COLORS[t], t) for t in TYPE_ORDER if t in used]
    if any(t not in TYPE_COLORS for t in used):
        legend.append(("unknown", UNKNOWN_COLOR, "unknown type"))
    if crossfades:
        legend.append(("crossfade", "#555555", "crossfade"))
    x = LEFT
    for kind, color, label in legend:
        if kind == "crossfade":
            out.append('<rect class="swatch" x="%d" y="%d" width="14" height="14" fill="#555555" '
                       'fill-opacity="0.22" stroke="#555555"/>' % (x, legend_y))
        else:
            out.append('<rect class="swatch" x="%d" y="%d" width="14" height="14" fill="%s" stroke="#333333" '
                       'stroke-width="0.6"/>' % (x, legend_y, color))
        out.append('<text x="%d" y="%d" font-size="11">%s</text>' % (x + 20, legend_y + 11, esc(label)))
        x += 20 + CHAR_W * len(label) + 22
    out.append("</svg>")
    return "\n".join(out) + "\n"


def cmd_timeline(args):
    plan, plan_dir, error = load_plan(args.plan)
    if error:
        print("error: %s: %s" % (args.plan, error), file=sys.stderr)
        return 1
    errors, rows, sequences = check(plan, plan_dir)
    if errors:
        for message in errors:
            print("error: %s" % message, file=sys.stderr)
        print("%d error(s); no timeline written" % len(errors), file=sys.stderr)
        return 1
    svg = timeline_svg(plan, rows, sequences, load_json(CATALOG))
    with open(args.svg, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg)
    print("wrote %s (%d shot(s), %.2f s)" % (args.svg, len(rows), rows[-1][1]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check and export a video-studio shot plan.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="check the plan and print its timeline")
    p_check.add_argument("plan")
    p_check.set_defaults(run=cmd_check)
    p_export = sub.add_parser("export", help="check, then write one camera-path JSON per shot")
    p_export.add_argument("plan")
    p_export.add_argument("outdir")
    p_export.set_defaults(run=cmd_export)
    p_serve = sub.add_parser("serve", help="serve the shot-plan page on 127.0.0.1 (stdlib only)")
    p_serve.add_argument("--port", type=int, default=8810)
    p_serve.set_defaults(run=cmd_serve)
    p_render = sub.add_parser("render", help="check, then render every shot with stillmove and join them")
    p_render.add_argument("plan")
    p_render.add_argument("outdir")
    p_render.add_argument("--depth", default="fake", choices=("fake", "plane", "gradient", "da2-small"),
                          help="depth backend: fake (stillmove's plane, the default), plane, gradient, da2-small")
    p_render.add_argument("--scale", type=float, default=1.0, help="render at this share of the plan size")
    p_render.add_argument("--mp4", action="store_true", help="also write preview.mp4 if ffmpeg is on PATH")
    p_render.set_defaults(run=cmd_render)
    p_timeline = sub.add_parser("timeline", help="check, then write an SVG of the shots, moves and crossfades")
    p_timeline.add_argument("plan")
    p_timeline.add_argument("--svg", required=True, metavar="OUT", help="output SVG file")
    p_timeline.set_defaults(run=cmd_timeline)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
