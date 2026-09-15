"""Build the asset-pack `manifest.json` (format 1) for a selection of vehicles: converts scenes, applies rules.

The manifest is everything the Tesla View card needs to know about a pack; see docs/asset-pack-format.md.
`BuildResult.files` maps pack-relative output path → bytes (generated JSON) or source Path (copied as is).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import PACK_FORMAT, __version__
from .catalog import Catalog, VehicleEntry, resolve_default_wheel, resolve_wheel_family
from .convert.anim import convert_animation_file, convert_clip
from .convert.materials import texture_refs
from .convert.scene import SceneConverter, SceneFacts, SceneResult
from .godot.glb import GlbInfo, read_glb
from .rules import Rules

log = logging.getLogger(__name__)

MODEL_ID_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass
class BuildResult:
    manifest: dict[str, Any]
    files: dict[str, bytes | Path] = field(default_factory=dict)  # pack path → content or source file
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _json_default(o: Any) -> Any:
    if hasattr(o, "id") and o.__class__.__name__ == "SubRef":
        return f"sub_resource#{o.id}"
    if hasattr(o, "name") and o.__class__.__name__ in ("Ident", "Call"):
        return getattr(o, "name", str(o))
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False, default=_json_default) + "\n").encode("utf-8")


def _generated_at() -> str:
    """UTC timestamp; honours SOURCE_DATE_EPOCH for reproducible packs."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    now = dt.datetime.fromtimestamp(int(epoch), dt.UTC) if epoch else dt.datetime.now(dt.UTC)
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def overrides_path(scene_rel: str) -> str:
    """`Ego/Bayberry/Bayberry.tscn` → `Ego/Bayberry/Bayberry.overrides.json`."""
    return re.sub(r"\.tscn$", ".overrides.json", scene_rel)


def anim_path(scene_rel: str, player: str) -> str:
    return f"{scene_rel.rsplit('/', 1)[0]}/animations/{player}.json"


