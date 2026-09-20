"""Pack animation JSON (`animations/<Player>.json`, see convert/anim.py) → glTF animations.

One glTF animation per AnimationPlayer; every track becomes translation / rotation / scale channels on the node
whose name is the last segment of the Godot node path. Time 0 = closed, `length` = open (documented in the guide).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .gltf import FLOAT, Document

EPS = 1e-9


@dataclass
class AnimStats:
    name: str
    length: float
    channels: int
    missing_nodes: list[str] = field(default_factory=list)


def _series(keys: list[dict[str, Any]], prop: str) -> tuple[list[float], list[float]] | None:
    times: list[float] = []
    values: list[float] = []
    for k in keys:
        if prop not in k:
            continue
        t = float(k["time"])
        if times and t <= times[-1] + EPS:  # glTF requires strictly increasing input
            t = times[-1] + 1e-4
        times.append(t)
        values.extend(float(x) for x in k[prop])
    if not times:
        return None
    return times, values


def _static(values: list[float], width: int) -> bool:
    first = values[:width]
    return all(abs(values[i] - first[i % width]) < 1e-7 for i in range(len(values)))


def add_animation(doc: Document, data: dict[str, Any], resolve: dict[str, int]) -> AnimStats:
    """Append one animation. `resolve` maps node names (last path segment) to node indices."""
    name = str(data.get("name") or "Animation")
    stats = AnimStats(name, float(data.get("length", 0.0)), 0)
    samplers: list[dict[str, Any]] = []
    channels: list[dict[str, Any]] = []
    for track in data.get("tracks", []):
        leaf = str(track.get("nodePath", "")).split(":", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        node = resolve.get(leaf)
        if node is None:
            stats.missing_nodes.append(leaf)
            continue
        keys = track.get("keys", [])
        for prop, path, width in (("position", "translation", 3), ("quaternion", "rotation", 4), ("scale", "scale", 3)):
            s = _series(keys, prop)
            if s is None:
                continue
            times, values = s
            if len(times) > 1 and _static(values, width) and prop == "scale":
                continue  # a constant unit scale track is noise
            if prop == "quaternion":
                values = _shortest_path(values)
            input_acc = doc.add_accessor(times, "SCALAR", FLOAT)
            output_acc = doc.add_accessor(values, "VEC4" if width == 4 else "VEC3", FLOAT, minmax=False)
            samplers.append({"input": input_acc, "output": output_acc, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
    stats.channels = len(channels)
    if channels:
        doc.animations.append({"name": name, "samplers": samplers, "channels": channels})
    return stats


def _shortest_path(q: list[float]) -> list[float]:
    """Flip quaternion signs so consecutive keys interpolate the short way round."""
    out = list(q)
    for i in range(4, len(out), 4):
        dot = sum(out[i + k] * out[i - 4 + k] for k in range(4))
        if dot < 0:
            for k in range(4):
                out[i + k] = -out[i + k]
    return out
