"""Read the JSON chunk of a GLB file (node names, material names, mesh primitive counts) for validation."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GlbInfo:
    nodes: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    meshes: list[tuple[str, int]] = field(default_factory=list)  # (mesh name, primitive count)
    node_mesh: dict[str, str] = field(default_factory=dict)  # node name → mesh name

    @property
    def node_set(self) -> set[str]:
        return set(self.nodes)


def read_glb(path: Path) -> GlbInfo:
    with open(path, "rb") as fh:
        head = fh.read(20)
        magic, _version, _length, chunk_len, chunk_type = struct.unpack("<4sIIII", head)
        if magic != b"glTF":
            raise ValueError(f"{path} is not a GLB file")
        if chunk_type != 0x4E4F534A:  # 'JSON'
            raise ValueError(f"{path}: first chunk is not JSON")
        doc = json.loads(fh.read(chunk_len).decode("utf-8"))
    return _info(doc)


def read_glb_bytes(data: bytes) -> GlbInfo:
    magic, _version, _length, chunk_len, chunk_type = struct.unpack("<4sIIII", data[:20])
    if magic != b"glTF" or chunk_type != 0x4E4F534A:
        raise ValueError("not a GLB file")
    return _info(json.loads(data[20 : 20 + chunk_len].decode("utf-8")))


def _info(doc: dict) -> GlbInfo:
    info = GlbInfo()
    meshes = doc.get("meshes", [])
    for m in meshes:
        info.meshes.append((m.get("name", ""), len(m.get("primitives", []))))
    for n in doc.get("nodes", []):
        name = n.get("name", "")
        info.nodes.append(name)
        if "mesh" in n and 0 <= n["mesh"] < len(meshes):
            info.node_mesh[name] = meshes[n["mesh"]].get("name", "")
    info.materials = [m.get("name", "") for m in doc.get("materials", [])]
    return info
