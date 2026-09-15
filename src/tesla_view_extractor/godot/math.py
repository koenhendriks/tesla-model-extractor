"""Transform helpers: Godot `Transform` (3x3 basis rows + origin) ↔ column-major 4x4 ↔ (position, quaternion, scale)."""

from __future__ import annotations

import math


def transform_to_mat4(t: list[float]) -> list[float]:
    """Godot `Transform( xx,xy,xz, yx,yy,yz, zx,zy,zz, ox,oy,oz )` → 16 floats column-major for THREE.Matrix4.fromArray."""
    a, b, c, d, e, f, g, h, i, x, y, z = t
    return [a, d, g, 0.0, b, e, h, 0.0, c, f, i, 0.0, x, y, z, 1.0]


def mat4_origin(m: list[float]) -> list[float]:
    return [m[12], m[13], m[14]]


def basis_to_quat(m: list[list[float]]) -> list[float]:
    """3x3 row-major orthonormal basis → quaternion [x, y, z, w]."""
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return [(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s]
    if m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        return [0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s]
    if m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        return [(m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s]
    s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
    return [(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s]


def decompose_transform(t: list[float]) -> tuple[list[float], list[float], list[float]]:
    """Godot Transform 12 floats → (position, quaternion[x,y,z,w], scale). Handles negative determinant (mirroring)."""
    f = list(t)
    m = [f[0:3], f[3:6], f[6:9]]
    sx = math.sqrt(m[0][0] ** 2 + m[1][0] ** 2 + m[2][0] ** 2)
    sy = math.sqrt(m[0][1] ** 2 + m[1][1] ** 2 + m[2][1] ** 2)
    sz = math.sqrt(m[0][2] ** 2 + m[1][2] ** 2 + m[2][2] ** 2)
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )
    if det < 0:
        sx = -sx
    scale = [sx or 1.0, sy or 1.0, sz or 1.0]
    mn = [[m[r][c] / scale[c] for c in range(3)] for r in range(3)]
    return f[9:12], basis_to_quat(mn), [sx, sy, sz]


def round_floats(values: list[float], ndigits: int = 6) -> list[float]:
    return [round(v, ndigits) + 0.0 for v in values]
