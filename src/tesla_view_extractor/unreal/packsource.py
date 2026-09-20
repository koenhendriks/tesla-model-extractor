"""Read an existing asset pack (zip or unpacked directory) back into a `BuildResult`."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..manifest import BuildResult


def load_pack(source: Path) -> BuildResult:
    source = Path(source)
    files: dict[str, bytes | Path] = {}
    if source.is_dir():
        for p in sorted(source.rglob("*")):
            if p.is_file():
                files[p.relative_to(source).as_posix()] = p
    else:
        with zipfile.ZipFile(source) as z:
            for info in z.infolist():
                if not info.is_dir():
                    files[info.filename] = z.read(info)
    if "manifest.json" not in files:
        raise ValueError(f"{source}: not an asset pack (no manifest.json)")
    raw = files["manifest.json"]
    manifest = json.loads(raw if isinstance(raw, bytes) else Path(raw).read_bytes())
    return BuildResult(manifest=manifest, files=files, warnings=list(manifest.get("warnings", [])))


def is_pack(source: Path) -> bool:
    source = Path(source)
    if source.is_dir():
        return (source / "manifest.json").exists()
    if source.suffix.lower() != ".zip" or not zipfile.is_zipfile(source):
        return False
    with zipfile.ZipFile(source) as z:
        return "manifest.json" in z.namelist()
