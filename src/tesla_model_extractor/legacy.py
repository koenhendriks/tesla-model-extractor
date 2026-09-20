"""`compare-legacy`: check a pack against the asset folder Tesla View shipped before packs existed (development aid).

The legacy layout renamed Godot folders (`Ego/Bayberry/` → `model-y-juniper/`, …) and stored generated JSON under other
names. This maps both ways and reports byte/semantic differences so a refactor of the extractor can be checked for
regressions against known-good output.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .validate import PackReader

LEGACY_MAP = [
    ("Ego/BayberryE41/", "model-y-standard-e41/"),
    ("Ego/Bayberry/", "model-y-juniper/"),
    ("Ego/Wheels_Bayberry/", "wheels/"),
    ("Ego/Brakes/", "brakes/"),
    ("Ego/Shared/", "shared/badging/"),
    ("Ego/3_High/Defrost", "shared/defrost/Defrost"),
    ("mobile/materials/", "shared/materials/"),
    ("mobile/geometry/", "charging/"),
    ("mobile/scenes/", "shared/plate/"),
    ("shaders/", "shared/shaders/"),
    ("shared_misc_textures/New_Studio.png", "shared/environment/studio/New_Studio.png"),
    ("", "full-godot-project-recovered/"),
]


def to_legacy(rel: str) -> str:
    m = re.match(r"^(.*)/animations/([^/]+)\.json$", rel)
    if m:
        return to_legacy(m.group(1) + "/") + "animations/json/" + m.group(2) + ".json"
    m = re.match(r"^(.*)/([^/]+)\.overrides\.json$", rel)
    if m:
        folder, stem = m.group(1), m.group(2)
        if folder.startswith("Ego/Brakes"):
            return f"brakes/{stem}.json"
        if folder.startswith("mobile/geometry/Charging_Cable"):
            return f"charging/Charging_Cable/{stem}.json"
        return to_legacy(folder + "/") + "scene-overrides.json"
    for a, b in LEGACY_MAP:
        if rel.startswith(a):
            return b + rel[len(a) :]
    return rel


def _is_file_path(s: str) -> bool:
    last = s.rsplit("/", 1)[-1]
    return "/" in s and "." in last and not s.startswith("sub_resource") and " " not in s


def remap_json(v: Any) -> Any:
    if isinstance(v, str) and _is_file_path(v):
        return to_legacy(v)
    if isinstance(v, dict):
        return {(to_legacy(k) if isinstance(k, str) and _is_file_path(k) else k): remap_json(x) for k, x in v.items()}
    if isinstance(v, list):
        return [remap_json(x) for x in v]
    return v


@dataclass
class Compare:
    identical: list[str] = field(default_factory=list)
    json_equal: list[str] = field(default_factory=list)
    different: dict[str, list[str]] = field(default_factory=dict)
    pack_only: list[str] = field(default_factory=list)
    legacy_only: list[str] = field(default_factory=list)


def _diff(a: Any, b: Any, path: str, out: list[str], limit: int = 20) -> None:
    if len(out) >= limit:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"{path}.{k}: only in pack")
            elif k not in b:
                out.append(f"{path}.{k}: only in legacy")
            else:
                _diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            _diff(x, y, f"{path}[{i}]", out, limit)
    elif (
        isinstance(a, (int, float))
        and isinstance(b, (int, float))
        and not isinstance(a, bool)
        and not isinstance(b, bool)
    ):
        if abs(a - b) > 1e-6:
            out.append(f"{path}: {a} != {b}")
    elif a != b:
        out.append(f"{path}: {str(a)[:50]!r} != {str(b)[:50]!r}")


IGNORE_KEYS = {"note", "source", "instance", "inline_mesh", "godot_type"}


def _strip(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if k not in IGNORE_KEYS}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def compare_legacy(pack: Path, legacy_dir: Path) -> Compare:
    cmp = Compare()
    reader = PackReader(pack)
    legacy_files = {p.relative_to(legacy_dir).as_posix(): p for p in legacy_dir.rglob("*") if p.is_file()}
    seen: set[str] = set()
    try:
        for rel in sorted(reader.names):
            if rel == "manifest.json":
                continue
            leg = to_legacy(rel)
            seen.add(leg)
            lp = legacy_files.get(leg)
            if lp is None:
                cmp.pack_only.append(f"{rel} (→ {leg})")
                continue
            data = reader.read(rel)
            old = lp.read_bytes()
            if hashlib.sha256(data).digest() == hashlib.sha256(old).digest():
                cmp.identical.append(rel)
                continue
            if rel.endswith(".json"):
                try:
                    a = _strip(remap_json(json.loads(data)))
                    b = _strip(json.loads(old))
                except json.JSONDecodeError as e:
                    cmp.different[rel] = [f"json error: {e}"]
                    continue
                out: list[str] = []
                _diff(b, a, "", out)
                if out:
                    cmp.different[rel] = out
                else:
                    cmp.json_equal.append(rel)
            else:
                cmp.different[rel] = [f"binary differs ({len(data)} vs {len(old)} bytes)"]
        for leg in sorted(legacy_files):
            if leg not in seen and not leg.endswith((".md", ".jpeg", ".html", ".js", ".py")):
                cmp.legacy_only.append(leg)
    finally:
        reader.close()
    return cmp
