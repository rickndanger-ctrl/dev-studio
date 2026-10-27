"""Easing curves for camera moves. Each maps u in [0, 1] to a progress value in [0, 1]."""


def _linear(u):
    return u


def _ease_in(u):
    return u * u * u


def _ease_out(u):
    return 1.0 - (1.0 - u) ** 3


def _ease_in_out(u):
    if u < 0.5:
        return 4.0 * u * u * u
    return 1.0 - (-2.0 * u + 2.0) ** 3 / 2.0


_CURVES = {
    "linear": _linear,
    "ease_in_out": _ease_in_out,
    "ease_in": _ease_in,
    "ease_out": _ease_out,
}


def ease(name, u):
    """Return the eased progress for u, using the curve called name."""
    try:
        curve = _CURVES[name]
    except KeyError:
        raise ValueError(f"unknown easing: {name!r}") from None
    return curve(u)
