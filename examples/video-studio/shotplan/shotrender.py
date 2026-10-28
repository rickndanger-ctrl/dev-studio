"""Render a checked shot plan to one timeline: stills moved by stillmove, then joined.

Per shot: a placeholder image (or the plan's file), its camera path exported at the render
size, and a still-to-motion render with stillmove's depth backend and warp. Frames are
written one at a time, so only two frames are ever in memory.

Then the join: N = round(total * fps) frames, frame k at t = k / fps. A shot frame is
round((t - start) * fps), clamped. Where one shot hands over to the next with a crossfade,
the two frames are blended linearly across the overlap.

numpy, Pillow and stillmove are imported only inside the functions that need them, so the
rest of shotplan stays stdlib.
"""

import hashlib
import json
import math
import os
import shutil
import subprocess

PLACEHOLDER = "placeholder:"
# shotplan's --depth names. "fake" is the stillmove fake depth used by default ("plane").
DEPTH_NAMES = {"fake": "plane"}
DEPTH_CHOICES = ("fake", "plane", "gradient", "da2-small")


def pixel_size(plan, scale):
    """The render size: the plan's width and height times scale, at least 2 pixels each."""
    return max(2, round(plan["width"] * scale)), max(2, round(plan["height"] * scale))


def placeholder_image(label, width, height, path):
    """A flat image in a colour that is fixed by the label, with the label drawn on it."""
    from PIL import Image, ImageDraw

    digest = hashlib.sha256(label.encode("utf-8")).digest()
    colour = tuple(int(c) // 2 + 64 for c in digest[:3])
    img = Image.new("RGB", (width, height), colour)
    draw = ImageDraw.Draw(img)
    left, top, right, bottom = draw.textbbox((0, 0), label)
    text_w, text_h = right - left, bottom - top
    draw.text(((width - text_w) / 2 - left, (height - text_h) / 2 - top), label, fill=(255, 255, 255))
    img.save(path)


def frame_index(t, start, fps, count):
    """The shot frame shown at time t: round((t - start) * fps), clamped to the shot's frames."""
    index = math.floor((t - start) * fps + 0.5)
    return min(max(index, 0), count - 1)


def pick(t, starts, ends):
    """Which shots show at t: ('cut', i) or ('blend', i, i + 1, alpha) for a crossfade overlap."""
    for i in range(len(starts) - 1):
        overlap_start, overlap_end = starts[i + 1], ends[i]
        if overlap_end - overlap_start > 1e-9 and overlap_start <= t < overlap_end:
            return ("blend", i, i + 1, (t - overlap_start) / (overlap_end - overlap_start))
    for i in range(len(starts)):
        if t < ends[i] or i == len(starts) - 1:
            return ("cut", i)
    raise AssertionError("unreachable: the last shot always matches")


def _load_rgb(path):
    import numpy as np
    from PIL import Image

    with Image.open(path) as img:
        return np.asarray(img.convert("RGB"), dtype=np.float64)


def render_shot(image_path, cam_path, backend, sdir):
    """Move one still along its camera path. Returns the frame count.

    The loop is stillmove.render.render_frames, the same one stillmove render uses: depth is
    smoothed once, then every frame is warped with the backward warp.
    """
    from stillmove import cameras
    from stillmove.depth import check_depth
    from stillmove.render import _load_image, render_frames

    cams = cameras.load(cam_path)
    image = _load_image(image_path, cams.width, cams.height)
    depth = check_depth(backend.depth(image), image.shape[:2])
    return render_frames(image, depth, cams, os.path.join(sdir, "frames"))


def _write_gif_pillow(outdir, count, fps):
    from PIL import Image
    from stillmove.render import frame_durations_ms

    def frames():
        for k in range(1, count):
            yield Image.open(os.path.join(outdir, "frames", "%04d.png" % k)).convert("RGB")

    first = Image.open(os.path.join(outdir, "frames", "0000.png")).convert("RGB")
    first.save(os.path.join(outdir, "preview.gif"), save_all=True, append_images=frames(),
               duration=frame_durations_ms(count, fps), loop=0)
    return "preview.gif"


def _write_gif_ffmpeg(outdir, fps):
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps),
        "-i", os.path.join(outdir, "frames", "%04d.png"),
        "-vf", "split[a][b];[a]palettegen[p];[b][p]paletteuse",
        os.path.join(outdir, "preview.gif"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed: %s" % result.stderr.strip())
    return "preview.gif"


def join(outdir, rows, shot_counts, fps):
    """Write outdir/frames/NNNN.png, the joined timeline. Returns the frame count N.

    rows are (start, end, shot id, ...) from shotplan.check. shot_counts maps shot id to its
    rendered frame count.
    """
    from PIL import Image

    starts = [row[0] for row in rows]
    ends = [row[1] for row in rows]
    ids = [row[2] for row in rows]
    total = max(ends)
    count = math.floor(total * fps + 0.5)
    frames_dir = os.path.join(outdir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    def shot_frame(s, t):
        return _load_rgb(os.path.join(outdir, "shots", ids[s], "frames",
                                      "%04d.png" % frame_index(t, starts[s], fps, shot_counts[ids[s]])))

    for k in range(count):
        t = k / fps
        choice = pick(t, starts, ends)
        if choice[0] == "cut":
            picture = shot_frame(choice[1], t)
        else:
            _, a, b, alpha = choice
            picture = (1.0 - alpha) * shot_frame(a, t) + alpha * shot_frame(b, t)
        out = picture.round().clip(0, 255).astype("uint8")
        Image.fromarray(out).save(os.path.join(frames_dir, "%04d.png" % k))
    return count


def render_plan(plan, plan_dir, outdir, rows, sequences, catalog, depth="fake", scale=1.0,
                mp4=False, use_ffmpeg=None):
    """Render a checked plan into outdir and return the manifest dict.

    rows and sequences come from shotplan.check(plan, plan_dir). use_ffmpeg None means use
    ffmpeg if it is on PATH; False forces the Pillow GIF writer.
    """
    from stillmove.depth import get_backend
    from camera_motion.export.formats import to_json
    from camera_motion.sequences.build import build

    if depth not in DEPTH_CHOICES:
        raise ValueError("unknown depth %r; use one of %s" % (depth, ", ".join(DEPTH_CHOICES)))
    if scale <= 0:
        raise ValueError("--scale must be greater than 0")
    backend = get_backend(DEPTH_NAMES.get(depth, depth))
    fps = plan["fps"]
    width, height = pixel_size(plan, scale)
    if use_ffmpeg is None:
        use_ffmpeg = shutil.which("ffmpeg") is not None

    os.makedirs(outdir, exist_ok=True)
    shot_counts = {}
    for shot in plan["shots"]:
        sid = shot["id"]
        sdir = os.path.join(outdir, "shots", sid)
        os.makedirs(sdir, exist_ok=True)
        if shot["image"].startswith(PLACEHOLDER):
            image_path = os.path.join(sdir, "input.png")
            placeholder_image(shot["image"][len(PLACEHOLDER):], width, height, image_path)
        else:
            image_path = os.path.join(plan_dir, shot["image"])
        cam_path = os.path.join(sdir, "camera.json")
        frames = build(sequences[sid], catalog)
        with open(cam_path, "w", encoding="utf-8") as handle:
            json.dump(to_json(frames, width=width, height=height, fps=fps), handle)
        shot_counts[sid] = render_shot(image_path, cam_path, backend, sdir)

    count = join(outdir, rows, shot_counts, fps)
    note = None
    if use_ffmpeg:
        gif = _write_gif_ffmpeg(outdir, fps)
    else:
        gif = _write_gif_pillow(outdir, count, fps)
    mp4_name = None
    if mp4:
        if use_ffmpeg:
            from stillmove.render import _encode_mp4
            mp4_name = _encode_mp4(outdir, fps)
        else:
            note = "ffmpeg not found on PATH; mp4 skipped"

    manifest = {
        "id": plan["id"],
        "name": plan["name"],
        "fps": fps,
        "width": width,
        "height": height,
        "scale": scale,
        "depth": backend.name,
        "frames": count,
        "frame_dir": "frames",
        "gif": gif,
        "mp4": mp4_name,
        "shots": [{"id": row[2], "start": row[0], "end": row[1], "frames": shot_counts[row[2]],
                   "dir": os.path.join("shots", row[2])} for row in rows],
    }
    if note:
        manifest["note"] = note
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    return manifest
