"""Wavefront OBJ → glTF mesh primitives, split the way Godot's importer splits surfaces (one per `usemtl`).

Godot's OBJ importer commits a surface whenever a new `usemtl`, `o` or `g` line follows faces, so `material/N` in
the `.tscn` refers to the N-th such group. Vertices are de-duplicated per (position, uv, normal) triple; faces with
more than three corners are fan-triangulated; missing normals are generated flat per face.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .gltf import ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER, FLOAT, UNSIGNED_INT, UNSIGNED_SHORT, Document


@dataclass
class Surface:
    material: str | None
    positions: list[float] = field(default_factory=list)
    normals: list[float] = field(default_factory=list)
    uvs: list[float] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    _index: dict[tuple[int, int, int], int] = field(default_factory=dict)
    has_uv: bool = False
    has_normal: bool = False


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def parse_obj(text: str) -> list[Surface]:
    v: list[list[float]] = []
    vt: list[list[float]] = []
    vn: list[list[float]] = []
    surfaces: list[Surface] = []
    cur: Surface | None = None
    pending_material: str | None = None

    def surface() -> Surface:
        nonlocal cur
        if cur is None:
            cur = Surface(pending_material)
            surfaces.append(cur)
        return cur

    def commit() -> None:
        nonlocal cur
        if cur is not None and not cur.indices:
            surfaces.remove(cur)
        cur = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "v" and len(parts) >= 4:
            v.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif tag == "vt" and len(parts) >= 3:
            vt.append([float(parts[1]), float(parts[2])])
        elif tag == "vn" and len(parts) >= 4:
            vn.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif tag == "usemtl":
            commit()
            pending_material = " ".join(parts[1:]) or None
        elif tag in ("o", "g"):
            commit()
        elif tag == "f" and len(parts) >= 4:
            s = surface()
            corners = [_corner(p, len(v), len(vt), len(vn)) for p in parts[1:]]
            flat = None
            if any(c[2] is None for c in corners):
                p0, p1, p2 = (v[corners[i][0]] for i in range(3))
                n = _cross([p1[k] - p0[k] for k in range(3)], [p2[k] - p0[k] for k in range(3)])
                ln = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
                flat = [x / ln for x in n]
            idx = []
            for pi, ti, ni in corners:
                key = (pi, -1 if ti is None else ti, -1 if ni is None else ni)
                if key not in s._index:
                    s._index[key] = len(s.positions) // 3
                    s.positions.extend(v[pi])
                    if ti is not None:
                        s.uvs.extend([vt[ti][0], 1.0 - vt[ti][1]])  # OBJ v=0 is the bottom, glTF v=0 is the top
                        s.has_uv = True
                    else:
                        s.uvs.extend([0.0, 0.0])
                    if ni is not None:
                        s.normals.extend(vn[ni])
                        s.has_normal = True
                    else:
                        s.normals.extend(flat or [0.0, 1.0, 0.0])
                idx.append(s._index[key])
            for i in range(1, len(idx) - 1):
                s.indices.extend([idx[0], idx[i], idx[i + 1]])
    commit()
    return surfaces


def _corner(token: str, nv: int, nvt: int, nvn: int) -> tuple[int, int | None, int | None]:
    def ref(s: str, n: int) -> int | None:
        if not s:
            return None
        i = int(s)
        return (i - 1) if i > 0 else (n + i)

    bits = token.split("/")
    pi = ref(bits[0], nv)
    ti = ref(bits[1], nvt) if len(bits) > 1 else None
    ni = ref(bits[2], nvn) if len(bits) > 2 else None
    return (pi if pi is not None else 0), ti, ni


def add_obj_mesh(doc: Document, name: str, text: str) -> tuple[int | None, int]:
    """Append the OBJ as one mesh; returns (mesh index or None when empty, number of primitives)."""
    surfaces = parse_obj(text)
    prims: list[dict[str, Any]] = []
    for s in surfaces:
        if not s.indices:
            continue
        attrs = {
            "POSITION": doc.add_accessor(s.positions, "VEC3", FLOAT, ARRAY_BUFFER),
            "NORMAL": doc.add_accessor(_normalized(s.normals), "VEC3", FLOAT, ARRAY_BUFFER, minmax=False),
        }
        if s.has_uv:
            attrs["TEXCOORD_0"] = doc.add_accessor(s.uvs, "VEC2", FLOAT, ARRAY_BUFFER, minmax=False)
        n_vertices = len(s.positions) // 3
        comp = UNSIGNED_SHORT if n_vertices < 65536 else UNSIGNED_INT
        prims.append(
            {
                "attributes": attrs,
                "indices": doc.add_accessor(s.indices, "SCALAR", comp, ELEMENT_ARRAY_BUFFER, minmax=False),
                "mode": 4,
            }
        )
    if not prims:
        return None, 0
    return doc.add_mesh(name, prims), len(prims)


def _normalized(normals: list[float]) -> list[float]:
    out = list(normals)
    for i in range(0, len(out), 3):
        x, y, z = out[i : i + 3]
        ln = (x * x + y * y + z * z) ** 0.5
        if ln < 1e-12:
            out[i : i + 3] = [0.0, 1.0, 0.0]
        elif abs(ln - 1.0) > 1e-6:
            out[i : i + 3] = [x / ln, y / ln, z / ln]
    return out
