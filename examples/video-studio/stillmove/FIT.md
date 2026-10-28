# Keep the subject in frame: contract

Problem: a move that pushes or swings too far lets the subject touch or leave the frame edge.
Fix: before rendering, shorten the planned move just enough that the subject stays at least a margin
inside the frame on every frame, and say by how much. Every render path uses the same code.

## `stillmove/fit.py` (worker-1)

- `scale_cameras(cams, s) -> Cameras` for 0 <= s <= 1: frame 0 unchanged; for every frame i the
  camera-to-world pose is moved toward frame 0's by s: position `p0 + s*(pi - p0)`, rotation
  `R0 * exp(s * log(R0^T Ri))` (axis-angle scaled), focal lengths `f0 + s*(fi - f0)`. s = 1 returns
  the same frames; s = 0 holds frame 0 for the whole clip. Frame count and times never change.
- `subject_points(depth, alpha=None, fg_depth=None, step=4) -> (N, 3) array | None`, in frame-0
  OpenCV camera coordinates. With alpha: pixels with alpha > 0.5, sampled every `step` px (always
  including the mask's outline), at `fg_depth` when given (the flat layer) else at their own depth.
  Without alpha: the "depth foreground" = pixels nearer than the 25th percentile of depth, only if
  that is clearly nearer than the median (at least 5% nearer); otherwise None (no subject: a flat
  depth map has nothing to keep in frame).
- `fit_move(cams, points, margin=0.06) -> Fit(scale, worst_frame, min_gap, note)`: project the points
  through every frame of `scale_cameras(cams, s)`; the subject's bounding box must stay at least
  `margin*width` from the left/right edges and `margin*height` from top/bottom on every frame. Find
  the largest s in [0, 1] (bisection to 0.005) that keeps it. Cases:
  - fits at s = 1: scale 1.0, note "".
  - fits at some s < 1: that s, note e.g. "move shortened to 83% to keep the subject 6% inside the frame".
  - does not fit even at s = 0 (the subject already crosses the margin in the first frame): scale 1.0,
    note "subject is already within 6% of the edge in the first frame; move left as planned".
  - points None: scale 1.0, note "" (nothing to keep in frame).

## Wiring (worker-2)

- `stillmove render`: compute the depth (and the matte with --layers) first, then
  `fit_move`, then render `scale_cameras(cams, fit.scale)`. With --layers the points sit at the
  foreground plane depth used for the layer. Flags `--no-fit` and `--fit-margin F` (default 0.06,
  0 < F < 0.4). Print the note; the manifest records `fit: {scale, margin, note}` (null with --no-fit).
- `stillmove mesh`: the same fit for the camera keyed into scene.py (points = mesh vertices of the
  subject when a matte is given, else the depth foreground); same flags; note printed.
- `shotplan render`: per shot, the same fit (layered shots use their matte; plain shots use the depth
  foreground); `--no-fit` / `--fit-margin`; each shot's note and scale go into the manifest.
- The Make a clip / promo page shows the note under the finished video when it is not empty.
