"""Convert a Godot `.tscn` (+ referenced materials) into `scene-overrides.json` and collect facts about the scene.

The Tesla app instances the imported GLB and then applies the `.tscn` on top: node transforms, visibility and
per-surface materials. The GLB alone renders wrong (placeholder materials, wheel pivots at the origin), so the card
needs these overrides. The same converter handles `.obj`-based scenes (brakes, charge cables, Model S/X).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..godot.math import transform_to_mat4
from ..godot.resolve import BinaryResourceError, ResourceNotFound, ResourceRoot
from ..godot.text import GodotFile
from ..godot.values import Call, ExtRef, Ident, SubRef, color, node_path, transform12
from .materials import binary_unsupported, material_from_file, normalize_material

log = logging.getLogger(__name__)

MATERIAL_TYPES = ("SpatialMaterial", "ShaderMaterial")


@dataclass
class ClipSource:
    player: str
    clip: str
    ext_rel: str | None = None  # external Animation .tres
    sub_id: int | None = None  # inline sub_resource


@dataclass
class MarkerInfo:
    name: str
    node: str
    parent: str | None
    position: list[float]
    kind: str  # "marker" (Marker.gd, marker_name) | "locator" (root marker_locators_paths)


@dataclass
class SceneFacts:
    scene_rel: str
    root_name: str
    root_type: str | None
    root_instance: str | None  # rel path of the instanced scene (GLB / tscn) or None
    root_script: str | None
    root_transform: list[float] | None  # 16 col-major if the root has a transform
    bindings: dict[str, tuple[str, str]] = field(default_factory=dict)  # prop → (raw NodePath, leaf node name)
    root_props: dict[str, Any] = field(default_factory=dict)
    players: list[ClipSource] = field(default_factory=list)
    markers: list[MarkerInfo] = field(default_factory=list)
    packed_scene_props: dict[str, str] = field(default_factory=dict)  # root prop → rel tscn (brakes_* etc.)
    packed_scenes: list[str] = field(default_factory=list)  # every ext PackedScene .tscn referenced
    inline_meshes: list[str] = field(default_factory=list)
    node_names: set[str] = field(default_factory=set)
    node_parents: dict[str, str | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_global_lights(self) -> bool:
        return any(k.endswith("_global_path") for k in self.bindings)


@dataclass
class SceneResult:
    overrides: dict[str, Any]
    facts: SceneFacts
    gf: GodotFile


def leaf_of(path: str) -> str:
    """`../ROOT/Door_LF_Spatial/Front_Left_Door` → `Front_Left_Door`; strips a `:property` suffix."""
    p = path.split(":", 1)[0]
    return p.rstrip("/").rsplit("/", 1)[-1]


def _simplify(gf: GodotFile, v: Any) -> Any:
    if isinstance(v, ExtRef):
        return gf.ext_rel(v)
    if isinstance(v, SubRef):
        return f"sub_resource#{v.id}"
    if isinstance(v, Ident):
        return v.name
    if isinstance(v, Call):
        if v.name == "NodePath":
            return v.args[0] if v.args else ""
        c = color(v)
        if c is not None:
            return c
        return {v.name: [_simplify(gf, a) for a in v.args]}
    if isinstance(v, list):
        return [_simplify(gf, x) for x in v]
    if isinstance(v, dict):
        return {str(_simplify(gf, k)): _simplify(gf, x) for k, x in v.items()}
    return v


class SceneConverter:
    def __init__(self, root: ResourceRoot):
        self.root = root
        self.materials: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []

    # ---------- materials ----------
    def material_key(self, gf: GodotFile, ref: Any) -> str | None:
        if isinstance(ref, ExtRef):
            rel = gf.ext_rel(ref)
            if not rel:
                return None
            if rel not in self.materials:
                self.materials[rel] = self._load_material(rel)
            return rel
        if isinstance(ref, SubRef):
            blk = gf.sub(ref)
            if blk is None:
                return None
            key = f"sub_resource#{ref.id}"
            if key not in self.materials:
                self.materials[key] = normalize_material(gf, blk.props, blk.type or "SpatialMaterial", key, None)
            return key
        return None

    def _load_material(self, rel: str) -> dict[str, Any]:
        try:
            mgf = self.root.load(rel)
        except ResourceNotFound:
            self.warnings.append(f"material not found: {rel}")
            return {"name": rel.rsplit("/", 1)[-1], "kind": "missing", "source": rel}
        except BinaryResourceError as e:
            self.warnings.append(str(e))
            return binary_unsupported(rel)
        return material_from_file(mgf, rel)

    # ---------- scene ----------
    def convert(self, scene_res: str) -> SceneResult:
        rel = self.root.rel(scene_res)
        gf = self.root.load(rel)
        self.root.prefetch_binary(
            [e.rel for e in gf.ext.values() if e.type == "Material"] + self._sibling_materials(rel)
        )
        nodes: dict[str, dict[str, Any]] = {}
        root_props: dict[str, Any] = {}
        root_blk = None
        facts = SceneFacts(
            scene_rel=rel, root_name="", root_type=None, root_instance=None, root_script=None, root_transform=None
        )

        for ext in gf.ext.values():
            if ext.type == "PackedScene" and ext.rel.lower().endswith(".tscn"):
                facts.packed_scenes.append(ext.rel)

        for b in gf.nodes:
            name = b.name or ""
            parent = b.parent
            props = b.props
            if parent is None:  # scene root
                root_blk = b
                facts.root_name = name
                facts.root_type = b.type
                facts.root_instance = self._recovered_glb(gf.ext_rel(b.attrs.get("instance")))
                for k, v in props.items():
                    if k == "__parse_errors__":
                        continue
                    root_props[k] = _simplify(gf, v)
                    np_ = node_path(v)
                    if np_ is not None and k.endswith("_path"):
                        facts.bindings[k] = (np_, leaf_of(np_))
                    if isinstance(v, ExtRef) and gf.ext.get(v.id) and gf.ext[v.id].type == "PackedScene":
                        facts.packed_scene_props[k] = gf.ext[v.id].rel
                if isinstance(props.get("script"), ExtRef):
                    facts.root_script = gf.ext_rel(props["script"])
                t = transform12(props.get("transform"))
                if t:
                    facts.root_transform = transform_to_mat4(t)
                nodes.setdefault(name, {"parent": None})
                facts.node_names.add(name)
                facts.node_parents[name] = None
                continue

            n = nodes.setdefault(name, {"parent": parent})
            facts.node_names.add(name)
            facts.node_parents.setdefault(name, parent)
            if b.type:
                n["type"] = b.type
            inst = gf.ext_rel(b.attrs.get("instance"))
            if inst:
                n["instance"] = inst
            if "visible" in props:
                n["visible"] = bool(props["visible"])
            t = transform12(props.get("transform"))
            if t:
                n["matrix"] = transform_to_mat4(t)
            mesh = props.get("mesh")
            if isinstance(mesh, ExtRef):
                n["mesh"] = gf.ext_rel(mesh)
            elif isinstance(mesh, SubRef):  # inline ArrayMesh: not exportable here, only recorded
                n["inline_mesh"] = True
                facts.inline_meshes.append(name)
                sub = gf.sub(mesh)
                if (
                    sub is not None
                ):  # ArrayMesh surfaces carry their material inline: surfaces/N = {"material": ExtResource}
                    for k, v in sub.props.items():
                        m = re.match(r"surfaces/(\d+)$", k)
                        if m and isinstance(v, dict) and "material" in v:
                            mk = self.material_key(gf, v["material"])
                            if mk:
                                n.setdefault("materials", {})[m.group(1)] = mk
            if "material" in props:  # CSG / single-material nodes
                mk = self.material_key(gf, props["material"])
                if mk:
                    n.setdefault("materials", {})["0"] = mk
            for k, v in props.items():
                m = re.match(r"material/(\d+)$", k)
                if m:
                    mk = self.material_key(gf, v)
                    if mk:
                        n.setdefault("materials", {})[m.group(1)] = mk
            if b.type == "AnimationPlayer":
                for k, v in props.items():
                    if k.startswith("anims/"):
                        clip = k[len("anims/") :]
                        if isinstance(v, ExtRef):
                            facts.players.append(ClipSource(name, clip, ext_rel=gf.ext_rel(v)))
                        elif isinstance(v, SubRef):
                            facts.players.append(ClipSource(name, clip, sub_id=v.id))
            script = gf.ext_rel(props.get("script")) if isinstance(props.get("script"), ExtRef) else None
            if script and script.endswith("Marker.gd") and isinstance(props.get("marker_name"), str):
                pos = list(t[9:12]) if t else [0.0, 0.0, 0.0]
                facts.markers.append(MarkerInfo(props["marker_name"], name, parent, pos, "marker"))

        # root-level marker locators (Frunk_Marker, Trunk_Marker, Chargeport_Marker, Lock_Marker, Plate_Marker …)
        if root_blk is not None:
            for v in root_blk.props.get("marker_locators_paths", []) or []:
                p = node_path(v)
                if not p:
                    continue
                leaf = leaf_of(p)
                node = nodes.get(leaf)
                pos = node["matrix"][12:15] if node and "matrix" in node else [0.0, 0.0, 0.0]
                parent = node["parent"] if node else (p.rsplit("/", 1)[0] if "/" in p else None)
                facts.markers.append(MarkerInfo(_locator_name(leaf), leaf, parent, list(pos), "locator"))

        by_name = self._materials_by_name(rel)
        facts.root_props = root_props
        facts.warnings.extend(self.warnings)
        overrides = {
            "source": rel,
            "root": {
                "name": facts.root_name,
                "type": facts.root_type,
                "instance": facts.root_instance,
                "matrix": facts.root_transform,
            },
            "root_props": root_props,
            "nodes": nodes,
            "materials": self.materials,
            "materials_by_name": by_name,
        }
        return SceneResult(overrides, facts, gf)

    def _recovered_glb(self, inst: str | None) -> str | None:
        """Scenes that instance an imported `.fbx` / `.dae` get the GLB GDRE recovered from the import instead."""
        if not inst or inst.lower().endswith((".glb", ".tscn")):
            return inst
        cand = inst.rsplit(".", 1)[0] + ".glb"
        return cand if self.root.exists(cand) else inst

    def _sibling_materials(self, scene_rel: str) -> list[str]:
        scene_path = self.root.require(scene_rel)
        out = []
        for p in sorted(scene_path.parent.iterdir()):
            if p.suffix in (".tres", ".material") and p.is_file():
                out.append(self._rel_of(p))
        return out

    def _rel_of(self, p) -> str:
        rel = p.relative_to(self.root.root).as_posix() if p.is_relative_to(self.root.root) else p.name
        return rel[len(".assets/") :] if rel.startswith(".assets/") else rel

    def _materials_by_name(self, scene_rel: str) -> dict[str, str]:
        """Index every material file next to the scene by file stem and resource_name (see godot2three.py)."""
        by_name: dict[str, str] = {}
        scene_path = self.root.require(scene_rel)
        for p in sorted(scene_path.parent.iterdir()):
            if p.suffix not in (".tres", ".material") or not p.is_file():
                continue
            rel = self._rel_of(p)
            gf = self.root.try_load(rel)
            if gf is None or gf.resource_type not in MATERIAL_TYPES:
                continue
            if rel not in self.materials:
                self.materials[rel] = material_from_file(gf, rel)
            by_name.setdefault(p.stem, rel)
            rn = self.materials[rel].get("name")
            if isinstance(rn, str) and rn:
                by_name.setdefault(rn, rel)
        return by_name


def _locator_name(leaf: str) -> str:
    """`Frunk_Marker` → `frunk`, `Chargeport_Marker` → `charge_port`, `Lock_Marker` → `lock`."""
    base = re.sub(r"_?Marker$", "", leaf)
    base = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", base).lower()
    return {"chargeport": "charge_port"}.get(base, base)


def convert_scene(root: ResourceRoot, scene_res: str) -> SceneResult:
    return SceneConverter(root).convert(scene_res)
