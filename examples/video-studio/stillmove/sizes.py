"""Output shapes for stillmove render --sizes and makeclip: id -> (width, height) in pixels.

Read SIZES at call time (sizes.SIZES) so tests can swap in small frames.
"""

SIZES = {"square": (1080, 1080), "wide": (1920, 1080), "vertical": (1080, 1920)}
