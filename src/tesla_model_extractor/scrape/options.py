"""`mobile/scripts/VehicleOptions.gd` → paints, wheel types/paths/aliases, default wheels, charge-cable map."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..godot.text import strip_res
from ..godot.values import Call, Ident
from .gdscript import const_block, const_scalar, enum_members, node_path_arg, parse_dict, strip_comments


@dataclass
class VehicleOptions:
    paints: dict[str, dict[str, Any]] = field(default_factory=dict)  # name → {albedo "#rrggbb", metallic, roughness}
    base_roughness: float = 0.04
    base_metallic: float = 0.3
    fallback_paint: dict[str, Any] | None = None
    wheel_types: list[str] = field(default_factory=list)  # enum order
    wheel_paths: dict[str, str] = field(default_factory=dict)  # enum name → rel .tscn
    wheel_aliases: dict[str, str] = field(default_factory=dict)  # API string (MobileWheelTypeEnumMap) → enum name
    default_wheels: dict[str, str] = field(default_factory=dict)  # model_key → enum name
    cable_map: dict[str, str] = field(default_factory=dict)  # port type → rel .tscn
    warnings: list[str] = field(default_factory=list)


def _hex(v: Any) -> str | None:
    if isinstance(v, Call) and v.name == "Color" and v.args:
        a = v.args[0]
        if isinstance(a, str):
            h = a.lstrip("#").lower()
            return "#" + h[:6]
        f = v.floats()
        if len(f) >= 3:
            return "#" + "".join(f"{int(round(c * 255)):02x}" for c in f[:3])
    return None


def _enum_ref(v: Any) -> str | None:
    if isinstance(v, Ident):
        return v.name.split(".")[-1]
    return None


def scrape_vehicle_options(text: str) -> VehicleOptions:
    text = strip_comments(text)
    o = VehicleOptions()
    br = const_scalar(text, "BASE_ROUGHNESS")
    bm = const_scalar(text, "BASE_METALLIC")
    if isinstance(br, (int, float)):
        o.base_roughness = float(br)
    if isinstance(bm, (int, float)):
        o.base_metallic = float(bm)
    consts = {"BASE_ROUGHNESS": o.base_roughness, "BASE_METALLIC": o.base_metallic}

    def paint(d: Any) -> dict[str, Any] | None:
        if not isinstance(d, dict):
            return None
        col = _hex(d.get("color"))
        if col is None:
            return None
        return {
            "albedo": col,
            "metallic": float(d.get("metallic", o.base_metallic)),
            "roughness": float(d.get("roughness", o.base_roughness)),
        }

    blk = const_block(text, "ExteriorColorValue")
    if blk:
        for k, v in parse_dict(blk, consts).items():
            p = paint(v)
            if isinstance(k, str) and p:
                o.paints[k] = p
    else:
        o.warnings.append("ExteriorColorValue table not found")
    fb = const_block(text, "FALLBACK_EXTERIOR_COLOR")
    if fb:
        o.fallback_paint = paint(parse_dict(fb, consts))

    o.wheel_types = [n for n, _ in enum_members(text, "WheelType")]
    if not o.wheel_types:
        o.warnings.append("WheelType enum not found")
    blk = const_block(text, "WheelTypeToPathMap")
    if blk:
        for k, v in parse_dict(blk).items():
            name, path = _enum_ref(k), node_path_arg(v)
            if name and path:
                o.wheel_paths[name] = strip_res(path)
    else:
        o.warnings.append("WheelTypeToPathMap not found")
    blk = const_block(text, "MobileWheelTypeEnumMap")
    if blk:
        for k, v in parse_dict(blk).items():
            name = _enum_ref(v)
            if isinstance(k, str) and name:
                o.wheel_aliases[k] = name
    blk = const_block(text, "DefaultWheelForVehicleType")
    if blk:
        for k, v in parse_dict(blk).items():
            name = _enum_ref(v)
            if isinstance(k, str) and name:
                o.default_wheels[k] = name
    blk = const_block(text, "ChargePortTypeToCableMap")
    if blk:
        for k, v in parse_dict(blk).items():
            path = node_path_arg(v)
            if isinstance(k, str) and path:
                o.cable_map[k] = strip_res(path)
    else:
        o.warnings.append("ChargePortTypeToCableMap not found")
    return o


def api_wheel_name(enum_name: str) -> str:
    """Enum member → the name the card / Tesla API use: underscores dropped (`Crossflow_19` → `Crossflow19`)."""
    return re.sub(r"_", "", enum_name)
