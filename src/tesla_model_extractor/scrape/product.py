"""`mobile/scripts/data/ProductManager.gd::get_vehicle_node_path` → which scene renders which API vehicle.

match model_key:
    "modely":
        match chassis_type:
            "model_y_long_wheel_base": return NodePath("res://Ego/BayberryE80/BayberryE80.tscn")
            _:
                match fascia_type:
                    "e41Bayberry": return NodePath("res://Ego/BayberryE41/BayberryE41.tscn")
                    "baseBayberry", "performanceBayberry": return NodePath("res://Ego/Bayberry/Bayberry.tscn")
                    _: return NodePath("res://Ego/Y_High/ModelY_High.tscn")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..godot.text import strip_res
from .gdscript import strip_comments

_RETURN = re.compile(r'return\s+NodePath\("([^"]+)"\)')
_CASE = re.compile(r'^(\s*)((?:"[^"]*"\s*,\s*)*"[^"]*"|_)\s*:\s*(.*)$')
_MATCH = re.compile(r"^(\s*)match\s+(\w+)\s*:\s*$")


@dataclass
class SceneRoute:
    scene: str  # rel .tscn
    model_key: str | None
    fascia_type: list[str] | None = None  # None = any
    chassis_type: list[str] | None = None
    is_default: bool = False  # the `_:` branch of a nested match


@dataclass
class ProductTable:
    routes: list[SceneRoute] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def scenes(self) -> list[str]:
        out: list[str] = []
        for r in self.routes:
            if r.scene not in out:
                out.append(r.scene)
        return out

    def for_scene(self, scene_rel: str) -> list[SceneRoute]:
        return [r for r in self.routes if r.scene == scene_rel]


def _values(case: str) -> list[str] | None:
    if case.strip() == "_":
        return None
    return re.findall(r'"([^"]*)"', case)


def scrape_product_table(text: str) -> ProductTable:
    text = strip_comments(text)
    table = ProductTable()
    m = re.search(r"^\s*func\s+get_vehicle_node_path\s*\(", text, re.M)
    if not m:
        table.warnings.append("get_vehicle_node_path not found; falling back to every res://Ego/**/*.tscn mentioned")
        for p in sorted(set(re.findall(r'"(res://Ego/[^"]+\.tscn)"', text))):
            table.routes.append(SceneRoute(strip_res(p), None))
        return table
    body = text[m.end() :]
    # cut at the next top-level func
    nxt = re.search(r"^\s*(?:static\s+)?func\s+", body, re.M)
    if nxt:
        body = body[: nxt.start()]

    # indentation-driven walk: stack of (indent, kind, value)
    stack: list[tuple[int, str, object]] = []  # kind: "match" (variable) | "case" (values or None)
    for line in body.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        mm = _MATCH.match(line)
        if mm:
            stack.append((indent, "match", mm.group(2)))
            continue
        mc = _CASE.match(line)
        if mc:
            stack.append((indent, "case", _values(mc.group(2))))
            rest = mc.group(3)
            if rest:
                _emit(table, stack, rest)
            continue
        _emit(table, stack, line)
    if not table.routes:
        table.warnings.append("no routes parsed from get_vehicle_node_path")
    return table


def _emit(table: ProductTable, stack: list[tuple[int, str, object]], line: str) -> None:
    r = _RETURN.search(line)
    if not r:
        return
    route = SceneRoute(strip_res(r.group(1)), None)
    var: str | None = None
    for _indent, kind, value in stack:
        if kind == "match":
            var = str(value)
        elif kind == "case":
            vals = value  # list[str] | None
            if var == "model_key":
                route.model_key = vals[0] if isinstance(vals, list) and vals else None
                if isinstance(vals, list) and len(vals) > 1:
                    route.model_key = vals[0]
                    route.__dict__.setdefault("extra_model_keys", vals[1:])
            elif var == "fascia_type":
                route.fascia_type = vals if isinstance(vals, list) else None
                route.is_default = route.is_default or vals is None
            elif var == "chassis_type":
                route.chassis_type = vals if isinstance(vals, list) else None
                route.is_default = route.is_default or vals is None
    table.routes.append(route)
