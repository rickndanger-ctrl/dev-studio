"""Rotation matrix <-> unit quaternion, stdlib only.

Quaternions are (x, y, z, w). mat_to_quat returns w >= 0 (Shepperd's method).
"""
import math


def mat_to_quat(r):
    """Unit quaternion (x, y, z, w) with w >= 0 for a 3x3 rotation matrix r."""
    m00, m01, m02 = r[0]
    m10, m11, m12 = r[1]
    m20, m21, m22 = r[2]
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    return (x / norm, y / norm, z / norm, w / norm)


def quat_to_mat(q):
    """3x3 rotation matrix for a quaternion (x, y, z, w). The input is normalized first."""
    x, y, z, w = q
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def make_continuous(quats):
    """Flip each quaternion's sign when its dot with the previous one is negative.

    q and -q are the same rotation, so the output keeps every rotation but takes the
    short way between consecutive keys. Works for any component order.
    """
    out = []
    prev = None
    for q in quats:
        q = tuple(q)
        if prev is not None and sum(a * b for a, b in zip(q, prev)) < 0:
            q = tuple(-c for c in q)
        out.append(q)
        prev = q
    return out
