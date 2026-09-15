"""Normalise Godot `SpatialMaterial` / `ShaderMaterial` properties into the `MaterialDesc` shape the card understands.

The output shape is stable (the card's `card/src/scene/materials.ts` `MaterialDesc` type depends on it):
  kind: "pbr" | "car_paint" | "tinted_glass" | "chrome_badge" | "beam_glow" | "shader" | "binary_unsupported"
  pbr fields: albedo[3], alpha, albedo_texture, metallic(_texture/_channel), specular, roughness(_texture/_channel),
              ao_texture/_channel/_on_uv2/_light_affect, normal_texture/_scale, emission/_energy/_texture/_on_uv2/_operator,
              transparent, unshaded, cull_mode, blend_mode, depth_draw_mode, render_priority
  shader fields: shader (path), params {name: value | texture path | colour}, render_priority
All paths are project-relative (`res://` stripped).
"""

from __future__ import annotations

from typing import Any

from ..godot.text import GodotFile
from ..godot.values import Call, ExtRef, Ident, SubRef, color

SHADER_KINDS = (
    ("opaque_skybox", "car_paint"),
    ("glass_skybox", "tinted_glass"),
    ("headlights_beam_glow", "beam_glow"),
    ("Powerflow", "power_flow"),
)


def _tex(gf: GodotFile, v: Any) -> str | None:
    if isinstance(v, ExtRef):
        return gf.ext_rel(v)
    return None


def _plain(gf: GodotFile, v: Any) -> Any:
    """Shader params: textures → path, colours → [r,g,b,a], vectors → list, scalars as is."""
    if isinstance(v, ExtRef):
        return gf.ext_rel(v)
    if isinstance(v, SubRef):
        return f"sub_resource#{v.id}"
    if isinstance(v, Ident):
        return v.name
    c = color(v)
    if c is not None:
        return c
    if isinstance(v, Call):
        return v.floats() or [_plain(gf, a) for a in v.args]
    if isinstance(v, list):
        return [_plain(gf, a) for a in v]
    if isinstance(v, dict):
        return {str(k): _plain(gf, a) for k, a in v.items()}
    return v


def normalize_material(
    gf: GodotFile, props: dict[str, Any], mtype: str, name_hint: str, source: str | None
) -> dict[str, Any]:
    g = props.get
    d: dict[str, Any] = {"name": g("resource_name", name_hint), "godot_type": mtype, "source": source}
    if mtype == "ShaderMaterial":
        shader_path = gf.ext_rel(g("shader"))
        d["shader"] = shader_path or "(inline)"
        d["params"] = {k.split("/", 1)[1]: _plain(gf, v) for k, v in props.items() if k.startswith("shader_param/")}
        kind = "shader"
        for needle, k in SHADER_KINDS:
            if shader_path and needle in shader_path:
                kind = k
                break
        if kind == "shader" and source and "Badging" in source:
            kind = "chrome_badge"
        d["kind"] = kind
        d["render_priority"] = g("render_priority", 0)
        return d
    d["kind"] = "pbr"
    col = color(g("albedo_color")) or [1.0, 1.0, 1.0, 1.0]
    d["albedo"] = col[:3]
    d["alpha"] = col[3] if len(col) > 3 else 1.0
    d["albedo_texture"] = _tex(gf, g("albedo_texture"))
    d["metallic"] = g("metallic", 0.0)
    d["metallic_texture"] = _tex(gf, g("metallic_texture"))
    d["metallic_channel"] = g("metallic_texture_channel", 0)
    d["specular"] = g("metallic_specular", 0.5)
    d["roughness"] = g("roughness", 1.0)
    d["roughness_texture"] = _tex(gf, g("roughness_texture"))
    d["roughness_channel"] = g("roughness_texture_channel", 0)
    if g("ao_enabled"):
        d["ao_texture"] = _tex(gf, g("ao_texture"))
        d["ao_channel"] = g("ao_texture_channel", 0)
        d["ao_on_uv2"] = bool(g("ao_on_uv2", False))
        d["ao_light_affect"] = g("ao_light_affect", 0.0)
    if g("normal_enabled"):
        d["normal_texture"] = _tex(gf, g("normal_texture"))
        d["normal_scale"] = g("normal_scale", 1.0)
    if g("emission_enabled"):
        d["emission"] = (color(g("emission")) or [0.0, 0.0, 0.0, 1.0])[:3]
        d["emission_energy"] = g("emission_energy", 1.0)
        d["emission_texture"] = _tex(gf, g("emission_texture"))
        d["emission_on_uv2"] = bool(g("emission_on_uv2", False))
        d["emission_operator"] = g("emission_operator", 0)
    d["transparent"] = bool(g("flags_transparent", False))
    d["unshaded"] = bool(g("flags_unshaded", False))
    d["cull_mode"] = g("params_cull_mode", 0)
    d["blend_mode"] = g("params_blend_mode", 0)
    d["depth_draw_mode"] = g("params_depth_draw_mode", 0)
    d["render_priority"] = g("render_priority", 0)
    return d


def material_from_file(gf: GodotFile, source: str) -> dict[str, Any]:
    """A `.tres` / text `.material` file → MaterialDesc."""
    res = gf.resource
    mtype = gf.resource_type or "SpatialMaterial"
    name_hint = source.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return normalize_material(gf, res.props if res else {}, mtype, name_hint, source)


def binary_unsupported(source: str) -> dict[str, Any]:
    return {
        "name": source.rsplit("/", 1)[-1],
        "kind": "binary_unsupported",
        "source": source,
        "note": "binary .material (RSCC) could not be converted; run with GDRE Tools available",
    }


def texture_refs(desc: dict[str, Any]) -> list[str]:
    """All texture / mesh file paths referenced by a MaterialDesc (used by the packer)."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if (
            isinstance(v, str)
            and "/" in v
            and v.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tga", ".hdr", ".exr"))
        ):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(desc)
    return out
