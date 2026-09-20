"""`MaterialDesc` (see convert/materials.py) → glTF 2.0 material appended to a `Document`.

Colour factors: the app renders in gamma space, so a Godot colour value is what appears on screen. glTF factors are
linear, hence `srgb_to_linear` on every colour factor; textures are declared sRGB by glTF and decode to the same
appearance without conversion.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .gltf import Document
from .textures import TextureRepacker

EXT_EMISSIVE_STRENGTH = "KHR_materials_emissive_strength"
EXT_UNLIT = "KHR_materials_unlit"
EXT_CLEARCOAT = "KHR_materials_clearcoat"
EXT_TEXTURE_TRANSFORM = "KHR_texture_transform"

GODOT_CULL_DISABLED = 2
GODOT_BLEND_ADD = 1
ALL_TEXCOORDS = frozenset({0, 1})


def srgb_to_linear(c: float) -> float:
    c = min(max(c, 0.0), 1.0)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_rgb(h: str) -> list[float]:
    h = h.lstrip("#")
    return [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]


def _rgb(c: list[float] | None, default: list[float]) -> list[float]:
    return [round(srgb_to_linear(x), 5) for x in (c or default)[:3]]


@dataclass
class Paint:
    name: str
    albedo: list[float]  # 0..1 gamma
    metallic: float
    roughness: float

    @classmethod
    def from_table(cls, name: str, entry: dict[str, Any]) -> Paint:
        return cls(name, hex_to_rgb(entry["albedo"]), float(entry["metallic"]), float(entry["roughness"]))


@dataclass
class MaterialBuilder:
    doc: Document
    textures: TextureRepacker
    paint: Paint | None = None
    flip_normal_green: bool = True
    paint_explicit: bool = (
        False  # the user picked a paint: it also replaces shaders with their own colours (Cybertruck)
    )
    warnings: list[str] = field(default_factory=list)
    additive: list[str] = field(default_factory=list)  # glTF material names that were additive in Godot
    _built: dict[str, int] = field(default_factory=dict)
    _texture_cache: dict[tuple[Any, ...], int | None] = field(default_factory=dict)

    # ---------- textures ----------
    def _texture(self, key: tuple[Any, ...], make: Any, name: str, wrap: bool = True) -> int | None:
        if key not in self._texture_cache:
            data = make()
            if data is None:
                self._texture_cache[key] = None
            else:
                img = self.doc.add_image(data, name)
                sampler = self.doc.sampler() if wrap else self.doc.sampler(33071, 33071)
                self._texture_cache[key] = self.doc.add_texture(img, sampler)
        return self._texture_cache[key]

    def _info(self, tex: int | None, tex_coord: int = 0, uv: dict[str, Any] | None = None, **extra: Any) -> dict | None:
        if tex is None:
            return None
        info: dict[str, Any] = {"index": tex}
        if tex_coord:
            info["texCoord"] = tex_coord
        if uv:
            info["extensions"] = {EXT_TEXTURE_TRANSFORM: uv}
            self.doc.use_extension(EXT_TEXTURE_TRANSFORM)
        info.update({k: v for k, v in extra.items() if v is not None})
        return info

    @staticmethod
    def _stem(rel: str) -> str:
        return rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    # ---------- entry point ----------
    def build(self, key: str, desc: dict[str, Any], texcoords: frozenset[int] = ALL_TEXCOORDS) -> int:
        """glTF material index for a MaterialDesc, memoised per material key.

        `texcoords` are the UV sets the target primitive has; textures bound to a missing set are dropped in a
        per-primitive variant of the material (a validator error otherwise, and undefined sampling in engines)."""
        if texcoords != ALL_TEXCOORDS:
            base = self.doc.materials[self.build(key, desc)]
            if not _missing_texcoords(base, texcoords):
                return self._built[key]
            memo = f"{key}#uv{''.join(str(t) for t in sorted(texcoords))}"
            if memo not in self._built:
                mat = copy.deepcopy(base)
                _drop_texcoords(mat, texcoords)
                self._built[memo] = self.doc.add_material(mat)
            return self._built[memo]
        if key in self._built:
            return self._built[key]
        kind = desc.get("kind", "pbr")
        if kind == "pbr":
            mat = self._pbr(desc)
        elif kind == "car_paint":
            mat = self._car_paint(desc)
        elif kind == "tinted_glass":
            mat = self._glass(desc)
        elif kind == "chrome_badge":
            mat = self._chrome(desc)
        else:
            mat = self._generic_shader(desc, kind)
        mat["name"] = str(desc.get("name") or self._stem(key))
        mat.setdefault("extras", {})["godot"] = {"source": desc.get("source") or key, "kind": kind}
        idx = self.doc.add_material(mat)
        self._built[key] = idx
        return idx

    # ---------- SpatialMaterial ----------
    def _uv_transform(self, desc: dict[str, Any], slot: int = 1) -> dict[str, Any] | None:
        scale = desc.get(f"uv{slot}_scale")
        offset = desc.get(f"uv{slot}_offset")
        out: dict[str, Any] = {}
        if scale and (abs(scale[0] - 1) > 1e-6 or abs(scale[1] - 1) > 1e-6):
            out["scale"] = [scale[0], scale[1]]
        if offset and (abs(offset[0]) > 1e-6 or abs(offset[1]) > 1e-6):
            out["offset"] = [offset[0], offset[1]]
        return out or None

    def _pbr(self, d: dict[str, Any]) -> dict[str, Any]:
        uv1 = self._uv_transform(d, 1)
        uv2 = self._uv_transform(d, 2)
        alpha = float(d.get("alpha", 1.0))
        pbr: dict[str, Any] = {
            "baseColorFactor": _rgb(d.get("albedo"), [1.0, 1.0, 1.0]) + [round(alpha, 5)],
            "metallicFactor": float(d.get("metallic", 0.0)),
            "roughnessFactor": float(d.get("roughness", 1.0)),
        }
        mat: dict[str, Any] = {"pbrMetallicRoughness": pbr}
        albedo = d.get("albedo_texture")
        if albedo:
            tex = self._texture(("bc", albedo), lambda: self.textures.base_color(albedo), self._stem(albedo))
            pbr["baseColorTexture"] = self._info(tex, 0, uv1)
        metal, rough = d.get("metallic_texture"), d.get("roughness_texture")
        if metal or rough:
            mc, rc = int(d.get("metallic_channel", 0)), int(d.get("roughness_channel", 0))
            tex = self._texture(
                ("mr", metal, mc, rough, rc),
                lambda: self.textures.metallic_roughness(metal, mc, rough, rc),
                f"{self._stem(rough or metal or 'mr')}_MR",
            )
            pbr["metallicRoughnessTexture"] = self._info(tex, 0, uv1)
            if metal is None:
                pbr["metallicFactor"] = float(d.get("metallic", 0.0))
            if rough is None:
                pbr["roughnessFactor"] = float(d.get("roughness", 1.0))
        ao = d.get("ao_texture")
        if ao:
            ch = int(d.get("ao_channel", 0))
            on_uv2 = bool(d.get("ao_on_uv2"))
            tex = self._texture(("ao", ao, ch), lambda: self.textures.occlusion(ao, ch), f"{self._stem(ao)}_AO")
            mat["occlusionTexture"] = self._info(tex, 1 if on_uv2 else 0, uv2 if on_uv2 else uv1, strength=1.0)
        normal = d.get("normal_texture")
        if normal:
            tex = self._texture(
                ("normal", normal, self.flip_normal_green),
                lambda: self.textures.normal(normal, self.flip_normal_green),
                self._stem(normal),
            )
            mat["normalTexture"] = self._info(tex, 0, uv1, scale=float(d.get("normal_scale", 1.0)))
        if "emission" in d:
            self._emission(mat, d, uv1, uv2)
        transparent = bool(d.get("transparent")) or alpha < 0.999
        if transparent:
            mat["alphaMode"] = "BLEND"
        if d.get("cull_mode") == GODOT_CULL_DISABLED:
            mat["doubleSided"] = True
        if d.get("unshaded"):
            mat["extensions"] = {EXT_UNLIT: {}}
            self.doc.use_extension(EXT_UNLIT)
        if d.get("blend_mode") == GODOT_BLEND_ADD:
            mat["alphaMode"] = "BLEND"
            mat.setdefault("extras", {})["godot_blend_mode"] = "add"
            self.additive.append(str(d.get("name") or ""))
        return mat

    def _emission(self, mat: dict[str, Any], d: dict[str, Any], uv1: Any, uv2: Any) -> None:
        energy = float(d.get("emission_energy", 1.0))
        color = d.get("emission") or [0.0, 0.0, 0.0]
        tex_rel = d.get("emission_texture")
        if tex_rel:
            tex = self._texture(("em", tex_rel), lambda: self.textures.emissive(tex_rel), self._stem(tex_rel))
            on_uv2 = bool(d.get("emission_on_uv2"))
            mat["emissiveTexture"] = self._info(tex, 1 if on_uv2 else 0, uv2 if on_uv2 else uv1)
            # Godot adds colour and texture; glTF multiplies. A texture with a black colour is the common case.
            mat["emissiveFactor"] = [1.0, 1.0, 1.0] if max(color[:3]) < 1e-6 else _rgb(color, color)
            if max(color[:3]) >= 1e-6 and int(d.get("emission_operator", 0)) == 0:
                self.warnings.append(
                    f"{d.get('name')}: emission colour is added to the texture in Godot; exported as multiply"
                )
        else:
            mat["emissiveFactor"] = _rgb(color, color)
        if abs(energy - 1.0) > 1e-6 and energy > 0:
            mat.setdefault("extensions", {})[EXT_EMISSIVE_STRENGTH] = {"emissiveStrength": round(energy, 4)}
            self.doc.use_extension(EXT_EMISSIVE_STRENGTH)

    # ---------- ShaderMaterial approximations ----------
    def _car_paint(self, d: dict[str, Any]) -> dict[str, Any]:
        p = d.get("params", {})
        own_look = "paint_mix" in str(d.get("shader", ""))  # Cybertruck stainless: the shader carries the colour
        if self.paint and (self.paint_explicit or not own_look):
            albedo, metallic, roughness = self.paint.albedo, self.paint.metallic, self.paint.roughness
        else:
            albedo = (p.get("color") or p.get("color_bright") or [0.1, 0.1, 0.1, 1.0])[:3]
            metallic = float(p.get("metallic", p.get("metallic_bright", 0.7)))
            roughness = float(p.get("roughness", p.get("roughness_bright", 0.1)))
        mat: dict[str, Any] = {
            "pbrMetallicRoughness": {
                "baseColorFactor": _rgb(albedo, albedo) + [1.0],
                "metallicFactor": metallic,
                "roughnessFactor": roughness,
            },
            "extensions": {EXT_CLEARCOAT: {"clearcoatFactor": 1.0, "clearcoatRoughnessFactor": 0.03}},
        }
        self.doc.use_extension(EXT_CLEARCOAT)
        ao = p.get("ao")
        if isinstance(ao, str):
            # the body UVs lie outside 0..1 and rely on REPEAT wrapping (the default sampler)
            tex = self._texture(("ao", ao, 2), lambda: self.textures.occlusion(ao, 2), f"{self._stem(ao)}_AO")
            strength = round(min(1.0, max(0.0, float(p.get("ao_intensity", 1.0)))), 4)
            mat["occlusionTexture"] = self._info(tex, 0, None, strength=strength)
        if self.paint and (self.paint_explicit or not own_look):
            mat["extras"] = {"paint": self.paint.name}
        return mat

    def _glass(self, d: dict[str, Any]) -> dict[str, Any]:
        p = d.get("params", {})
        color = p.get("color") or [0.0, 0.0, 0.0, 0.8]
        return {
            "pbrMetallicRoughness": {
                "baseColorFactor": _rgb(color, color) + [round(float(color[3]) if len(color) > 3 else 0.8, 5)],
                "metallicFactor": float(p.get("metalic", p.get("metallic", 0.0))),
                "roughnessFactor": float(p.get("roughness", 0.05)),
            },
            "alphaMode": "BLEND",
        }

    def _chrome(self, d: dict[str, Any]) -> dict[str, Any]:
        p = d.get("params", {})
        color = p.get("base_color") or [0.8, 0.8, 0.8, 1.0]
        return {
            "pbrMetallicRoughness": {
                "baseColorFactor": _rgb(color, color) + [1.0],
                "metallicFactor": 1.0,
                "roughnessFactor": 0.1,
            }
        }

    def _generic_shader(self, d: dict[str, Any], kind: str) -> dict[str, Any]:
        p = d.get("params", {})
        color = p.get("color") or p.get("albedo") or p.get("color_background") or [1.0, 1.0, 1.0, 1.0]
        alpha = float(color[3]) if len(color) > 3 else 1.0
        mat: dict[str, Any] = {
            "pbrMetallicRoughness": {
                "baseColorFactor": _rgb(color, color) + [round(alpha, 5)],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "extensions": {EXT_UNLIT: {}},
            "alphaMode": "BLEND",
            "doubleSided": True,
        }
        self.doc.use_extension(EXT_UNLIT)
        tex_rel = next(
            (v for k, v in p.items() if k in ("texture_albedo", "texture_bc", "albedo_texture") and isinstance(v, str)),
            None,
        )
        if tex_rel and "/" in tex_rel:
            tex = self._texture(("bc", tex_rel), lambda: self.textures.base_color(tex_rel), self._stem(tex_rel))
            mat["pbrMetallicRoughness"]["baseColorTexture"] = self._info(tex)
        self.warnings.append(f"{d.get('name')}: {kind} shader ({d.get('shader')}) approximated as unlit blend")
        return mat


def _texture_infos(mat: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    out: list[tuple[dict[str, Any], str]] = []
    pbr = mat.get("pbrMetallicRoughness", {})
    for holder, k in (
        (pbr, "baseColorTexture"),
        (pbr, "metallicRoughnessTexture"),
        (mat, "normalTexture"),
        (mat, "occlusionTexture"),
        (mat, "emissiveTexture"),
    ):
        if isinstance(holder.get(k), dict):
            out.append((holder, k))
    return out


def _missing_texcoords(mat: dict[str, Any], available: frozenset[int]) -> bool:
    return any(holder[k].get("texCoord", 0) not in available for holder, k in _texture_infos(mat))


def _drop_texcoords(mat: dict[str, Any], available: frozenset[int]) -> None:
    for holder, k in _texture_infos(mat):
        if holder[k].get("texCoord", 0) not in available:
            del holder[k]
    mat["name"] = f"{mat.get('name', '')}_uv{''.join(str(t) for t in sorted(available))}"
