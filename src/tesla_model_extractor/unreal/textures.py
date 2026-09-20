"""Repack Godot texture channels into the layouts glTF expects (Pillow).

Godot's SpatialMaterial samples metallic / roughness / AO from any channel of any texture (`*_texture_channel`,
0 R, 1 G, 2 B, 3 A, 4 grayscale). glTF fixes the layout: metallicRoughness = G roughness, B metallic; occlusion = R.
Godot normal maps are Y-down (DirectX style), glTF wants Y-up, so the green channel is inverted.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageChops, ImageOps

Loader = Callable[[str], bytes | None]

CHANNEL_NAMES = {0: "R", 1: "G", 2: "B", 3: "A"}


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def channel(img: Image.Image, ch: int) -> Image.Image:
    if ch == 4:
        return ImageOps.grayscale(img.convert("RGB"))
    return img.getchannel(CHANNEL_NAMES.get(ch, "R"))


def _match(a: Image.Image, b: Image.Image) -> tuple[Image.Image, Image.Image]:
    if a.size == b.size:
        return a, b
    size = (max(a.width, b.width), max(a.height, b.height))
    return a.resize(size, Image.Resampling.BILINEAR), b.resize(size, Image.Resampling.BILINEAR)


class TextureRepacker:
    """Produces PNG bytes for glTF texture slots; results are memoised so one source texture is packed once."""

    def __init__(self, load: Loader):
        self.load = load
        self._src: dict[str, Image.Image | None] = {}
        self._out: dict[tuple[Any, ...], bytes | None] = {}
        self.warnings: list[str] = []

    def source(self, rel: str) -> Image.Image | None:
        if rel not in self._src:
            data = self.load(rel)
            if data is None:
                self.warnings.append(f"texture not found: {rel}")
                self._src[rel] = None
            else:
                try:
                    self._src[rel] = _open(data)
                except Exception as e:  # noqa: BLE001
                    self.warnings.append(f"cannot decode {rel}: {e}")
                    self._src[rel] = None
        return self._src[rel]

    def _memo(self, key: tuple[Any, ...], make: Callable[[], bytes | None]) -> bytes | None:
        if key not in self._out:
            self._out[key] = make()
        return self._out[key]

    def base_color(self, rel: str) -> bytes | None:
        """Passed through unchanged when it already is a PNG; re-encoded otherwise."""
        data = self.load(rel)
        if data is None:
            self.warnings.append(f"texture not found: {rel}")
            return None
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data
        img = self.source(rel)
        return _png(img) if img else None

    def metallic_roughness(self, metal: str | None, metal_ch: int, rough: str | None, rough_ch: int) -> bytes | None:
        def make() -> bytes | None:
            m = self.source(metal) if metal else None
            r = self.source(rough) if rough else None
            if m is None and r is None:
                return None
            size = (m or r).size  # type: ignore[union-attr]
            g = channel(r, rough_ch) if r else Image.new("L", size, 255)
            b = channel(m, metal_ch) if m else Image.new("L", size, 255)
            g, b = _match(g, b)
            red = Image.new("L", g.size, 255)
            return _png(Image.merge("RGB", (red, g, b)))

        return self._memo(("mr", metal, metal_ch, rough, rough_ch), make)

    def occlusion(self, rel: str, ch: int) -> bytes | None:
        def make() -> bytes | None:
            img = self.source(rel)
            if img is None:
                return None
            ao = channel(img, ch)
            return _png(Image.merge("RGB", (ao, ao, ao)))

        return self._memo(("ao", rel, ch), make)

    def normal(self, rel: str, flip_green: bool = True) -> bytes | None:
        def make() -> bytes | None:
            img = self.source(rel)
            if img is None:
                return None
            if not flip_green:
                return _png(img.convert("RGB"))
            r, g, b = img.convert("RGB").split()
            return _png(Image.merge("RGB", (r, ImageChops.invert(g), b)))

        return self._memo(("normal", rel, flip_green), make)

    def emissive(self, rel: str) -> bytes | None:
        def make() -> bytes | None:
            img = self.source(rel)
            return _png(img.convert("RGB")) if img else None

        return self._memo(("emissive", rel), make)
