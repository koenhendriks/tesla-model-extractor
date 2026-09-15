"""Locate, download (pinned + checksum-verified) and run GDRE Tools (https://github.com/GDRETools/gdsdecomp, MIT)."""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import GDRE_VERSION

log = logging.getLogger(__name__)

# Release assets of the pinned version, sha256 from the GitHub release metadata.
RELEASES: dict[str, dict[str, str]] = {
    "2.6.4": {
        "linux": "eda8cb09e64a060728fa371aa80ae148d3c5584a7de2f553699936daa84e7b4e",
        "macos": "a76051666f1a54333fb86de856ed12232077c73711662cd838044f7c3c4c51eb",
        "windows": "62e06406a08da639e81afb14dcb071beb738be275469e8e980516957b5ebec47",
    }
}
URL = "https://github.com/GDRETools/gdsdecomp/releases/download/v{version}/GDRE_tools-v{version}-{os}.zip"
BINARY_NAMES = {
    "linux": "gdre_tools.x86_64",
    "macos": "gdre_tools.app/Contents/MacOS/gdre_tools",
    "windows": "gdre_tools.exe",
}


class GdreError(RuntimeError):
    pass


def host_os() -> str:
    s = sys.platform
    if s.startswith("linux"):
        return "linux"
    if s == "darwin":
        return "macos"
    if s.startswith("win"):
        return "windows"
    raise GdreError(f"unsupported platform {s}")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "tesla-view-extractor" / "gdre"


@dataclass
class Gdre:
    binary: Path
    version: str = GDRE_VERSION

    # ---------- locating ----------
    @classmethod
    def locate(cls, explicit: str | None = None, allow_download: bool = True) -> Gdre:
        """--gdre PATH → $GDRE_TOOLS → PATH → cache → download."""
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit))
        if env := os.environ.get("GDRE_TOOLS"):
            candidates.append(Path(env))
        for name in ("gdre_tools", "gdre_tools.x86_64", "gdre_tools.exe"):
            if w := shutil.which(name):
                candidates.append(Path(w))
        os_name = host_os()
        candidates.append(cache_dir() / f"v{GDRE_VERSION}" / BINARY_NAMES[os_name])
        for c in candidates:
            if c.is_dir():
                c = c / BINARY_NAMES[os_name]
            if c.is_file():
                return cls(c)
        if not allow_download:
            raise GdreError(
                "GDRE Tools not found. Install it from https://github.com/GDRETools/gdsdecomp/releases and pass "
                "--gdre PATH (or set $GDRE_TOOLS), or allow the download."
            )
        return cls(download(os_name))

    # ---------- running ----------
    def run(self, args: list[str], timeout: int = 3600) -> subprocess.CompletedProcess[str]:
        cmd = [str(self.binary), "--headless", *args]
        log.debug("running %s", " ".join(cmd))
        env = dict(os.environ)
        env.setdefault("XDG_DATA_HOME", str(cache_dir() / "xdg"))  # keep Godot's user:// out of the real home
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)

    def version_string(self) -> str:
        r = self.run(["--version"], timeout=120)
        for line in (r.stdout + r.stderr).splitlines():
            if "Godot RE Tools" in line:
                return line.strip()
        return f"exit {r.returncode}"

    def recover(self, source: Path, output: Path) -> Path:
        """Full project recovery of a PCK/APK/extracted `assets/godot` directory into `output`."""
        output.mkdir(parents=True, exist_ok=True)
        r = self.run([f"--recover={source}", f"--output={output}"])
        log_path = output / "tve-recover.log"
        log_path.write_text(r.stdout + "\n" + r.stderr)
        if r.returncode != 0 and not (output / "project.godot").exists():
            raise GdreError(f"gdre_tools --recover failed (exit {r.returncode}); see {log_path}")
        return output

    def bin_to_txt(self, src: Path, out_dir: Path) -> Path | None:
        """Convert one binary resource to text; returns the produced file (same base name) or None."""
        out_dir.mkdir(parents=True, exist_ok=True)
        r = self.run([f"--bin-to-txt={src}", f"--output={out_dir}"], timeout=600)
        produced = out_dir / src.name
        if produced.exists():
            return produced
        # GDRE may pick a different extension for the text form
        for cand in out_dir.glob(src.stem + ".*"):
            if cand.suffix in (".tres", ".tscn", ".material"):
                return cand
        log.warning("bin-to-txt produced nothing for %s: %s", src, (r.stderr or r.stdout)[-400:])
        return None

    def bin_to_txt_many(self, srcs: list[Path], out_dir: Path) -> None:
        """One GDRE process for many files (`--bin-to-txt` is repeatable). Files with equal names would collide → chunk them."""
        out_dir.mkdir(parents=True, exist_ok=True)
        pending = list(srcs)
        while pending:
            batch: list[Path] = []
            names: set[str] = set()
            rest: list[Path] = []
            for p in pending:
                if p.name in names:
                    rest.append(p)
                else:
                    names.add(p.name)
                    batch.append(p)
            r = self.run([f"--bin-to-txt={p}" for p in batch] + [f"--output={out_dir}"], timeout=1800)
            if r.returncode != 0:
                log.debug("bin-to-txt batch exit %s: %s", r.returncode, (r.stderr or r.stdout)[-400:])
            pending = rest


def download(os_name: str, version: str = GDRE_VERSION, dest: Path | None = None) -> Path:
    if version not in RELEASES or os_name not in RELEASES[version]:
        raise GdreError(f"no pinned GDRE release for {os_name} v{version}")
    dest = dest or (cache_dir() / f"v{version}")
    binary = dest / BINARY_NAMES[os_name]
    if binary.exists():
        return binary
    dest.mkdir(parents=True, exist_ok=True)
    url = URL.format(version=version, os=os_name)
    zip_path = dest / "gdre.zip"
    log.info("downloading GDRE Tools v%s for %s", version, os_name)
    with urllib.request.urlopen(url, timeout=120) as resp, open(zip_path, "wb") as out:  # noqa: S310 – pinned https URL
        shutil.copyfileobj(resp, out)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    expected = RELEASES[version][os_name]
    if digest != expected:
        zip_path.unlink(missing_ok=True)
        raise GdreError(f"GDRE download checksum mismatch: {digest} != {expected}")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    zip_path.unlink()
    if not binary.exists():
        raise GdreError(f"downloaded archive did not contain {BINARY_NAMES[os_name]}")
    if os_name != "windows":
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


def machine_note() -> str:
    return f"{platform.system()} {platform.machine()}"
