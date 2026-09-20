"""Write the Unreal export of a pack to disk: one folder per vehicle with the GLB, its sidecar and shared tables."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..manifest import BuildResult
from .scene import Assembler, ExportOptions


@dataclass
class VehicleOutput:
    model: str
    folder: Path
    glb: Path
    glb_bytes: int
    animations: int
    wheels: list[str] = field(default_factory=list)
    cables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def export_pack(
    result: BuildResult,
    model_ids: list[str],
    opt: ExportOptions,
    out_dir: Path,
    separate_wheels: bool = False,
    cables: bool = False,
) -> list[VehicleOutput]:
    out: list[VehicleOutput] = []
    asm = Assembler(result, opt)
    for model_id in model_ids:
        model = result.manifest["models"][model_id]
        res = asm.export_vehicle(model_id)
        folder = out_dir / model_id
        folder.mkdir(parents=True, exist_ok=True)
        glb_path = folder / res.sidecar["file"]
        glb_path.write_bytes(res.glb)
        (folder / "paints.json").write_bytes(_json_bytes(result.manifest.get("paints")))
        vo = VehicleOutput(
            model_id, folder, glb_path, len(res.glb), len(res.sidecar["animations"]), warnings=res.warnings
        )
        if separate_wheels:
            family = model.get("wheel_family")
            for api, w in (result.manifest.get("wheels") or {}).items():
                if family and w.get("family") != family:
                    continue
                data = asm.export_wheel(api)
                if data:
                    (folder / "wheels").mkdir(exist_ok=True)
                    (folder / "wheels" / f"{api}.glb").write_bytes(data)
                    vo.wheels.append(api)
        if cables:
            for port in result.manifest.get("cables") or {}:
                data = asm.export_cable(port)
                if data:
                    (folder / "cables").mkdir(exist_ok=True)
                    (folder / "cables" / f"{port}.glb").write_bytes(data)
                    vo.cables.append(port)
        pano = (result.manifest.get("environment") or {}).get("panorama")
        pano_bytes = asm.files.read(pano) if pano else None
        if pano and pano_bytes:
            (folder / "studio_panorama.png").write_bytes(pano_bytes)
            res.sidecar["environment_panorama"] = "studio_panorama.png"
        res.sidecar["separate_wheels"] = vo.wheels
        res.sidecar["cables"] = vo.cables
        (folder / "unreal.json").write_bytes(_json_bytes(res.sidecar))
        asm.warnings = []
        out.append(vo)
    return out
