"""Keep the subject in frame: the one place every render path asks for its camera (FIT.md, Wiring).

fit.py works out how far a move may go. This module turns that answer into the camera to render and
the record a manifest keeps, so stillmove render, stillmove mesh and shotplan render agree.
"""
from stillmove import fit

DEFAULT_MARGIN = 0.06


def check_margin(margin):
    """A margin must be more than 0 and less than 0.4 (the same rule for every flag)."""
    if not (0 < margin < 0.4):
        raise ValueError("--fit-margin must be greater than 0 and less than 0.4")
    return margin


def fitted(cams, depth, alpha=None, fg_depth=None, margin=None):
    """Return (camera path to render, fit record).

    margin None means no fit: the path comes back unchanged and the record is None. Otherwise the
    record is {"scale", "margin", "note"}, the manifest's fit field. The subject is the matte's
    pixels (alpha above one half) at fg_depth when a layer is drawn, else the depth foreground.
    """
    if margin is None:
        return cams, None
    check_margin(margin)
    points = fit.subject_points(depth, alpha=alpha, fg_depth=fg_depth, K=cams.frames[0].K)
    result = fit.fit_move(cams, points, margin=margin)
    record = {"scale": result.scale, "margin": margin, "note": result.note}
    return fit.scale_cameras(cams, result.scale), record
