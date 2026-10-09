"""glTF Document (see gltf.py) -> COLLADA (.dae) + textures.

Collada's <phong> technique maps more directly onto glTF PBR than OBJ/MTL does (diffuse/specular/shininess/
transparency are first-class elements, not implicit conventions), and <library_images> gives textures clean,
unambiguous relative paths. Features with no Collada-phong equivalent (metallic/roughness textures, clearcoat,
occlusion, KHR_materials_unlit, animations) are reduced the same way as for OBJ, with warnings recorded.
"""

from __future__ import annotations

import re
import struct
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from .gltf import Document, mat4_mul, node_local_matrix

_COMP_FMT = {5126: "f", 5123: "H", 5125: "I", 5121: "B"}
_COMP_SIZE = {5126: 4, 5123: 2, 5125: 4, 5121: 1}
_TYPE_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
_INVALID_ID = re.compile(r"[^A-Za-z0-9_]+")


@dataclass
class DaeExport:
    dae: bytes
    textures: dict[str, bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _safe_id(raw: str | None, index: int, used: set[str], prefix: str = "mat") -> str:
    """Sanitize an original name into a valid COLLADA xs:ID, falling back to <prefix>_<index>, and
    disambiguating duplicates with a numeric suffix."""
    base = _INVALID_ID.sub("_", raw).strip("_") if raw else ""
    if not base or base[0].isdigit():
        base = f"{prefix}_{base}" if base else f"{prefix}_{index}"
    name = base
    n = 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def _world_matrices(doc: Document) -> dict[int, list[float]]:
    parents = doc.parents()
    cache: dict[int, list[float]] = {}

    def world(i: int) -> list[float]:
        if i in cache:
            return cache[i]
        local = node_local_matrix(doc.nodes[i])
        p = parents.get(i)
        m = mat4_mul(world(p), local) if p is not None else local
        cache[i] = m
        return m

    for i in range(len(doc.nodes)):
        world(i)
    return cache


def _apply(m: list[float], v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _rotate(m: list[float], v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    return (m[0] * x + m[4] * y + m[8] * z, m[1] * x + m[5] * y + m[9] * z, m[2] * x + m[6] * y + m[10] * z)


def _accessor_values(doc: Document, idx: int) -> list[tuple[float, ...]]:
    acc = doc.json["accessors"][idx]
    bv = doc.json["bufferViews"][acc["bufferView"]]
    n = _TYPE_N[acc["type"]]
    fmt = _COMP_FMT[acc["componentType"]]
    size = _COMP_SIZE[acc["componentType"]]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride") or size * n
    out = []
    for i in range(acc["count"]):
        off = start + i * stride
        out.append(struct.unpack_from(f"<{n}{fmt}", doc.bin, off))
    return out


def _floats(values: Sequence[tuple[float, ...]]) -> str:
    return " ".join(f"{c:.6f}" for v in values for c in v)


def export_dae(doc: Document, textures: dict[str, bytes] | None, name: str = "model") -> DaeExport:
    """All primitives in scene (world) space, pose at export time, one geometry+material per glTF primitive."""
    textures = textures or {}
    warnings: list[str] = []
    world = _world_matrices(doc)

    used_mat_ids: set[str] = set()
    used_img_ids: set[str] = set()
    used_geom_ids: set[str] = set()
    tex_out: dict[str, bytes] = {}

    images_xml: list[str] = []
    effects_xml: list[str] = []
    materials_xml: list[str] = []
    geometries_xml: list[str] = []
    instances_xml: list[str] = []  # <node> entries under the visual scene

    mat_ref_ids: dict[int, str] = {}  # glTF material index -> collada material id (one effect+material per glTF mat)

    def material_ids(mi: int) -> tuple[str, str]:
        """Create (once) the <effect>/<material> pair for a glTF material index, returning (effect_id, material_id)."""
        if mi in mat_ref_ids:
            return mat_ref_ids[mi], mat_ref_ids[mi]
        mat = doc.materials[mi] if mi >= 0 else {}
        raw_name = mat.get("name")
        mat_id = _safe_id(raw_name, mi, used_mat_ids, "mat")
        effect_id = f"{mat_id}-effect"
        effects_xml.append(
            _effect_xml(doc, mi, mat, effect_id, mat_id, textures, tex_out, used_img_ids, images_xml, warnings)
        )
        materials_xml.append(
            f'    <material id="{mat_id}" name="{escape(mat_id)}">\n'
            f'      <instance_effect url="#{effect_id}"/>\n    </material>'
        )
        mat_ref_ids[mi] = mat_id
        return effect_id, mat_id

    node_count = 0
    for ni, node in enumerate(doc.nodes):
        if "mesh" not in node:
            continue
        m = world[ni]
        for pi, prim in enumerate(doc.meshes[node["mesh"]]["primitives"]):
            if prim.get("mode", 4) != 4:
                warnings.append(
                    f"{node.get('name')}: primitive mode {prim.get('mode')} is not a triangle list, skipped"
                )
                continue
            attrs = prim["attributes"]
            positions = _accessor_values(doc, attrs["POSITION"])
            normals = _accessor_values(doc, attrs["NORMAL"]) if "NORMAL" in attrs else None
            uvs = _accessor_values(doc, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
            indices: list[int] = (
                [int(v[0]) for v in _accessor_values(doc, prim["indices"])]
                if "indices" in prim
                else list(range(len(positions)))
            )

            world_positions = [_apply(m, (p[0], p[1], p[2])) for p in positions]
            world_normals = [_rotate(m, (n_[0], n_[1], n_[2])) for n_ in normals] if normals else None

            base = node.get("name") or f"node{ni}"
            geom_id = _safe_id(f"{base}_{pi}", node_count, used_geom_ids, "geom")
            node_count += 1
            mi = prim.get("material", -1)
            _, mat_id = material_ids(mi)

            geometries_xml.append(
                _geometry_xml(geom_id, world_positions, world_normals, uvs, indices, bool(normals), bool(uvs))
            )
            bind_vertex_input = (
                '\n          <bind_vertex_input semantic="UVSET0" input_semantic="TEXCOORD" input_set="0"/>'
                if uvs
                else ""
            )
            instances_xml.append(
                f'    <node id="{geom_id}_node" name="{escape(base)}">\n'
                f'      <instance_geometry url="#{geom_id}">\n'
                f"        <bind_material>\n          <technique_common>\n"
                f'            <instance_material symbol="{mat_id}" target="#{mat_id}">{bind_vertex_input}\n'
                f"            </instance_material>\n"
                f"          </technique_common>\n        </bind_material>\n"
                f"      </instance_geometry>\n    </node>"
            )

    dae = _assemble_dae(name, images_xml, effects_xml, materials_xml, geometries_xml, instances_xml)
    return DaeExport(dae=dae.encode("utf-8"), textures=tex_out, warnings=warnings)


def _geometry_xml(
    geom_id: str,
    positions: Sequence[tuple[float, float, float]],
    normals: Sequence[tuple[float, float, float]] | None,
    uvs: Sequence[tuple[float, ...]] | None,
    indices: list[int],
    has_normals: bool,
    has_uvs: bool,
) -> str:
    pos_src = f"{geom_id}-positions"
    norm_src = f"{geom_id}-normals"
    uv_src = f"{geom_id}-uvs"
    vtx_id = f"{geom_id}-vertices"

    sources = [
        _source_xml(pos_src, _floats(positions), len(positions), ("X", "Y", "Z")),
    ]
    if has_normals and normals:
        sources.append(_source_xml(norm_src, _floats(normals), len(normals), ("X", "Y", "Z")))
    if has_uvs and uvs:
        flat_uvs = [(uv[0], 1.0 - uv[1]) for uv in uvs]  # flip V: glTF origin top-left, Collada bottom-left
        sources.append(_source_xml(uv_src, _floats(flat_uvs), len(flat_uvs), ("S", "T")))

    inputs = [f'<input semantic="VERTEX" source="#{vtx_id}" offset="0"/>']
    offset = 1
    if has_normals and normals:
        inputs.append(f'<input semantic="NORMAL" source="#{norm_src}" offset="{offset}"/>')
        offset += 1
    if has_uvs and uvs:
        inputs.append(f'<input semantic="TEXCOORD" source="#{uv_src}" offset="{offset}" set="0"/>')
        offset += 1

    p_values = []
    for i in range(0, len(indices), 3):
        for k in range(3):
            vi = indices[i + k]
            p_values.append(str(vi))
            if has_normals and normals:
                p_values.append(str(vi))
            if has_uvs and uvs:
                p_values.append(str(vi))
    p_text = " ".join(p_values)
    tri_count = len(indices) // 3

    return (
        f'  <geometry id="{geom_id}" name="{geom_id}">\n'
        f"    <mesh>\n"
        + "\n".join(sources)
        + f'\n      <vertices id="{vtx_id}"><input semantic="POSITION" source="#{pos_src}"/></vertices>\n'
        f'      <triangles count="{tri_count}">\n'
        f"        " + "\n        ".join(inputs) + "\n"
        f"        <p>{p_text}</p>\n"
        f"      </triangles>\n"
        f"    </mesh>\n"
        f"  </geometry>"
    )


def _source_xml(src_id: str, flat_values: str, count: int, params: tuple[str, ...]) -> str:
    stride = len(params)
    array_id = f"{src_id}-array"
    param_tags = "".join(f'<param name="{p}" type="float"/>' for p in params)
    return (
        f'      <source id="{src_id}">\n'
        f'        <float_array id="{array_id}" count="{count * stride}">{flat_values}</float_array>\n'
        f"        <technique_common>\n"
        f'          <accessor source="#{array_id}" count="{count}" stride="{stride}">{param_tags}</accessor>\n'
        f"        </technique_common>\n"
        f"      </source>"
    )


def _effect_xml(
    doc: Document,
    mi: int,
    mat: dict,
    effect_id: str,
    mat_label: str,
    textures: dict[str, bytes],
    out: dict[str, bytes],
    used_img_ids: set[str],
    images_xml: list[str],
    warnings: list[str],
) -> str:
    pbr = mat.get("pbrMetallicRoughness", {})
    base = pbr.get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
    metallic = pbr.get("metallicFactor", 0.0)
    roughness = pbr.get("roughnessFactor", 0.5)
    alpha = base[3] if len(base) > 3 else 1.0
    shininess = max(1.0, (1.0 - roughness) * 300.0)

    newparams: list[str] = []  # profile_COMMON-level <newparam> blocks (surfaces/samplers)
    diffuse_tag = f'<color sid="diffuse">{base[0]:.4f} {base[1]:.4f} {base[2]:.4f} 1</color>'
    bc = pbr.get("baseColorTexture")
    if bc is not None:
        img_id, _ = _emit_texture(doc, bc["index"], f"{mat_label}_BC.png", out, used_img_ids, images_xml)
        if img_id:
            surface_sid = f"{mat_label}-diffuse-surface"
            sampler_sid = f"{mat_label}-diffuse-sampler"
            newparams.append(
                f'<newparam sid="{surface_sid}"><surface type="2D"><init_from>{img_id}</init_from></surface></newparam>'
            )
            newparams.append(
                f'<newparam sid="{sampler_sid}"><sampler2D><source>{surface_sid}</source></sampler2D></newparam>'
            )
            diffuse_tag = f'<texture texture="{sampler_sid}" texcoord="UVSET0"/>'

    normal = mat.get("normalTexture")
    if normal is not None:
        img_id, _ = _emit_texture(doc, normal["index"], f"{mat_label}_N.png", out, used_img_ids, images_xml)
        if img_id:
            warnings.append(
                f"{mat_label}: normal map texture is embedded as an unused surface/sampler pair; "
                f"strength/scaling not applied (no standard Collada-phong normal map slot)"
            )
            surface_sid = f"{mat_label}-bump-surface"
            sampler_sid = f"{mat_label}-bump-sampler"
            newparams.append(
                f'<newparam sid="{surface_sid}"><surface type="2D"><init_from>{img_id}</init_from></surface></newparam>'
            )
            newparams.append(
                f'<newparam sid="{sampler_sid}"><sampler2D><source>{surface_sid}</source></sampler2D></newparam>'
            )

    if pbr.get("metallicRoughnessTexture") is not None:
        warnings.append(
            f"{mat_label}: metallic/roughness texture has no Collada-phong equivalent, only the factors are kept"
        )
    if mat.get("occlusionTexture") is not None:
        warnings.append(f"{mat_label}: ambient occlusion texture is dropped (no AO slot in Collada phong)")
    if "KHR_materials_clearcoat" in (mat.get("extensions") or {}):
        warnings.append(f"{mat_label}: clearcoat (paint lacquer) is dropped, only base colour/gloss is kept")
    if mat.get("alphaMode") == "BLEND":
        warnings.append(
            f"{mat_label}: transparency approximated with <transparency> only (no physically based blending)"
        )
    if "KHR_materials_unlit" in (mat.get("extensions") or {}):
        warnings.append(f"{mat_label}: unlit material approximated as plain phong with zero specular")
    if mat.get("emissiveTexture") is not None:
        warnings.append(f"{mat_label}: emissive texture is dropped, only the factor is kept")

    emissive = mat.get("emissiveFactor") or [0, 0, 0]
    specular = metallic
    # Index of refraction: Collada/Blender compute this from transparency internally for some material models,
    # which produces negative/undefined values for near-opaque materials and triggers a harmless but noisy
    # "IOR of negative value is not allowed" warning on import. Supplying a fixed, physically reasonable value
    # (1.5, typical for glass/clear coat; equally fine as a default for opaque paint/plastic) avoids that warning.
    ior = 1.5
    newparams_xml = "\n      " + "\n      ".join(newparams) if newparams else ""
    return (
        f'  <effect id="{effect_id}">\n'
        f"    <profile_COMMON>{newparams_xml}\n"
        f'      <technique sid="common">\n'
        f"        <phong>\n"
        f"          <emission><color>{emissive[0]:.4f} {emissive[1]:.4f} {emissive[2]:.4f} 1</color></emission>\n"
        f"          <ambient><color>{base[0]:.4f} {base[1]:.4f} {base[2]:.4f} 1</color></ambient>\n"
        f"          <diffuse>{diffuse_tag}</diffuse>\n"
        f"          <specular><color>{specular:.4f} {specular:.4f} {specular:.4f} 1</color></specular>\n"
        f"          <shininess><float>{shininess:.1f}</float></shininess>\n"
        f'          <transparent opaque="A_ONE"><color>1 1 1 {alpha:.4f}</color></transparent>\n'
        f"          <transparency><float>{alpha:.4f}</float></transparency>\n"
        f"          <index_of_refraction><float>{ior:.4f}</float></index_of_refraction>\n"
        f"        </phong>\n"
        f"      </technique>\n"
        f"    </profile_COMMON>\n"
        f"  </effect>"
    )


def _emit_texture(
    doc: Document, tex_idx: int, out_name: str, out: dict[str, bytes], used_img_ids: set[str], images_xml: list[str]
) -> tuple[str | None, str | None]:
    try:
        img = doc.json["images"][doc.json["textures"][tex_idx]["source"]]
        bv = doc.json["bufferViews"][img["bufferView"]]
    except (KeyError, IndexError):
        return None, None
    data = bytes(doc.bin[bv["byteOffset"] : bv["byteOffset"] + bv["byteLength"]])
    out[out_name] = data
    img_id = _safe_id(out_name.rsplit(".", 1)[0], len(used_img_ids), used_img_ids, "img")
    images_xml.append(f'  <image id="{img_id}"><init_from>textures/{out_name}</init_from></image>')
    return img_id, out_name


def _assemble_dae(
    name: str,
    images_xml: list[str],
    effects_xml: list[str],
    materials_xml: list[str],
    geometries_xml: list[str],
    instances_xml: list[str],
) -> str:
    scene_id = _INVALID_ID.sub("_", name) or "scene"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n'
        "  <asset>\n"
        "    <up_axis>Y_UP</up_axis>\n"
        "  </asset>\n"
        "  <library_images>\n" + "\n".join(images_xml) + "\n  </library_images>\n"
        "  <library_effects>\n" + "\n".join(effects_xml) + "\n  </library_effects>\n"
        "  <library_materials>\n" + "\n".join(materials_xml) + "\n  </library_materials>\n"
        "  <library_geometries>\n" + "\n".join(geometries_xml) + "\n  </library_geometries>\n"
        f'  <library_visual_scenes>\n    <visual_scene id="{scene_id}" name="{escape(name)}">\n'
        + "\n".join(instances_xml)
        + f"\n    </visual_scene>\n  </library_visual_scenes>\n"
        f'  <scene><instance_visual_scene url="#{scene_id}"/></scene>\n'
        "</COLLADA>\n"
    )


_WARNING_PATTERNS: list[tuple[str, str]] = [
    ("ambient occlusion texture is dropped", "ambient occlusion textures dropped (no AO slot in MTL)"),
    (
        "transparency approximated with 'd' only",
        "materials used approximated transparency ('d' only, no real blending)",
    ),
    (
        "metallic/roughness texture has no MTL equivalent",
        "materials dropped their metallic/roughness texture (factors kept)",
    ),
    ("clearcoat (paint lacquer) is dropped", "materials dropped clearcoat (paint lacquer); base colour/gloss kept"),
    ("emissive texture is dropped", "materials dropped their emissive texture (factor kept)"),
    ("normal map exported as map_Bump", "normal maps exported as map_Bump (strength/scaling not applied)"),
]


def summarize_dae_warnings(warnings: list[str]) -> list[str]:
    """Collapse repetitive per-material DAE warnings into counted summaries, for console output.

    The full, uncollapsed list is still what callers should write into a sidecar (e.g. obj.json); this is only
    for a readable CLI summary when a vehicle has dozens of materials repeating the same limitation."""
    counts: Counter[str] = Counter()
    other: list[str] = []
    for w in warnings:
        for needle, label in _WARNING_PATTERNS:
            if needle in w:
                counts[label] += 1
                break
        else:
            other.append(w)
    return [f"{n} × {label}" for label, n in counts.items()] + other