class PackBuilder:
    def __init__(self, cat: Catalog, exclude_paths: list[str] | None = None):
        self.cat = cat
        self.root = cat.root
        self.out = BuildResult(manifest={})
        self.exclude = exclude_paths or ["/Textures/Skins/", "/Skins/"]
        self._scene_cache: dict[str, SceneResult] = {}

    # ---------- files ----------
    def add_file(self, rel: str) -> bool:
        if any(x in "/" + rel for x in self.exclude):
            return False
        if rel in self.out.files:
            return True
        p = self.root.resolve(rel)
        if p is None:
            if rel not in self.out.missing:
                self.out.missing.append(rel)
            return False
        self.out.files[rel] = p
        return True

    def add_json(self, rel: str, data: Any) -> None:
        self.out.files[rel] = _json_bytes(data)

    def add_overrides_refs(self, overrides: dict[str, Any]) -> None:
        for m in overrides["materials"].values():
            for t in texture_refs(m):
                self.add_file(t)
        for n in overrides["nodes"].values():
            mesh = n.get("mesh")
            if isinstance(mesh, str) and "/" in mesh:
                self.add_file(mesh)

    def convert(self, scene_rel: str) -> SceneResult:
        if scene_rel not in self._scene_cache:
            res = SceneConverter(self.root).convert(scene_rel)
            self._scene_cache[scene_rel] = res
            self.out.warnings += [f"{scene_rel}: {w}" for w in res.facts.warnings]
        return self._scene_cache[scene_rel]

    def emit_scene(self, scene_rel: str) -> SceneResult:
        """Convert a scene, write its overrides JSON and pull every referenced file into the pack."""
        res = self.convert(scene_rel)
        self.add_json(overrides_path(scene_rel), res.overrides)
        self.add_overrides_refs(res.overrides)
        if res.facts.root_instance and res.facts.root_instance.lower().endswith(".glb"):
            self.add_file(res.facts.root_instance)
        return res

    # ---------- vehicles ----------
    def build(self, vehicles: list[VehicleEntry], wheel_filter: list[str] | None = None) -> BuildResult:
        cat = self.cat
        manifest: dict[str, Any] = {
            "format": PACK_FORMAT,
            "generated_by": {
                "tool": "tesla-view-extractor",
                "version": __version__,
                "gdre": self.root.gdre.version if self.root.gdre else None,
            },
            "app_version": None,
            "generated_at": _generated_at(),
            "models": {},
            "wheels": {},
            "wheel_aliases": dict(cat.options.wheel_aliases),
            "cables": {},
            "paints": {
                "base_roughness": cat.options.base_roughness,
                "base_metallic": cat.options.base_metallic,
                "fallback": cat.options.fallback_paint,
                "colors": cat.options.paints,
            },
            "environment": cat.environment.to_manifest(),
            "warnings": [],
        }
        families: set[str] = set()
        for v in vehicles:
            model, fam = self.build_model(v)
            manifest["models"][v.id] = model
            if fam:
                families.add(fam)
        # wheels: the selected families (or an explicit list)
        wanted = []
        for w in cat.wheels:
            if not w.present:
                continue
            if wheel_filter is not None:
                if w.api_name in wheel_filter or w.enum_name in wheel_filter:
                    wanted.append(w)
            elif w.family in families:
                wanted.append(w)
        for w in wanted:
            manifest["wheels"][w.api_name] = self.build_wheel(w.scene, w.enum_name, w.family)
        # cables
        for port, sc in cat.options.cable_map.items():
            if not self.root.exists(sc):
                self.out.warnings.append(f"cable scene missing: {sc}")
                continue
            manifest["cables"][port] = self.build_cable(sc)
        # environment panorama
        pano = cat.environment.panorama
        if pano and not self.add_file(pano):
            self.out.warnings.append(f"panorama not found: {pano}")
        manifest["warnings"] = list(dict.fromkeys(self.out.warnings))
        self.out.manifest = manifest
        self.add_json("manifest.json", manifest)
        return self.out

    def build_model(self, v: VehicleEntry) -> tuple[dict[str, Any], str | None]:
        rules = v.rules
        res = self.emit_scene(v.scene)
        f = res.facts
        glb: GlbInfo | None = None
        if f.root_instance and f.root_instance.lower().endswith(".glb"):
            p = self.root.resolve(f.root_instance)
            if p:
                try:
                    glb = read_glb(p)
                except Exception as e:  # noqa: BLE001
                    self.out.warnings.append(f"{v.id}: cannot read GLB {f.root_instance}: {e}")
        known = set(f.node_names) | (glb.node_set if glb else set())
        leaf = {k: v_[1] for k, v_ in f.bindings.items()}  # binding → leaf node

        def nodes_of(bindings: list[str]) -> list[str]:
            out: list[str] = []
            for b in bindings:
                n = leaf.get(b)
                if n and (n in known or not known) and n not in out:
                    out.append(n)
            return out

        def existing(names: list[str]) -> list[str]:
            return [n for n in names if n in known]

        # lights
        lights: dict[str, Any] = {}
        for ch, spec in (rules.get("lights") or {}).items():
            on = nodes_of(spec.get("on", [])) + existing(spec.get("on_nodes", []))
            off = nodes_of(spec.get("off", [])) + existing(spec.get("off_nodes", []))
            if on or off:
                lights[ch] = {"on": list(dict.fromkeys(on))}
                if off:
                    lights[ch]["off"] = list(dict.fromkeys(off))
        lights_eu: dict[str, Any] | None = None
        if f.has_global_lights:
            lights_eu = json.loads(json.dumps(lights))
            for ch, spec in (rules.get("lights_eu") or {}).items():
                on = nodes_of(spec.get("on", [])) + existing(spec.get("on_nodes", []))
                if on:
                    lights_eu[ch] = {"on": list(dict.fromkeys(on))}
                    if ch in lights and lights[ch].get("off"):
                        lights_eu[ch]["off"] = lights[ch]["off"]

        # variants
        variants: dict[str, Any] = {}
        for name, spec in (rules.get("variants") or {}).items():
            req = spec.get("requires", [])
            if all(r in leaf for r in req):
                show = nodes_of(spec.get("show", [])) + existing(spec.get("show_nodes", []))
                hide = nodes_of(spec.get("hide", [])) + existing(spec.get("hide_nodes", []))
                variants[name] = {"show": show, "hide": hide}
            else:
                variants[name] = None

        # defaults: show/hide after load
        d = rules.get("defaults") or {}
        hide = nodes_of(d.get("hide_bindings", [])) + existing(d.get("hide_nodes", []))
        show = nodes_of(d.get("show_bindings", [])) + existing(d.get("show_nodes", []))
        for ch in lights.values():
            hide += ch.get("on", [])
            show += ch.get("off", [])
        if lights_eu:
            for ch in lights_eu.values():
                hide += ch.get("on", [])
        hide = [
            n
            for n in dict.fromkeys(hide)
            if n not in show or n in {x for c in lights.values() for x in c.get("on", [])}
        ]
        show = list(dict.fromkeys(n for n in show if n not in hide))

        # closures + animations
        animations: dict[str, str] = {}
        players = {cs.player: cs for cs in f.players}
        for cs in f.players:
            if cs.ext_rel:
                agf = self.root.try_load(cs.ext_rel)
                if agf is None:
                    self.out.warnings.append(f"{v.id}: animation {cs.ext_rel} missing")
                    continue
                r = convert_animation_file(agf, cs.player)
            elif cs.sub_id is not None and cs.sub_id in res.gf.subs:
                r = convert_clip(res.gf.subs[cs.sub_id].props, cs.player)
            else:
                continue
            self.out.warnings += [f"{v.id}: {w}" for w in r.warnings]
            if not r.data["tracks"]:
                continue
            apath = anim_path(v.scene, cs.player)
            self.add_json(apath, r.data)
            animations[cs.player] = apath
        closures: dict[str, list[str]] = {}
        for ch, patterns in (rules.get("closures") or {}).items():
            hit = None
            for pat in patterns:
                rx = re.compile(pat)
                hit = next((n for n in players if rx.search(n) and n in animations), None)
                if hit is None:
                    hit = next((n for n, p in players.items() if rx.search(p.clip) and n in animations), None)
                if hit:
                    break
            if hit:
                closures[ch] = [hit]

        # pivots
        pv = rules.get("pivots") or {}
        pivots: dict[str, Any] = {
            "wheels": nodes_of(pv.get("wheels", [])),
            "brakes": nodes_of(pv.get("brakes", [])),
            "charge_port": leaf.get(pv.get("charge_port", "")),
            "front": leaf.get(pv.get("front", "")),
            "rear": leaf.get(pv.get("rear", "")),
        }

        # markers
        aliases = (rules.get("markers") or {}).get("aliases") or {}
        markers: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for m in f.markers:
            name = aliases.get(m.name, m.name)
            if m.kind == "marker":
                name = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name).lower()
            key = (name, m.node)
            if key in seen:
                continue
            seen.add(key)
            markers.append(
                {
                    "name": name,
                    "node": m.node,
                    "parent": m.parent,
                    "position": [round(x, 5) for x in m.position],
                    "kind": m.kind,
                }
            )

        # brakes
        br_rules = rules.get("brakes") or {}
        brakes: dict[str, dict[str, str]] = {}
        for prop, sc in f.packed_scene_props.items():
            for prefix, set_name in (br_rules.get("sets") or {}).items():
                if prop.startswith(prefix + "_"):
                    pos = "front" if "front" in prop else "rear"
                    self.emit_scene(sc)
                    brakes.setdefault(set_name, {})[pos] = overrides_path(sc)

        family = resolve_wheel_family(self.cat, v)
        default_wheel = resolve_default_wheel(self.cat, v, family)
        model: dict[str, Any] = {
            "name": v.name,
            "codename": v.codename,
            "aliases": list(rules.get("aliases") or []),
            "api_match": v.api_match,
            "kind": "glb" if f.root_instance and f.root_instance.lower().endswith(".glb") else "obj",
            "dir": v.folder,
            "glb": f.root_instance if f.root_instance and f.root_instance.lower().endswith(".glb") else None,
            "overrides": overrides_path(v.scene),
            "root": f.root_name,
            "animations": animations,
            "closures": closures,
            "bindings": leaf,
            "show": show,
            "hide": hide,
            "variants": variants,
            "lights": lights,
            "lights_eu": lights_eu,
            "pivots": pivots,
            "markers": markers,
            "brakes": brakes,
            "brakes_default": br_rules.get("default", "standard") if brakes else None,
            "wheel_family": family,
            "default_wheel": default_wheel,
            "inline_meshes": f.inline_meshes,
            "excluded": self.exclude,
            "warnings": list(dict.fromkeys(f.warnings)),
        }
        if not closures:
            model["warnings"].append("no closure animations matched the rules")
        if not pivots["wheels"]:
            model["warnings"].append("no wheel pivots found (lf_wheel_path …)")
        return model, family

    # ---------- wheels ----------
    def build_wheel(self, scene_rel: str, enum_name: str, family: str) -> dict[str, Any]:
        if scene_rel.lower().endswith(".glb"):  # some wheel entries point straight at the model (Semi)
            self.add_file(scene_rel)
            return {
                "internal": enum_name,
                "family": family,
                "dir": scene_rel.rsplit("/", 1)[0],
                "scene": scene_rel,
                "kind": "glb",
                "glb": scene_rel,
                "root": scene_rel.rsplit("/", 1)[1][:-4],
                "overrides": None,
                "root_matrix": None,
            }
        res = self.convert(scene_rel)
        f = res.facts
        entry: dict[str, Any] = {
            "internal": enum_name,
            "family": family,
            "dir": scene_rel.rsplit("/", 1)[0],
            "scene": scene_rel,
        }
        if f.root_instance and f.root_instance.lower().endswith(".glb"):
            self.emit_scene(scene_rel)
            entry.update(
                {
                    "kind": "glb",
                    "glb": f.root_instance,
                    "root": f.root_name,
                    "overrides": overrides_path(scene_rel),
                    "root_matrix": f.root_transform,
                }
            )
        elif any("mesh" in n and isinstance(n["mesh"], str) for n in res.overrides["nodes"].values()):
            self.emit_scene(scene_rel)
            entry.update(
                {
                    "kind": "obj",
                    "root": f.root_name,
                    "overrides": overrides_path(scene_rel),
                    "root_matrix": f.root_transform,
                }
            )
        elif f.inline_meshes:
            # inline ArrayMesh (e.g. Wheel_E41): GDRE usually also recovered the imported GLB next to the scene – use it
            # and keep the per-surface materials the inline mesh declares.
            folder = scene_rel.rsplit("/", 1)[0]
            stem = scene_rel.rsplit("/", 1)[1][:-5]
            cand = next(
                (c for c in (f"{folder}/{stem}.glb", f"{folder}/{f.root_name}.glb") if self.root.exists(c)), None
            )
            if cand:
                self.emit_scene(scene_rel)
                self.add_file(cand)
                entry.update(
                    {
                        "kind": "glb",
                        "glb": cand,
                        "root": f.root_name,
                        "overrides": overrides_path(scene_rel),
                        "root_matrix": f.root_transform,
                        "glb_fallback": True,
                    }
                )
            else:
                entry.update({"kind": "unsupported", "reason": "inline ArrayMesh without a recovered GLB"})
        else:
            entry.update({"kind": "unsupported", "reason": "no GLB or OBJ mesh in scene"})
        return entry

    # ---------- cables ----------
    def build_cable(self, scene_rel: str) -> dict[str, Any]:
        res = self.emit_scene(scene_rel)
        flow_node = None
        for name, n in res.overrides["nodes"].items():
            for key in (n.get("materials") or {}).values():
                if res.overrides["materials"].get(key, {}).get("kind") == "power_flow":
                    flow_node = name
        return {"overrides": overrides_path(scene_rel), "flow_node": flow_node, "root": res.facts.root_name}


def summarize_model(m: dict[str, Any]) -> str:
    return (
        f"{m['name']} [{m['kind']}] closures={len(m['closures'])} lights={len(m['lights'])} "
        f"variants={[k for k, v in m['variants'].items() if v]} markers={len(m['markers'])} wheels={m['wheel_family']}"
    )


__all__ = [
    "PackBuilder",
    "BuildResult",
    "overrides_path",
    "anim_path",
    "summarize_model",
    "MODEL_ID_RE",
    "SceneFacts",
    "Rules",
]
