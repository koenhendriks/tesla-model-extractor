"""Write a BuildResult as a deterministic zip (or a directory)."""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import BuildResult

FIXED_TIME = (2020, 1, 1, 0, 0, 0)  # reproducible archives
MIB = 1024 * 1024


@dataclass
class PackStats:
    files: int
    raw_bytes: int
    zip_bytes: int
    sha256: str

    @property
    def raw_mib(self) -> float:
        return self.raw_bytes / MIB

    @property
    def zip_mib(self) -> float:
        return self.zip_bytes / MIB


def _content(v: bytes | Path) -> bytes:
    return v if isinstance(v, bytes) else Path(v).read_bytes()


def estimate_raw(result: BuildResult) -> int:
    total = 0
    for v in result.files.values():
        total += len(v) if isinstance(v, bytes) else Path(v).stat().st_size
    return total


def write_zip(result: BuildResult, out: Path, level: int = 9) -> PackStats:
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for rel in sorted(result.files):
            data = _content(result.files[rel])
            raw += len(data)
            info = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    return PackStats(len(result.files), raw, out.stat().st_size, digest)


def write_dir(result: BuildResult, out: Path) -> PackStats:
    out.mkdir(parents=True, exist_ok=True)
    raw = 0
    for rel, v in result.files.items():
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = _content(v)
        raw += len(data)
        dst.write_bytes(data)
    return PackStats(len(result.files), raw, 0, "")
