"""What is in a recovered project: vehicle scenes (via ProductManager routes), wheel families, cables, paints, environment."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .godot.resolve import ResourceRoot
from .rules import Rules, all_codename_rules, load_rules, rules_for_scene
from .scrape.environment import Environment, scrape_environment
from .scrape.options import VehicleOptions, api_wheel_name, scrape_vehicle_options
from .scrape.product import ProductTable, SceneRoute, scrape_product_table

_FAMILY_DIR = re.compile(r"(^|/)Wheels[^/]*$")


@dataclass
class VehicleEntry:
    id: str
    codename: str
    name: str
    scene: str  # rel .tscn
    present: bool
    rules: Rules
    routes: list[SceneRoute] = field(default_factory=list)
    size_bytes: int = 0

    @property
    def folder(self) -> str:
        return self.scene.rsplit("/", 1)[0]

    @property
    def model_keys(self) -> list[str]:
        return sorted({r.model_key for r in self.routes if r.model_key})

    @property
    def fascia_types(self) -> list[str]:
        out: list[str] = []
        for r in self.routes:
            for f in r.fascia_type or []:
                if f not in out:
                    out.append(f)
        return out

    @property
    def api_match(self) -> dict:
        d: dict = {"model_key": self.model_keys[0] if self.model_keys else None}
        if self.fascia_types:
            d["fascia_type"] = self.fascia_types
        chassis = [c for r in self.routes for c in (r.chassis_type or [])]
        if chassis:
            d["chassis_type"] = chassis
        return d


@dataclass
class WheelEntry:
    api_name: str
    enum_name: str
    scene: str
    family: str
    present: bool


@dataclass
class Catalog:
    root: ResourceRoot
    product: ProductTable
    options: VehicleOptions
    environment: Environment
    vehicles: list[VehicleEntry]
    wheels: list[WheelEntry]
    warnings: list[str] = field(default_factory=list)

    def vehicle(self, key: str) -> VehicleEntry | None:
        k = key.lower()
        for v in self.vehicles:
            if k in (v.id, v.codename.lower()) or k in [a.lower() for a in v.rules.get("aliases", [])]:
                return v
        return None

    def wheels_in_family(self, family: str) -> list[WheelEntry]:
        return [w for w in self.wheels if w.family == family and w.present]

    def wheel(self, name: str) -> WheelEntry | None:
        n = name.lower().replace("_", "")
        for w in self.wheels:
            if w.api_name.lower() == n or w.enum_name.lower() == name.lower():
                return w
        alias = self.options.wheel_aliases.get(name)
        if alias:
            return self.wheel(alias)
        return None


def wheel_family_of(scene_rel: str) -> str:
    """`Ego/Wheels_Bayberry/GeminiDark/GeminiDark.tscn` → `Ego/Wheels_Bayberry`; `Ego/Wheels/Wheel_Apollo.tscn` → `Ego/Wheels`."""
    parts = scene_rel.split("/")[:-1]
    for i in range(len(parts), 0, -1):
        cand = "/".join(parts[:i])
        if _FAMILY_DIR.search(cand):
            return cand
    return "/".join(parts)


def _read(root: ResourceRoot, rel: str) -> str | None:
    p = root.resolve(rel)
    return p.read_text(encoding="utf-8", errors="replace") if p else None


def build_catalog(root: ResourceRoot, extra_rules: Path | None = None) -> Catalog:
    warnings: list[str] = []
    prod_text = _read(root, "mobile/scripts/data/ProductManager.gd") or _read(root, "mobile/scripts/ProductManager.gd")
    product = scrape_product_table(prod_text) if prod_text else ProductTable(warnings=["ProductManager.gd not found"])
    opt_text = _read(root, "mobile/scripts/VehicleOptions.gd")
    options = scrape_vehicle_options(opt_text) if opt_text else VehicleOptions(warnings=["VehicleOptions.gd not found"])
    environment = scrape_environment(root)
    warnings += product.warnings + options.warnings + environment.warnings

    # vehicles: every scene the product table routes to, plus rules files whose scene exists but is not routed
    scenes = product.scenes()
    for _code, data in all_codename_rules(extra_rules).items():
        sc = data.get("scene")
        if isinstance(sc, str) and sc not in scenes and root.exists(sc):
            scenes.append(sc)
    vehicles: list[VehicleEntry] = []
    for sc in scenes:
        rules = rules_for_scene(sc, extra_rules)
        present = root.exists(sc)
        size = 0
        if present:
            folder = root.require(sc).parent
            size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and "/Skins/" not in p.as_posix())
        vehicles.append(
            VehicleEntry(
                id=str(rules["id"]),
                codename=str(rules["codename"]),
                name=str(rules["name"]),
                scene=sc,
                present=present,
                rules=rules,
                routes=product.for_scene(sc),
                size_bytes=size,
            )
        )
    ids = [v.id for v in vehicles]
    if len(ids) != len(set(ids)):
        warnings.append(f"duplicate vehicle ids: {sorted({i for i in ids if ids.count(i) > 1})}")

    wheels: list[WheelEntry] = []
    for enum_name, sc in options.wheel_paths.items():
        wheels.append(
            WheelEntry(
                api_name=api_wheel_name(enum_name),
                enum_name=enum_name,
                scene=sc,
                family=wheel_family_of(sc),
                present=root.exists(sc),
            )
        )
    return Catalog(root, product, options, environment, vehicles, wheels, warnings)


def resolve_wheel_family(cat: Catalog, v: VehicleEntry) -> str | None:
    fam = v.rules.get("wheel_family")
    if isinstance(fam, str):
        return fam
    families = sorted({w.family for w in cat.wheels})
    for f in families:  # folder named after the codename family (Wheels_Bayberry ← Bayberry, BayberryE41, BayberryE80)
        tag = f.rsplit("/", 1)[-1].replace("Wheels_", "").replace("Wheels", "")
        if tag and v.codename.lower().startswith(tag.lower()):
            return f
    for mk in v.model_keys:  # family of the default wheel for this model_key
        enum_name = cat.options.default_wheels.get(mk)
        if enum_name and enum_name in cat.options.wheel_paths:
            return wheel_family_of(cat.options.wheel_paths[enum_name])
    return None


def resolve_default_wheel(cat: Catalog, v: VehicleEntry, family: str | None) -> str | None:
    candidates = cat.wheels_in_family(family) if family else []
    want = v.rules.get("default_wheel")
    if isinstance(want, str):
        w = cat.wheel(want)
        if w and (not family or w.family == family):
            return w.api_name
    for mk in v.model_keys:
        enum_name = cat.options.default_wheels.get(mk)
        if enum_name:
            w = cat.wheel(enum_name)
            if w and w.present and (not family or w.family == family):
                return w.api_name
    return candidates[0].api_name if candidates else None


__all__ = [
    "Catalog",
    "VehicleEntry",
    "WheelEntry",
    "build_catalog",
    "resolve_wheel_family",
    "resolve_default_wheel",
    "load_rules",
]
