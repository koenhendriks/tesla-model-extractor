"""Godot 3 `Animation` resources → plain JSON keyframes for three.js.

Output: {"name", "length", "tracks": [{"nodePath", "type": "transform", "keys": [{"time", "position", "quaternion", "scale"}]}]}
Handles `transform` tracks (PoolRealArray, 12 floats per key: time, transition, pos xyz, quat xyzw, scale xyz) and
`value` tracks whose property is `:transform` (dict with "times" + "values" of `Transform(...)`), which is how the
app's inline TrunkAnimation drives the strut nodes. Other track kinds are skipped with a warning.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..godot.math import decompose_transform
from ..godot.text import GodotFile
from ..godot.values import node_path, pool_floats, transform12, vector3

NOTE = (
    "Godot 3 units (metres), Y-up, quaternion order [x,y,z,w]. Time 0 = closed, time=length = fully open. "
    "The app plays forward to open and backwards (negative speed) to close (Vehicle.gd toggle_open)."
)


@dataclass
class AnimResult:
    data: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


VECTOR_PROPS = {"translation": "position", "rotation_degrees": "rotation_deg", "scale": "scale"}


def quat_from_euler_yxz_deg(deg: list[float]) -> list[float]:
    """Godot 3 `rotation_degrees` (Euler, YXZ order) → quaternion [x, y, z, w]."""
    hx, hy, hz = (math.radians(d) / 2 for d in deg)
    cx, sx = math.cos(hx), math.sin(hx)
    cy, sy = math.cos(hy), math.sin(hy)
    cz, sz = math.cos(hz), math.sin(hz)
    # q = qY * qX * qZ
    qy = (0.0, sy, 0.0, cy)
    qx = (sx, 0.0, 0.0, cx)
    qz = (0.0, 0.0, sz, cz)

    def mul(a: tuple, b: tuple) -> tuple:
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return (
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        )

    return [round(c, 9) + 0.0 for c in mul(mul(qy, qx), qz)]


def _sample(keys: list[tuple[float, list[float]]], t: float) -> list[float]:
    """Linear interpolation of a (time, vec3) list, clamped at both ends."""
    if t <= keys[0][0]:
        return list(keys[0][1])
    for (t0, v0), (t1, v1) in zip(keys, keys[1:], strict=False):
        if t0 <= t <= t1:
            if t1 == t0:
                return list(v1)
            f = (t - t0) / (t1 - t0)
            return [a + (b - a) * f for a, b in zip(v0, v1, strict=False)]
    return list(keys[-1][1])


def convert_clip(props: dict[str, Any], name: str) -> AnimResult:
    warnings: list[str] = []
    length = float(props.get("length", 1.0))
    track_ids = sorted({int(k.split("/")[1]) for k in props if k.startswith("tracks/") and k.endswith("/type")})
    tracks: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, list[tuple[float, list[float]]]]] = {}  # node → prop → [(time, vec3)]
    for tid in track_ids:
        ttype = props.get(f"tracks/{tid}/type")
        tpath = node_path(props.get(f"tracks/{tid}/path")) or ""
        keys_v = props.get(f"tracks/{tid}/keys")
        keys: list[dict[str, Any]] = []
        if ttype == "transform":
            arr = pool_floats(keys_v) or []
            for i in range(0, len(arr) - 11, 12):
                t, _tr, px, py, pz, qx, qy, qz, qw, sx, sy, sz = arr[i : i + 12]
                keys.append(
                    {"time": t, "position": [px, py, pz], "quaternion": [qx, qy, qz, qw], "scale": [sx, sy, sz]}
                )
            node = tpath.split(":", 1)[0]
        elif ttype == "value" and isinstance(keys_v, dict):
            prop = tpath.split(":", 1)[1] if ":" in tpath else None
            node = tpath.split(":", 1)[0]
            if prop in VECTOR_PROPS:  # old-style clips: separate translation / rotation_degrees / scale tracks
                times = pool_floats(keys_v.get("times")) or []
                values = keys_v.get("values") or []
                series = [(t, v3) for t, v in zip(times, values, strict=False) if (v3 := vector3(v)) is not None]
                if series:
                    grouped.setdefault(node, {})[prop] = series
                continue
            if prop not in ("transform", None):
                warnings.append(f"{name}: value track {tpath} ({prop}) skipped")
                continue
            times = pool_floats(keys_v.get("times")) or []
            values = keys_v.get("values") or []
            for t, v in zip(times, values, strict=False):
                tf = transform12(v)
                if tf is None:
                    warnings.append(f"{name}: non-Transform value in {tpath} skipped")
                    continue
                pos, quat, scale = decompose_transform(tf)
                keys.append({"time": t, "position": pos, "quaternion": quat, "scale": scale})
        else:
            warnings.append(f"{name}: track {tid} type {ttype!r} ({tpath}) skipped")
            continue
        if not keys:
            warnings.append(f"{name}: track {tid} ({tpath}) has no keys")
            continue
        tracks.append({"nodePath": node, "type": "transform", "keys": keys})
    for node, props_ in grouped.items():  # merge the per-property series into (partial) transform keys
        times = sorted({t for series in props_.values() for t, _ in series})
        keys = []
        for t in times:
            k: dict[str, Any] = {"time": t}
            if "translation" in props_:
                k["position"] = _sample(props_["translation"], t)
            if "rotation_degrees" in props_:
                k["quaternion"] = quat_from_euler_yxz_deg(_sample(props_["rotation_degrees"], t))
            if "scale" in props_:
                k["scale"] = _sample(props_["scale"], t)
            keys.append(k)
        tracks.append(
            {
                "nodePath": node,
                "type": "transform",
                "keys": keys,
                "partial": True,
                "fields": [f for f in ("position", "quaternion", "scale") if f in keys[0]],
            }
        )
    return AnimResult(
        {"name": props.get("resource_name", name), "length": length, "tracks": tracks, "note": NOTE}, warnings
    )


def convert_animation_file(gf: GodotFile, name: str) -> AnimResult:
    res = gf.resource
    return convert_clip(res.props if res else {}, name)
