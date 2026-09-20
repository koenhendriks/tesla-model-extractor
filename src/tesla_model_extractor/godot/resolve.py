"""Locate `res://` resources inside a GDRE-recovered project and load them as parsed text.

GDRE writes most files at their `res://` path, some under `<root>/.assets/<path>` and keeps the import cache
under `<root>/.import/`. Binary resources (`RSRC` / compressed `RSCC`, typically `.material`) are converted to text
on demand with `gdre_tools --bin-to-txt` and cached under `<root>/.tve-cache/`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .text import GodotFile, is_text_resource, parse_text, strip_res

if TYPE_CHECKING:
    from ..gdre import Gdre

log = logging.getLogger(__name__)


class ResourceNotFound(FileNotFoundError):
    pass


class BinaryResourceError(RuntimeError):
    """A binary resource could not be converted to text (no GDRE available or conversion failed)."""


class ResourceRoot:
    def __init__(self, root: Path, gdre: Gdre | None = None, cache_dir: Path | None = None):
        self.root = Path(root)
        self.gdre = gdre
        self.cache_dir = cache_dir or (self.root / ".tve-cache")
        self._text_cache: dict[str, GodotFile] = {}

    # ---------- paths ----------
    def rel(self, res: str) -> str:
        return strip_res(res).lstrip("/")

    def resolve(self, res: str) -> Path | None:
        rel = self.rel(res)
        for base in (self.root, self.root / ".assets"):
            p = base / rel
            if p.exists():
                return p
        return None

    def exists(self, res: str) -> bool:
        return self.resolve(res) is not None

    def require(self, res: str) -> Path:
        p = self.resolve(res)
        if p is None:
            raise ResourceNotFound(res)
        return p

    # ---------- text resources ----------
    def is_binary(self, res: str) -> bool:
        p = self.require(res)
        with open(p, "rb") as fh:
            head = fh.read(16)
        return not is_text_resource(head)

    def load(self, res: str) -> GodotFile:
        """Parse a text resource; binary resources are converted through GDRE first."""
        rel = self.rel(res)
        if rel in self._text_cache:
            return self._text_cache[rel]
        p = self.require(res)
        with open(p, "rb") as fh:
            head = fh.read(16)
        if is_text_resource(head):
            gf = parse_text(p.read_text(encoding="utf-8", errors="replace"), p)
        else:
            gf = self._load_binary(rel, p)
        self._text_cache[rel] = gf
        return gf

    def _load_binary(self, rel: str, src: Path) -> GodotFile:
        cached = self.cache_dir / "bin2txt" / rel
        if not cached.exists():
            if self.gdre is None:
                raise BinaryResourceError(f"{rel} is a binary resource and no GDRE Tools binary is available")
            cached.parent.mkdir(parents=True, exist_ok=True)
            out = self.gdre.bin_to_txt(src, cached.parent)
            if out is None or not out.exists():
                raise BinaryResourceError(f"GDRE could not convert {rel} to text")
            if out != cached:
                out.replace(cached)
        return parse_text(cached.read_text(encoding="utf-8", errors="replace"), cached)

    def prefetch_binary(self, rels: list[str]) -> None:
        """Convert every not-yet-cached binary resource in `rels` with a single GDRE invocation."""
        if self.gdre is None:
            return
        todo: list[tuple[str, Path]] = []
        for res in dict.fromkeys(rels):
            rel = self.rel(res)
            p = self.resolve(rel)
            if p is None or (self.cache_dir / "bin2txt" / rel).exists():
                continue
            with open(p, "rb") as fh:
                if is_text_resource(fh.read(16)):
                    continue
            todo.append((rel, p))
        if not todo:
            return
        out_dir = self.cache_dir / "bin2txt" / "_batch"
        out_dir.mkdir(parents=True, exist_ok=True)
        self.gdre.bin_to_txt_many([p for _, p in todo], out_dir)
        for rel, p in todo:
            produced = out_dir / p.name
            if produced.exists():
                dst = self.cache_dir / "bin2txt" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                produced.replace(dst)
            else:
                log.warning("GDRE did not convert %s", rel)

    def try_load(self, res: str) -> GodotFile | None:
        try:
            return self.load(res)
        except (ResourceNotFound, BinaryResourceError) as e:
            log.debug("cannot load %s: %s", res, e)
            return None
