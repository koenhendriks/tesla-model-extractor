"""Turn a Tesla app bundle (`.apks` / `.apkm` / `.xapk` / `.apk`) or an already extracted / recovered directory into a
GDRE-recovered Godot project directory.

Detection is content based: the inner APK that contains `assets/godot/project.binary` holds the Godot project
(in app 4.60 that is `split_assets_pack.apk`). Only `assets/godot/**` is extracted.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .gdre import Gdre

log = logging.getLogger(__name__)
GODOT_PREFIX = "assets/godot/"
MARKER = GODOT_PREFIX + "project.binary"


class BundleError(RuntimeError):
    pass


@dataclass
class BundleInfo:
    source: Path
    kind: str  # bundle | apk | godot_root | recovered
    app_version: str | None


def app_version_from_name(p: Path) -> str | None:
    m = re.search(r"(\d+\.\d+\.\d+(?:[-_]\d+)?)", p.name)
    return m.group(1).replace("_", "-") if m else None


def _version_from_info(z: zipfile.ZipFile, names: list[str]) -> str | None:
    """APKMirror `.apkm` bundles carry an `info.json` with `release_version` ("4.60.5-4573")."""
    if "info.json" not in names:
        return None
    try:
        info = json.loads(z.read("info.json").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    v = info.get("release_version") or info.get("versionname")
    return str(v).replace("_", "-") if v else None


def detect(source: Path) -> BundleInfo:
    source = Path(source)
    if source.is_dir():
        if (source / "project.godot").exists() or (source / "mobile" / "scripts").exists():
            return BundleInfo(source, "recovered", None)
        if (source / "project.binary").exists() or (source / ".import").exists():
            return BundleInfo(source, "godot_root", None)
        if (source / GODOT_PREFIX).exists():
            return BundleInfo(source / GODOT_PREFIX.rstrip("/"), "godot_root", None)
        raise BundleError(f"{source} is neither a recovered project nor an extracted Godot root")
    if not zipfile.is_zipfile(source):
        raise BundleError(f"{source} is not a zip-based bundle (.apks/.apkm/.xapk/.apk)")
    with zipfile.ZipFile(source) as z:
        names = z.namelist()
        version = app_version_from_name(source) or _version_from_info(z, names)
    if MARKER in names:
        return BundleInfo(source, "apk", version)
    if any(n.lower().endswith(".apk") for n in names):
        return BundleInfo(source, "bundle", version)
    raise BundleError(f"{source}: no Godot project (assets/godot/project.binary) and no inner .apk files found")


def _extract_godot(z: zipfile.ZipFile, dest: Path) -> int:
    n = 0
    for info in z.infolist():
        if not info.filename.startswith(GODOT_PREFIX) or info.is_dir():
            continue
        rel = info.filename[len(GODOT_PREFIX) :]
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        n += 1
    return n


def extract_godot_root(source: Path, work: Path) -> Path:
    """Bundle/APK → `<work>/godot_root` containing the Godot project (assets/godot/** flattened)."""
    info = detect(source)
    if info.kind in ("godot_root", "recovered"):
        return info.source
    dest = work / "godot_root"
    if (dest / "project.binary").exists():
        log.info("reusing extracted Godot root at %s", dest)
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as outer:
        if info.kind == "apk":
            n = _extract_godot(outer, dest)
        else:
            n = 0
            for name in outer.namelist():
                if not name.lower().endswith(".apk"):
                    continue
                with outer.open(name) as fh:
                    data = fh.read()  # ~460 MB for the asset pack (stored in .apks, deflated in .apkm)
                try:
                    inner = zipfile.ZipFile(io.BytesIO(data))
                except zipfile.BadZipFile:
                    continue
                with inner:
                    if MARKER not in inner.namelist():
                        continue
                    log.info("Godot project found in %s", name)
                    n = _extract_godot(inner, dest)
                    break
                del data
    if n == 0 or not (dest / "project.binary").exists():
        raise BundleError(f"could not find assets/godot/project.binary inside {source}")
    log.info("extracted %d files to %s", n, dest)
    return dest


def recover(source: Path, work: Path, gdre: Gdre, keep: Path | None = None) -> Path:
    """Full pipeline: bundle → godot root → GDRE recover → recovered project dir."""
    info = detect(source)
    if info.kind == "recovered":
        return info.source
    godot_root = extract_godot_root(source, work)
    out = keep or (work / "recovered")
    if (out / "project.godot").exists() and (out / "mobile").exists():
        log.info("reusing recovered project at %s", out)
        return out
    log.info("running GDRE Tools recovery (this takes a few minutes)…")
    gdre.recover(godot_root, out)
    if not (out / "mobile" / "scripts").exists():
        raise BundleError(f"GDRE recovery did not produce the expected project layout in {out}")
    return out
