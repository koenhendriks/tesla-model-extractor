"""Lighting / camera constants of the app: how the Tesla app renders the car in the mobile view.

Sources (all optional – sensible fallbacks are the values observed in app 4.60.0):
  mobile/scripts/MobileEnvTest.gd     CameraPosition presets: camera pivot rotation + offset, sky rotation, energies
  mobile/scripts/EnvironmentManager.gd background colours (light/dark), fallback energies
  env/mobile.tres                     the PanoramaSky texture and ambient energy
  mobile.tscn                         camera fov
  mobile/scripts/VehicleManager.gd    flash-lights timing
  project.godot                       renderer (GLES2 → gamma-space shading)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..godot.resolve import ResourceRoot
from ..godot.values import GodotValueError, parse_value, vector3
from .gdscript import strip_comments

DEFAULT_PRESETS: dict[str, dict[str, Any]] = {
    "parked": {
        "sky_rot_deg": [0, -7, 83],
        "env_energy": 4,
        "amb_energy": 4,
        "camera": {"pivot_deg": [68.6, -138, 0], "offset": [-0.06, 6.7, 0]},
    },
    "charging": {
        "sky_rot_deg": [0, -7, 83],
        "env_energy": 4,
        "amb_energy": 4,
        "camera": {"pivot_deg": [74, -38, 0], "offset": [0, 6.55, 0]},
    },
    "drive": {
        "sky_rot_deg": [0, -7, 83],
        "env_energy": 4,
        "amb_energy": 4,
        "camera": {"pivot_deg": [68.6, -138, 0], "offset": [-0.06, 8, 0]},
    },
    "top_down": {
        "sky_rot_deg": [-10, -10, 0],
        "env_energy": 6,
        "amb_energy": 2.5,
        "camera": {"pivot_deg": [0, 0, 0], "offset": [0, 10, 0]},
    },
    "climate": {
        "sky_rot_deg": [0, -7, 83],
        "env_energy": 4,
        "amb_energy": 4,
        "camera": {"pivot_deg": [0, 0, 0], "offset": [0, 6, 0.6]},
    },
}


@dataclass
class Environment:
    panorama: str | None = "shared_misc_textures/New_Studio.png"
    renderer: str = "gles2_gamma"
    presets: dict[str, dict[str, Any]] = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_PRESETS.items()})
    fallback: str = "parked"
    fallback_energy: dict[str, float] = field(default_factory=lambda: {"env_energy": 3.0, "amb_energy": 7.0})
    fov: float = 40.0
    bg: dict[str, str] = field(default_factory=lambda: {"dark": "#161718", "light": "#F7F7F7"})
    flash: dict[str, float] = field(default_factory=lambda: {"duration_s": 1.5, "interval_s": 1.0})
    ambient_energy_default: float | None = None
    warnings: list[str] = field(default_factory=list)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "panorama": self.panorama,
            "renderer": self.renderer,
            "presets": self.presets,
            "fallback": self.fallback,
            "fallback_energy": self.fallback_energy,
            "fov": self.fov,
            "bg": self.bg,
            "flash": self.flash,
        }


_PRESET_LINE = re.compile(r"CameraPosition\.(\w+)\s*:\s*(\{.*\})\s*,?\s*$")


def scrape_environment(root: ResourceRoot) -> Environment:
    env = Environment()
    _presets(root, env)
    _manager(root, env)
    _env_resource(root, env)
    _fov(root, env)
    _flash(root, env)
    _renderer(root, env)
    return env


def _read(root: ResourceRoot, rel: str) -> str | None:
    p = root.resolve(rel)
    return p.read_text(encoding="utf-8", errors="replace") if p else None


def _presets(root: ResourceRoot, env: Environment) -> None:
    text = _read(root, "mobile/scripts/MobileEnvTest.gd")
    if text is None:
        env.warnings.append("MobileEnvTest.gd not found; using built-in presets")
        return
    cams: dict[str, dict[str, Any]] = {}
    envs: dict[str, dict[str, Any]] = {}
    for line in strip_comments(text).splitlines():
        m = _PRESET_LINE.search(line)
        if not m:
            continue
        try:
            d = parse_value(m.group(2))
        except GodotValueError:
            continue
        if not isinstance(d, dict):
            continue
        key = m.group(1).lower()
        if "offset" in d:
            cams[key] = {
                "pivot_deg": vector3(d.get("rot")) or [0, 0, 0],
                "offset": vector3(d.get("offset")) or [0, 6, 0],
            }
        elif "env_energy" in d:
            envs[key] = {
                "sky_rot_deg": vector3(d.get("rot")) or [0, 0, 0],
                "env_energy": float(d.get("env_energy", 4)),
                "amb_energy": float(d.get("amb_energy", 4)),
            }
    if not cams and not envs:
        env.warnings.append("no CameraPosition presets parsed; using built-in presets")
        return
    presets: dict[str, dict[str, Any]] = {}
    for key in list(dict.fromkeys([*cams, *envs])):
        e = envs.get(key) or DEFAULT_PRESETS.get(key, DEFAULT_PRESETS["parked"])
        presets[key] = {
            "sky_rot_deg": e["sky_rot_deg"],
            "env_energy": e["env_energy"],
            "amb_energy": e["amb_energy"],
            "camera": cams.get(key) or DEFAULT_PRESETS.get(key, DEFAULT_PRESETS["parked"])["camera"],
        }
    env.presets = presets
    if "parked" not in presets:
        env.fallback = next(iter(presets))


def _manager(root: ResourceRoot, env: Environment) -> None:
    text = _read(root, "mobile/scripts/EnvironmentManager.gd")
    if text is None:
        return
    m = re.search(r'light_background_color[^\n]*Color\("(#?[0-9A-Fa-f]{6})"\)', text)
    if m:
        env.bg["light"] = "#" + m.group(1).lstrip("#").upper()
    m = re.search(r'dark_background_color[^\n]*Color\("(#?[0-9A-Fa-f]{6})"\)', text)
    if m:
        env.bg["dark"] = "#" + m.group(1).lstrip("#").upper()
    for key in ("env_energy", "amb_energy"):
        m = re.search(rf'data\.get\("{key}",\s*([\d.]+)\)', text)
        if m:
            env.fallback_energy[key] = float(m.group(1))


def _env_resource(root: ResourceRoot, env: Environment) -> None:
    gf = root.try_load("env/mobile.tres")
    if gf is None:
        env.warnings.append("env/mobile.tres not found; assuming shared_misc_textures/New_Studio.png")
        return
    for blk in gf.blocks:
        if blk.kind == "sub_resource" and blk.type == "PanoramaSky":
            p = gf.ext_rel(blk.props.get("panorama"))
            if p:
                env.panorama = p
    res = gf.resource
    if res and isinstance(res.props.get("ambient_light_energy"), (int, float)):
        env.ambient_energy_default = float(res.props["ambient_light_energy"])
    if env.panorama and not root.exists(env.panorama):
        env.warnings.append(f"panorama {env.panorama} not found in recovered project")


def _fov(root: ResourceRoot, env: Environment) -> None:
    gf = root.try_load("mobile.tscn")
    if gf is None:
        return
    for n in gf.nodes:
        if n.type == "Camera" and isinstance(n.props.get("fov"), (int, float)):
            env.fov = float(n.props["fov"])
            return


def _flash(root: ResourceRoot, env: Environment) -> None:
    text = _read(root, "mobile/scripts/VehicleManager.gd")
    if text is None:
        return
    for key, out in (("flash_duration", "duration_s"), ("flash_interval", "interval_s")):
        m = re.search(rf'data\.get\("{key}",\s*([\d.]+)\)', text)
        if m:
            env.flash[out] = float(m.group(1))


def _renderer(root: ResourceRoot, env: Environment) -> None:
    text = _read(root, "project.godot")
    if text is None:
        return
    m = re.search(r'rendering/quality/driver/driver_name\s*=\s*"(\w+)"', text)
    if m:
        env.renderer = "gles2_gamma" if m.group(1).upper() == "GLES2" else "gles3_linear"
