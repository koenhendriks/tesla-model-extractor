"""Assemble one vehicle of a pack into a self-contained GLB for Unreal (or any glTF importer).

Input is the pack the extractor already builds (`BuildResult`: manifest + files). The vehicle GLB is the base;
the `.overrides.json` supplies node transforms, visibility and per-surface materials, wheels and brakes are merged
under their pivots, closure animations are attached to the pivot nodes and a sidecar JSON describes what the
glTF format cannot carry (light groups, hidden parts, variants, paints, markers).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..manifest import BuildResult
from .anim import add_animation
from .gltf import Document, quat_mul, set_matrix, yaw_quat
from .materials import MaterialBuilder, Paint
from .objmesh import add_obj_mesh
from .textures import TextureRepacker

log = logging.getLogger(__name__)

EMPTY_NODE_TYPES = {"Spatial", "Position3D"}
DEFAULT_VARIANTS = frozenset({"plate_eu"})
# variants that are alternatives of each other: selecting one deselects the other
EXCLUSIVE = {"plate_eu": "plate_us", "plate_us": "plate_eu"}


@dataclass
class ExportOptions:
    variants: frozenset[str] = DEFAULT_VARIANTS
    keep_all: bool = False
    wheel: str | None = "default"  # API wheel name, "default" or None (no wheels)
    brakes: str | None = "default"  # brake set name, "default" or None
    paint: str | None = None  # paint name from the table; None = the app's fallback paint
    yaw_deg: float = 0.0
    flip_normal_green: bool = True


@dataclass
class ExportResult:
    glb: bytes
    sidecar: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    extra_files: dict[str, bytes] = field(default_factory=dict)  # relative path → bytes (separate wheels, cables …)


class Files:
    def __init__(self, files: dict[str, bytes | Path]):
        self.files = files

    def read(self, rel: str) -> bytes | None:
        v = self.files.get(rel)
        if v is None:
            return None
        return v if isinstance(v, bytes) else Path(v).read_bytes()

    def json(self, rel: str) -> dict[str, Any] | None:
        data = self.read(rel)
        return json.loads(data) if data is not None else None


class Assembler:
    def __init__(self, result: BuildResult, opt: ExportOptions):
        self.result = result
        self.manifest = result.manifest
        self.files = Files(result.files)
        self.opt = opt
        self.warnings: list[str] = []
        self.doc = Document.empty("scene", "Root")
        self.textures = TextureRepacker(self.files.read)
        self.mats = MaterialBuilder(self.doc, self.textures, self._paint(), opt.flip_normal_green)
        self.mats.paint_explicit = bool(opt.paint)
        self.hidden: set[int] = set()
        self._overridden: set[tuple[int, int]] = set()  # (mesh, primitive) with a .tscn material

    # ---------- setup ----------
    def _paint(self) -> Paint | None:
        table = self.manifest.get("paints") or {}
        colors = table.get("colors") or {}
        if self.opt.paint:
            entry = colors.get(self.opt.paint) or next(
                (v for k, v in colors.items() if k.lower() == self.opt.paint.lower()), None
            )
            if entry is None:
                self.warnings.append(f"unknown paint {self.opt.paint!r}; using the app's fallback paint")
            else:
                return Paint.from_table(self.opt.paint, entry)
        fb = table.get("fallback")
        return Paint.from_table("fallback", fb) if fb else None

    def reset(self, scene_name: str, root_name: str) -> None:
        self.doc = Document.empty(scene_name, root_name)
        self.mats = MaterialBuilder(self.doc, self.textures, self.mats.paint, self.opt.flip_normal_green)
        self.mats.paint_explicit = bool(self.opt.paint)
        self.hidden = set()
        self._overridden = set()

    # ---------- scenes ----------
    def attach_scene(self, overrides_rel: str, parent: int, glb_hint: str | None = None) -> int | None:
        """Build the scene described by an `*.overrides.json` under `parent`; returns its root node index."""
        ov = self.files.json(overrides_rel)
        if ov is None:
            self.warnings.append(f"overrides missing: {overrides_rel}")
            return None
        root_info = ov.get("root") or {}
        glb_rel = glb_hint or root_info.get("instance")
        if glb_rel and not glb_rel.lower().endswith(".glb"):
            glb_rel = None
        root_name = root_info.get("name") or "ROOT"
        if glb_rel:
            data = self.files.read(glb_rel)
            if data is None:
                self.warnings.append(f"GLB missing: {glb_rel}")
                return None
            root = self.doc.merge(Document.from_glb(data), parent)
        else:
            root = self.doc.add_node(root_name, parent)
        if root_info.get("matrix"):
            set_matrix(self.doc.nodes[root], root_info["matrix"])
        self._apply_nodes(ov, root, overrides_rel)
        self._fallback_materials(ov, root)
        return root

    def _subtree_index(self, root: int) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for i in self.doc.descendants(root):
            out.setdefault(self.doc.nodes[i].get("name", ""), []).append(i)
        return out

    def _apply_nodes(self, ov: dict[str, Any], root: int, overrides_rel: str) -> None:
        materials: dict[str, Any] = ov.get("materials", {})
        created: dict[str, int] = {}  # tscn node name → index (tscn-only nodes, may shadow GLB names)
        skipped: set[str] = set()  # collision areas, players, tweens …: their children are skipped silently
        index = self._subtree_index(root)
        for name, n in ov.get("nodes", {}).items():
            if n.get("parent") is None:  # scene root, handled by attach_scene
                continue
            parent_leaf = str(n["parent"]).rstrip("/").rsplit("/", 1)[-1]
            parent = root if n["parent"] == "." else created.get(parent_leaf, _first(index, parent_leaf))
            if parent is None:
                if parent_leaf not in skipped:
                    self.warnings.append(f"{overrides_rel}: parent {n['parent']!r} of {name} not found")
                skipped.add(name)
                continue
            idx = self._resolve(name, n, parent, index)
            if idx is None:
                idx = self._create(name, n, parent, overrides_rel)
                if idx is None:
                    skipped.add(name)
                    continue
                created[name] = idx
                index.setdefault(name, []).append(idx)
            node = self.doc.nodes[idx]
            if n.get("matrix"):
                set_matrix(node, n["matrix"])
            if n.get("visible") is False:
                self.hidden.add(idx)
            if n.get("materials") and "mesh" in node:
                self._apply_materials(idx, n["materials"], materials)

    def _resolve(self, name: str, n: dict[str, Any], parent: int, index: dict[str, list[int]]) -> int | None:
        if n.get("type") or n.get("instance"):
            return None  # declared in the .tscn, so never an inherited GLB node even when a mesh shares the name
        hits = index.get(name) or []
        children = self.doc.nodes[parent].get("children", [])
        for h in hits:
            if h in children:
                return h
        if hits and not n.get("mesh"):
            return hits[0]
        if n.get("materials") and not n.get("type"):
            # Godot renamed the imported mesh node when it clashed with the scene root (GeminiDark → GeminiDark2)
            cands = [
                c
                for c in children
                if self.doc.nodes[c].get("name", "").startswith(name) and "mesh" in self.doc.nodes[c]
            ]
            if len(cands) == 1:
                return cands[0]
            mesh_children = [c for c in children if "mesh" in self.doc.nodes[c]]
            if len(mesh_children) == 1:
                return mesh_children[0]
        return None

    def _create(self, name: str, n: dict[str, Any], parent: int, overrides_rel: str) -> int | None:
        inst = n.get("instance")
        if inst and inst.lower().endswith(".tscn"):
            sub_rel = re.sub(r"\.tscn$", ".overrides.json", inst)
            if sub_rel in self.result.files:
                idx = self.attach_scene(sub_rel, parent)
                if idx is not None:
                    self.doc.nodes[idx]["name"] = name
                return idx
            self.warnings.append(f"{overrides_rel}: instanced scene {inst} is not part of the pack; skipped")
            return None
        if inst and inst.lower().endswith(".glb"):
            data = self.files.read(inst)
            if data is None:
                self.warnings.append(f"{overrides_rel}: GLB {inst} missing")
                return None
            idx = self.doc.merge(Document.from_glb(data), parent)
            self.doc.nodes[idx]["name"] = name
            return idx
        mesh = n.get("mesh")
        if isinstance(mesh, str) and mesh.lower().endswith(".obj"):
            data = self.files.read(mesh)
            if data is None:
                self.warnings.append(f"{overrides_rel}: mesh {mesh} missing")
                return None
            idx = self.doc.add_node(name, parent)
            mesh_idx, _ = add_obj_mesh(self.doc, name, data.decode("utf-8", errors="replace"))
            if mesh_idx is not None:
                self.doc.nodes[idx]["mesh"] = mesh_idx
            return idx
        if n.get("type") in EMPTY_NODE_TYPES:
            extras: dict[str, Any] = {"godot_type": n["type"]}
            return self.doc.add_node(name, parent, extras=extras)
        return None

    def _apply_materials(self, idx: int, slots: dict[str, str], materials: dict[str, Any]) -> None:
        node = self.doc.nodes[idx]
        mesh = node["mesh"]
        users = [i for i, nd in enumerate(self.doc.nodes) if nd.get("mesh") == mesh]
        if len(users) > 1:
            mesh = self.doc.clone_mesh(mesh)
            node["mesh"] = mesh
        prims = self.doc.meshes[mesh]["primitives"]
        for slot, key in slots.items():
            i = int(slot)
            if i >= len(prims):  # Godot keeps spare material slots; nothing to paint them on
                log.debug("%s: material slot %d but mesh has %d primitives", node.get("name"), i, len(prims))
                continue
            desc = materials.get(key)
            if desc is None:
                continue
            if desc.get("kind") in ("binary_unsupported", "missing"):
                self.warnings.append(f"{node.get('name')}: material {key} could not be converted ({desc.get('kind')})")
                continue
            prims[i]["material"] = self.mats.build(key, desc, _texcoords(prims[i]))
            self._overridden.add((mesh, i))

    def _fallback_materials(self, ov: dict[str, Any], root: int) -> None:
        by_name: dict[str, str] = ov.get("materials_by_name") or {}
        materials: dict[str, Any] = ov.get("materials", {})
        if not by_name:
            return
        for idx in self.doc.descendants(root):
            node = self.doc.nodes[idx]
            if "mesh" not in node:
                continue
            for pi, prim in enumerate(self.doc.meshes[node["mesh"]]["primitives"]):
                if (node["mesh"], pi) in self._overridden or "material" not in prim:
                    continue
                glb_name = self.doc.materials[prim["material"]].get("name", "")
                key = by_name.get(glb_name)
                if not key or key not in materials or materials[key].get("kind") in ("binary_unsupported", "missing"):
                    continue
                prim["material"] = self.mats.build(key, materials[key], _texcoords(prim))
                self._overridden.add((node["mesh"], pi))

    # ---------- vehicle ----------
    def export_vehicle(self, model_id: str) -> ExportResult:
        model = self.manifest["models"][model_id]
        codename = model.get("codename") or model_id
        self.reset(codename, f"{codename}_Root")
        outer = self.doc.root
        root = self.attach_scene(model["overrides"], outer, model.get("glb"))
        if root is None:
            raise ValueError(f"{model_id}: vehicle scene could not be assembled")
        if abs(self.opt.yaw_deg) > 1e-9:
            node = self.doc.nodes[outer]
            node["rotation"] = quat_mul(yaw_quat(self.opt.yaw_deg), node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
        removed = self._visibility(model, root)
        wheel = self._wheels(model, root)
        brakes = self._brakes(model, root)
        anims = self._animations(model, root)
        for idx in self.hidden:
            self.doc.nodes[idx].setdefault("extras", {})["visible"] = False
        markers = self._markers(model, root)
        self.doc.compact()
        hidden_names = sorted({n["name"] for n in self.doc.nodes if n.get("extras", {}).get("visible") is False})
        self.warnings += self.textures.warnings + self.mats.warnings
        self.textures.warnings.clear()
        self.mats.warnings.clear()
        sidecar = {
            "format": 1,
            "generated_by": {"tool": "tesla-model-extractor", "version": __version__},
            "app_version": self.manifest.get("app_version"),
            "model": model_id,
            "name": model.get("name"),
            "codename": codename,
            "file": f"{codename}.glb",
            "units": "metres, Y-up, nose towards -Z (glTF); the importer converts to the engine's axes",
            "paint": self.mats.paint.name if self.mats.paint else None,
            "paints": self.manifest.get("paints"),
            "wheel": wheel,
            "brakes": brakes,
            "variants_applied": sorted(self.opt.variants),
            "variants_available": sorted(k for k, v in (model.get("variants") or {}).items() if v),
            "removed_nodes": sorted(removed),
            "hidden_nodes": hidden_names,
            "lights": model.get("lights"),
            "lights_eu": model.get("lights_eu"),
            "closures": model.get("closures"),
            "animations": anims,
            "pivots": model.get("pivots"),
            "markers": markers,
            "additive_materials": sorted({m for m in self.mats.additive if m}),
            "ground_nodes": sorted(
                n["name"]
                for n in self.doc.nodes
                if re.search(r"(?i)ground|^floor$|shadow", n.get("name", "")) and "mesh" in n
            ),
            "environment": self.manifest.get("environment"),
            "warnings": list(dict.fromkeys(self.warnings)),
        }
        return ExportResult(self.doc.to_glb(), sidecar, list(dict.fromkeys(self.warnings)))

    def _visibility(self, model: dict[str, Any], root: int) -> set[str]:
        index = self._subtree_index(root)

        def nodes_named(names: list[str]) -> list[int]:
            return [i for n in names for i in index.get(n, [])]

        for i in nodes_named(model.get("hide") or []):
            self.hidden.add(i)
        for i in nodes_named(model.get("show") or []):
            self.hidden.discard(i)
        removed: set[str] = set()
        if self.opt.keep_all:
            return removed
        selected = set(self.opt.variants)
        for a, b in EXCLUSIVE.items():
            if a in selected and b in selected:
                selected.discard(min(a, b))
                self.warnings.append(f"variants {a} and {b} exclude each other; using {max(a, b)}")
        for name, spec in (model.get("variants") or {}).items():
            if not spec:
                continue
            drop = spec.get("hide", []) if name in selected else spec.get("show", [])
            keep = spec.get("show", []) if name in selected else spec.get("hide", [])
            for n in drop:
                if n in keep:
                    continue
                for i in index.get(n, []):
                    self.doc.detach(i)
                    self.hidden.discard(i)
                    removed.add(n)
        for n in removed:
            index.pop(n, None)
        # nodes hidden by the scene that the selected variant needs visible
        for name in selected:
            spec = (model.get("variants") or {}).get(name) or {}
            for i in nodes_named(spec.get("show", [])):
                self.hidden.discard(i)
        return removed

    def _wheels(self, model: dict[str, Any], root: int) -> str | None:
        if self.opt.wheel is None:
            return None
        pivots = model.get("pivots", {}).get("wheels") or []
        if not pivots:
            self.warnings.append("no wheel pivots; wheels not attached")
            return None
        wheels: dict[str, Any] = self.manifest.get("wheels") or {}
        api = model.get("default_wheel") if self.opt.wheel == "default" else self.opt.wheel
        entry = wheels.get(api or "") or next((w for k, w in wheels.items() if k.lower() == str(api).lower()), None)
        if entry is None or entry.get("kind") not in ("glb", "obj"):
            self.warnings.append(f"wheel {api!r} not available in the pack; wheels not attached")
            return None
        index = self._subtree_index(root)
        targets: list[tuple[str, int]] = []
        for pivot in pivots:
            p = _first(index, pivot)
            if p is None:
                self.warnings.append(f"wheel pivot {pivot} not found")
            else:
                targets.append((pivot, p))
        if not targets:
            return None
        first_pivot, first_parent = targets[0]
        if entry.get("overrides"):
            first = self.attach_scene(entry["overrides"], first_parent, entry.get("glb"))
        else:
            data = self.files.read(entry["glb"])
            first = self.doc.merge(Document.from_glb(data), first_parent) if data else None
        if first is None:
            return None
        if entry.get("root_matrix"):
            set_matrix(self.doc.nodes[first], entry["root_matrix"])
        # clone with the original names, then give every corner its own suffix
        placed = [(first_pivot, first)] + [(pivot, self.doc.clone_subtree(first, p)) for pivot, p in targets[1:]]
        for pivot, idx in placed:
            tag = _pivot_tag(pivot)
            self._rename(idx, f"Wheel_{tag}", tag)
        return api

    def _brakes(self, model: dict[str, Any], root: int) -> str | None:
        if self.opt.brakes is None:
            return None
        sets: dict[str, Any] = model.get("brakes") or {}
        if not sets:
            return None
        name = self.opt.brakes
        if name == "default":
            name = model.get("brakes_default") or next(iter(sets))
        chosen = sets.get(name or "")
        if not chosen:
            self.warnings.append(f"brake set {name!r} not in pack ({', '.join(sets)})")
            return None
        index = self._subtree_index(root)
        templates: dict[str, int] = {}
        placed: list[tuple[str, int]] = []
        for pivot in model.get("pivots", {}).get("brakes") or []:
            p = _first(index, pivot)
            if p is None:
                continue
            tag = _pivot_tag(pivot)
            side = "front" if tag.endswith("F") else "rear"
            ov_rel = chosen.get(side)
            if not ov_rel:
                continue
            if side in templates:
                placed.append((pivot, self.doc.clone_subtree(templates[side], p)))
                continue
            b = self.attach_scene(ov_rel, p)
            if b is None:
                continue
            templates[side] = b
            placed.append((pivot, b))
        for pivot, idx in placed:
            tag = _pivot_tag(pivot)
            self._rename(idx, f"Brake_{tag}", tag)
        return name

    def _rename(self, idx: int, root_name: str, suffix: str) -> None:
        self.doc.nodes[idx]["name"] = root_name
        for c in self.doc.descendants(idx):
            if c != idx:
                self.doc.nodes[c]["name"] = f"{self.doc.nodes[c].get('name', 'Node')}_{suffix}"

    def _animations(self, model: dict[str, Any], root: int) -> list[dict[str, Any]]:
        index = self._subtree_index(root)
        resolve = {name: hits[0] for name, hits in index.items()}
        resolve["."] = root  # tracks on the scene root itself (Cybertruck suspension)
        closures = {a: ch for ch, names in (model.get("closures") or {}).items() for a in names}
        out = []
        for player, rel in (model.get("animations") or {}).items():
            data = self.files.json(rel)
            if data is None:
                self.warnings.append(f"animation {rel} missing")
                continue
            data.setdefault("name", player)
            st = add_animation(self.doc, data, resolve)
            if st.missing_nodes:
                self.warnings.append(f"{player}: nodes not found {sorted(set(st.missing_nodes))}")
            if st.channels:
                out.append(
                    {"name": player, "length": st.length, "closure": closures.get(player), "channels": st.channels}
                )
        return out

    def _markers(self, model: dict[str, Any], root: int) -> list[dict[str, Any]]:
        index = self._subtree_index(root)
        out = []
        for m in model.get("markers") or []:
            idx = _first(index, m["node"])
            if idx is None:
                continue
            self.doc.nodes[idx].setdefault("extras", {})["marker"] = m["name"]
            wm = self.doc.world_matrix(idx)
            out.append({**m, "world_position": [round(x, 4) for x in wm[12:15]]})
        return out

    # ---------- stand-alone parts ----------
    def export_wheel(self, api_name: str) -> bytes | None:
        entry = (self.manifest.get("wheels") or {}).get(api_name)
        if not entry or entry.get("kind") not in ("glb", "obj"):
            return None
        self.reset(api_name, f"{api_name}_Root")
        if entry.get("overrides"):
            w = self.attach_scene(entry["overrides"], self.doc.root, entry.get("glb"))
        else:
            data = self.files.read(entry["glb"])
            w = self.doc.merge(Document.from_glb(data), self.doc.root) if data else None
        if w is None:
            return None
        if entry.get("root_matrix"):
            set_matrix(self.doc.nodes[w], entry["root_matrix"])
        self.doc.compact()
        return self.doc.to_glb()

    def export_cable(self, port: str) -> bytes | None:
        entry = (self.manifest.get("cables") or {}).get(port)
        if not entry:
            return None
        self.reset(f"Cable_{port}", f"Cable_{port}_Root")
        if self.attach_scene(entry["overrides"], self.doc.root) is None:
            return None
        self.doc.compact()
        return self.doc.to_glb()


def _texcoords(prim: dict[str, Any]) -> frozenset[int]:
    return frozenset(int(k[len("TEXCOORD_") :]) for k in prim.get("attributes", {}) if k.startswith("TEXCOORD_"))


def _first(index: dict[str, list[int]], name: str) -> int | None:
    hits = index.get(name)
    return hits[0] if hits else None


def _pivot_tag(pivot: str) -> str:
    """`Wheel_LF_Spatial` → `LF`, `Brake_RR_Spatial` → `RR`."""
    m = re.search(r"_(LF|RF|LR|RL|RR)(_|$)", pivot)
    return m.group(1) if m else re.sub(r"[^A-Za-z0-9]", "", pivot)
