#!/usr/bin/env python3
"""CI guard: the repository must never contain Tesla app material.

Fails on: any .apk/.apks/.xapk/.pck/.stex/.glb/.material/.obj/.png/.tres/.tscn/.gd file (fixtures are generated at
test time, not committed), anything larger than 200 KB outside the allow-list, and Tesla-internal codenames outside
the rules/docs where they are legitimately used as configuration keys.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_SUFFIXES = {".apk", ".apks", ".xapk", ".pck", ".stex", ".glb", ".gltf", ".material", ".obj", ".mtl", ".png", ".jpg",
                      ".jpeg", ".webp", ".tres", ".tscn", ".gd", ".gdc", ".import", ".scn", ".res"}
MAX_BYTES = 200 * 1024
# README screenshots: renders of the exported models made by us, not files taken from the app
PREVIEW_DIR = "docs/images/"
PREVIEW_SUFFIXES = {".webp", ".jpg", ".jpeg", ".png"}
PREVIEW_MAX_BYTES = 600 * 1024
CODENAME_RE = re.compile(r"\b(Bayberry|Poppyseed|Palladium|Cybercab|Lychee|Tamarind)\b")
CODENAME_ALLOWED = ("src/tesla_model_extractor/rules/", "docs/", "README.md", "scripts/check_no_assets.py", "tests/", "src/tesla_model_extractor/scrape/", "src/tesla_model_extractor/convert/", "src/tesla_model_extractor/catalog.py", "src/tesla_model_extractor/manifest.py", "src/tesla_model_extractor/legacy.py", "src/tesla_model_extractor/bundle.py", "src/tesla_model_extractor/unreal/")


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / p.decode() for p in out.split(b"\0") if p]


def main() -> int:
    problems: list[str] = []
    for p in tracked_files():
        rel = p.relative_to(ROOT).as_posix()
        if not p.exists():
            continue
        if rel.startswith(PREVIEW_DIR) and p.suffix.lower() in PREVIEW_SUFFIXES:
            if p.stat().st_size > PREVIEW_MAX_BYTES:
                problems.append(f"{rel}: preview image larger than {PREVIEW_MAX_BYTES // 1024} KB")
            continue
        if p.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"{rel}: forbidden file type {p.suffix}")
        if p.stat().st_size > MAX_BYTES:
            problems.append(f"{rel}: {p.stat().st_size // 1024} KB is suspiciously large")
        if p.suffix in (".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".json"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if CODENAME_RE.search(text) and not rel.startswith(CODENAME_ALLOWED):
                problems.append(f"{rel}: Tesla codename outside the allowed locations")
            if re.search(r"com\.teslamotors|RSCC\x02", text):
                problems.append(f"{rel}: app identifier / binary resource content")
    if problems:
        print("Tesla material check FAILED:")
        for x in problems:
            print("  -", x)
        return 1
    print("ok: no Tesla assets in the repository")
    return 0


if __name__ == "__main__":
    sys.exit(main())
