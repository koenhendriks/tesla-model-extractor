"""Validate an asset pack (zip or directory): manifest shape, referenced files, node names, size limits."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import PACK_FORMAT
from .godot.glb import read_glb_bytes

MODEL_ID_RE = re.compile(r"^[a-z0-9_]+$")
DEFAULT_MAX_MIB = 100


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    zip_bytes: int = 0
    raw_bytes: int = 0
    files: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


class PackReader:
    """Uniform access to a pack in a zip file or an unpacked directory."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.zip: zipfile.ZipFile | None = None
        if self.path.is_file():
            self.zip = zipfile.ZipFile(self.path)
            self.names = set(self.zip.namelist())
        else:
            self.names = {p.relative_to(self.path).as_posix() for p in self.path.rglob("*") if p.is_file()}

    def read(self, rel: str) -> bytes:
        if self.zip:
            return self.zip.read(rel)
        return (self.path / rel).read_bytes()

    def size(self, rel: str) -> int:
        if self.zip:
            return self.zip.getinfo(rel).file_size
        return (self.path / rel).stat().st_size

    def close(self) -> None:
        if self.zip:
            self.zip.close()


def _refs(m: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(required files, optional files) referenced by a manifest."""
    req: list[str] = []
    opt: list[str] = []
    for model in m.get("models", {}).values():
        if model.get("glb"):
            req.append(model["glb"])
        req.append(model["overrides"])
        req += list(model.get("animations", {}).values())
        for s in (model.get("brakes") or {}).values():
            req += list(s.values())
    for w in m.get("wheels", {}).values():
        if w.get("kind") in ("glb", "obj"):
            if w.get("glb"):
                req.append(w["glb"])
            if w.get("overrides"):
                req.append(w["overrides"])
    for c in m.get("cables", {}).values():
        req.append(c["overrides"])
    if (m.get("environment") or {}).get("panorama"):
        opt.append(m["environment"]["panorama"])
    return req, opt


def _overrides_refs(ov: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str) and "/" in v and v.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".obj", ".glb")):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for mat in ov.get("materials", {}).values():
        walk(mat)
    for n in ov.get("nodes", {}).values():
        walk(n.get("mesh"))
    return out


def validate_pack(path: Path, max_mib: float = DEFAULT_MAX_MIB) -> Report:
    rep = Report()
    if path.is_file():
        rep.zip_bytes = path.stat().st_size
        if not zipfile.is_zipfile(path):
            rep.errors.append("not a zip file")
            return rep
    reader = PackReader(path)
    try:
        rep.files = len(reader.names)
        rep.raw_bytes = sum(reader.size(n) for n in reader.names)
        if "manifest.json" not in reader.names:
            rep.errors.append("manifest.json missing at the pack root")
            return rep
        try:
            m = json.loads(reader.read("manifest.json"))
        except json.JSONDecodeError as e:
            rep.errors.append(f"manifest.json is not valid JSON: {e}")
            return rep
        if m.get("format") != PACK_FORMAT:
            rep.errors.append(f"unsupported pack format {m.get('format')!r} (expected {PACK_FORMAT})")
        models = m.get("models") or {}
        if not models:
            rep.errors.append("manifest has no models")
        for mid, model in models.items():
            if not MODEL_ID_RE.match(mid):
                rep.errors.append(f"bad model id {mid!r}")
            for key in ("name", "overrides", "closures", "lights", "pivots", "markers"):
                if key not in model:
                    rep.errors.append(f"model {mid}: missing {key}")
        req, opt = _refs(m)
        for r in req:
            if r not in reader.names:
                rep.errors.append(f"referenced file missing: {r}")
        for o in opt:
            if o not in reader.names:
                rep.warnings.append(f"optional file missing: {o}")
        for bad in reader.names:
            if bad.startswith("/") or ".." in bad.split("/") or "\\" in bad:
                rep.errors.append(f"unsafe path in pack: {bad}")
        # per-model checks against the GLB
        for mid, model in models.items():
            ovp = model.get("overrides")
            if ovp not in reader.names:
                continue
            ov = json.loads(reader.read(ovp))
            for ref in _overrides_refs(ov):
                if ref not in reader.names:
                    rep.warnings.append(f"model {mid}: {ovp} references missing file {ref}")
            known = set(ov.get("nodes", {}).keys())
            if model.get("glb") in reader.names:
                try:
                    known |= read_glb_bytes(reader.read(model["glb"])).node_set
                except Exception as e:  # noqa: BLE001
                    rep.warnings.append(f"model {mid}: cannot read GLB: {e}")
            names: list[str] = list(model.get("show", [])) + list(model.get("hide", []))
            for grp in (model.get("lights") or {}).values():
                names += grp.get("on", []) + grp.get("off", [])
            for var in (model.get("variants") or {}).values():
                if var:
                    names += var.get("show", []) + var.get("hide", [])
            names += model.get("pivots", {}).get("wheels", []) + model.get("pivots", {}).get("brakes", [])
            for n in dict.fromkeys(names):
                if n not in known:
                    rep.warnings.append(f"model {mid}: node {n!r} not found in overrides or GLB")
            for ch, clips in (model.get("closures") or {}).items():
                for c in clips:
                    if c not in (model.get("animations") or {}):
                        rep.errors.append(f"model {mid}: closure {ch} references unknown animation {c}")
        for n in reader.names:
            if "/Skins/" in n:
                rep.warnings.append(f"skin texture included: {n}")
        if rep.zip_bytes and rep.zip_bytes > max_mib * 1024 * 1024:
            rep.errors.append(
                f"zip is {rep.zip_bytes / 1048576:.1f} MiB, over the {max_mib} MiB limit (Home Assistant upload limit is 100 MiB) – build per-model packs"
            )
    finally:
        reader.close()
    return rep
