"""Minimal glTF 2.0 document model: read/write GLB, append data, merge documents, prune what is unreachable.

Everything lives in one binary buffer (buffer 0). Indices into the JSON arrays are plain ints, so appending is
cheap and merging another document is index offsetting. `compact()` drops nodes that are no longer reachable from
the scene together with every mesh / material / texture / image / accessor / bufferView only they used and rebuilds
the binary buffer, which is what makes variant pruning actually shrink the file.
"""

from __future__ import annotations

import copy
import json
import math
import struct
from dataclasses import dataclass, field
from typing import Any

from ..godot.math import decompose_transform

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
FLOAT = 5126
UNSIGNED_BYTE = 5121
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
COMPONENT_SIZE = {UNSIGNED_BYTE: 1, 5122: 2, UNSIGNED_SHORT: 2, UNSIGNED_INT: 4, FLOAT: 4}
# textureInfo objects may only carry these keys; used to find texture references anywhere inside a material
TEXTURE_INFO_KEYS = {"index", "texCoord", "scale", "strength", "extensions", "extras"}


def _pad(data: bytes | bytearray, fill: bytes) -> bytes:
    rem = len(data) % 4
    return bytes(data) + fill * ((4 - rem) % 4) if rem else bytes(data)


def trs_from_mat4(m: list[float]) -> tuple[list[float], list[float], list[float]]:
    """Column-major 4x4 (as in `*.overrides.json`) → (translation, rotation[x,y,z,w], scale)."""
    godot12 = [m[0], m[4], m[8], m[1], m[5], m[9], m[2], m[6], m[10], m[12], m[13], m[14]]
    return decompose_transform(godot12)


def quat_mul(a: list[float], b: list[float]) -> list[float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]


def quat_rotate(q: list[float], v: list[float]) -> list[float]:
    x, y, z, w = q
    # v' = v + 2w(q×v) + 2(q×(q×v))
    cx, cy, cz = y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0]
    dx, dy, dz = y * cz - z * cy, z * cx - x * cz, x * cy - y * cx
    return [v[0] + 2 * (w * cx + dx), v[1] + 2 * (w * cy + dy), v[2] + 2 * (w * cz + dz)]


def yaw_quat(deg: float) -> list[float]:
    h = math.radians(deg) / 2
    return [0.0, math.sin(h), 0.0, math.cos(h)]


def _clean(values: list[float]) -> list[float]:
    return [float(round(v, 7)) + 0.0 for v in values]


@dataclass
class Document:
    json: dict[str, Any]
    bin: bytearray = field(default_factory=bytearray)

    # ---------- construction ----------
    @classmethod
    def empty(cls, scene_name: str, root_name: str) -> Document:
        doc = cls(
            {
                "asset": {"version": "2.0"},
                "scene": 0,
                "scenes": [{"name": scene_name, "nodes": [0]}],
                "nodes": [{"name": root_name}],
            }
        )
        return doc

    @classmethod
    def from_glb(cls, data: bytes) -> Document:
        if len(data) < 20 or data[:4] != GLB_MAGIC:
            raise ValueError("not a GLB file")
        _magic, _version, length, json_len, json_type = struct.unpack_from("<4sIIII", data, 0)
        if json_type != CHUNK_JSON:
            raise ValueError("GLB: first chunk is not JSON")
        doc = json.loads(data[20 : 20 + json_len].decode("utf-8"))
        off = 20 + json_len
        blob = bytearray()
        if off + 8 <= min(length, len(data)):
            bin_len, bin_type = struct.unpack_from("<II", data, off)
            if bin_type == CHUNK_BIN:
                blob = bytearray(data[off + 8 : off + 8 + bin_len])
        buffers = doc.get("buffers", [])
        if len(buffers) > 1 or any("uri" in b for b in buffers):
            raise ValueError("GLB: only a single embedded buffer is supported")
        for bv in doc.get("bufferViews", []):
            if bv.get("buffer", 0) != 0:
                raise ValueError("GLB: bufferView references a missing buffer")
        return cls(doc, blob)

    def to_glb(self) -> bytes:
        self.json["buffers"] = [{"byteLength": len(self.bin)}] if self.bin else []
        if not self.bin:
            self.json.pop("buffers", None)
        js = _pad(json.dumps(self.json, separators=(",", ":"), allow_nan=False).encode("utf-8"), b" ")
        chunks = struct.pack("<II", len(js), CHUNK_JSON) + js
        if self.bin:
            blob = _pad(self.bin, b"\0")
            chunks += struct.pack("<II", len(blob), CHUNK_BIN) + blob
        return struct.pack("<4sII", GLB_MAGIC, 2, 12 + len(chunks)) + chunks

    # ---------- generic accessors ----------
    def _list(self, key: str) -> list[Any]:
        return self.json.setdefault(key, [])

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self._list("nodes")

    @property
    def meshes(self) -> list[dict[str, Any]]:
        return self._list("meshes")

    @property
    def materials(self) -> list[dict[str, Any]]:
        return self._list("materials")

    @property
    def animations(self) -> list[dict[str, Any]]:
        return self._list("animations")

    @property
    def root_nodes(self) -> list[int]:
        scenes = self.json.get("scenes") or []
        idx = self.json.get("scene", 0)
        return list(scenes[idx].get("nodes", [])) if scenes else []

    @property
    def root(self) -> int:
        roots = self.root_nodes
        if not roots:
            raise ValueError("glTF document has no scene root")
        return roots[0]

    def use_extension(self, name: str, required: bool = False) -> None:
        used = self._list("extensionsUsed")
        if name not in used:
            used.append(name)
        if required:
            req = self._list("extensionsRequired")
            if name not in req:
                req.append(name)

    # ---------- graph queries ----------
    def parents(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for i, n in enumerate(self.nodes):
            for c in n.get("children", []):
                out[c] = i
        return out

    def by_name(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for i, n in enumerate(self.nodes):
            out.setdefault(n.get("name", ""), []).append(i)
        return out

    def find(self, name: str) -> int | None:
        hits = self.by_name().get(name)
        return hits[0] if hits else None

    def descendants(self, idx: int) -> list[int]:
        out: list[int] = []
        stack = [idx]
        while stack:
            i = stack.pop()
            out.append(i)
            stack.extend(self.nodes[i].get("children", []))
        return out

    def world_matrix(self, idx: int) -> list[float]:
        """Column-major 4x4 of a node in scene space."""
        parents = self.parents()
        chain = []
        cur: int | None = idx
        while cur is not None:
            chain.append(cur)
            cur = parents.get(cur)
        m = _IDENTITY[:]
        for i in reversed(chain):
            m = mat4_mul(m, node_local_matrix(self.nodes[i]))
        return m

    # ---------- mutation ----------
    def add_node(
        self,
        name: str,
        parent: int | None,
        translation: list[float] | None = None,
        rotation: list[float] | None = None,
        scale: list[float] | None = None,
        extras: dict[str, Any] | None = None,
    ) -> int:
        node: dict[str, Any] = {"name": name}
        set_trs(node, translation, rotation, scale)
        if extras:
            node["extras"] = extras
        self.nodes.append(node)
        idx = len(self.nodes) - 1
        if parent is not None:
            self.nodes[parent].setdefault("children", []).append(idx)
        return idx

    def clone_subtree(self, idx: int, parent: int) -> int:
        """Duplicate a node subtree under `parent`; meshes and materials are shared, not copied."""
        node = copy.deepcopy(self.nodes[idx])
        children = node.pop("children", [])
        self.nodes.append(node)
        new = len(self.nodes) - 1
        self.nodes[parent].setdefault("children", []).append(new)
        for c in children:
            self.clone_subtree(c, new)
        return new

    def detach(self, idx: int) -> None:
        """Remove a node (and so its subtree) from the graph; `compact()` drops the data."""
        for n in self.nodes:
            ch = n.get("children")
            if ch and idx in ch:
                ch.remove(idx)
                if not ch:
                    del n["children"]
        for sc in self.json.get("scenes", []):
            if idx in sc.get("nodes", []):
                sc["nodes"].remove(idx)

    def add_buffer_view(self, data: bytes, target: int | None = None, stride: int | None = None) -> int:
        if len(self.bin) % 4:
            self.bin += b"\0" * (4 - len(self.bin) % 4)
        bv: dict[str, Any] = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target is not None:
            bv["target"] = target
        if stride is not None:
            bv["byteStride"] = stride
        self.bin += data
        self._list("bufferViews").append(bv)
        return len(self.json["bufferViews"]) - 1

    def add_accessor(
        self,
        values: list[float] | list[int],
        kind: str,
        component: int = FLOAT,
        target: int | None = None,
        minmax: bool = True,
    ) -> int:
        n = COMPONENTS[kind]
        count = len(values) // n
        fmt = {FLOAT: "f", UNSIGNED_BYTE: "B", UNSIGNED_SHORT: "H", UNSIGNED_INT: "I"}[component]
        data = struct.pack(f"<{len(values)}{fmt}", *values)
        acc: dict[str, Any] = {
            "bufferView": self.add_buffer_view(data, target),
            "componentType": component,
            "count": count,
            "type": kind,
        }
        if minmax and count:
            acc["min"] = [min(values[i::n]) for i in range(n)]
            acc["max"] = [max(values[i::n]) for i in range(n)]
            if component == FLOAT:
                acc["min"] = _clean(acc["min"])
                acc["max"] = _clean(acc["max"])
        self._list("accessors").append(acc)
        return len(self.json["accessors"]) - 1

    def add_image(self, data: bytes, name: str, mime: str = "image/png") -> int:
        img = {"bufferView": self.add_buffer_view(data), "mimeType": mime, "name": name}
        self._list("images").append(img)
        return len(self.json["images"]) - 1

    def sampler(self, wrap_s: int = 10497, wrap_t: int = 10497, mag: int = 9729, minf: int = 9987) -> int:
        want = {"magFilter": mag, "minFilter": minf, "wrapS": wrap_s, "wrapT": wrap_t}
        samplers = self._list("samplers")
        for i, s in enumerate(samplers):
            if s == want:
                return i
        samplers.append(want)
        return len(samplers) - 1

    def add_texture(self, image: int, sampler: int | None = None) -> int:
        tex: dict[str, Any] = {"source": image, "sampler": self.sampler() if sampler is None else sampler}
        self._list("textures").append(tex)
        return len(self.json["textures"]) - 1

    def add_material(self, material: dict[str, Any]) -> int:
        self.materials.append(material)
        return len(self.materials) - 1

    def clone_mesh(self, mesh: int) -> int:
        self.meshes.append(copy.deepcopy(self.meshes[mesh]))
        return len(self.meshes) - 1

    def add_mesh(self, name: str, primitives: list[dict[str, Any]]) -> int:
        self.meshes.append({"name": name, "primitives": primitives})
        return len(self.meshes) - 1

    # ---------- merge ----------
    def merge(self, other: Document, parent: int) -> int:
        """Append every object of `other` and hang its scene root under `parent`; returns the new root index."""
        other = Document(copy.deepcopy(other.json), bytearray(other.bin))
        off = {
            "bufferViews": len(self._list("bufferViews")),
            "accessors": len(self._list("accessors")),
            "images": len(self._list("images")),
            "textures": len(self._list("textures")),
            "materials": len(self.materials),
            "meshes": len(self.meshes),
            "nodes": len(self.nodes),
        }
        if len(self.bin) % 4:
            self.bin += b"\0" * (4 - len(self.bin) % 4)
        base = len(self.bin)
        self.bin += other.bin
        for bv in other.json.get("bufferViews", []):
            bv["byteOffset"] = bv.get("byteOffset", 0) + base
            bv["buffer"] = 0
            self.json["bufferViews"].append(bv)
        for acc in other.json.get("accessors", []):
            if "bufferView" in acc:
                acc["bufferView"] += off["bufferViews"]
            if "sparse" in acc:
                raise ValueError("sparse accessors are not supported")
            self.json["accessors"].append(acc)
        for img in other.json.get("images", []):
            if "bufferView" in img:
                img["bufferView"] += off["bufferViews"]
            self.json["images"].append(img)
        sampler_map = {i: self.sampler(**_sampler_kwargs(s)) for i, s in enumerate(other.json.get("samplers", []))}
        for tex in other.json.get("textures", []):
            if "source" in tex:
                tex["source"] += off["images"]
            tex["sampler"] = sampler_map.get(tex.get("sampler", -1), self.sampler())
            self.json["textures"].append(tex)
        for mat in other.json.get("materials", []):
            _walk_texture_infos(mat, lambda ti: ti.__setitem__("index", ti["index"] + off["textures"]))
            self.materials.append(mat)
        for mesh in other.json.get("meshes", []):
            for prim in mesh.get("primitives", []):
                prim["attributes"] = {k: v + off["accessors"] for k, v in prim["attributes"].items()}
                if "indices" in prim:
                    prim["indices"] += off["accessors"]
                if "material" in prim:
                    prim["material"] += off["materials"]
                if "targets" in prim:
                    prim["targets"] = [{k: v + off["accessors"] for k, v in t.items()} for t in prim["targets"]]
            self.meshes.append(mesh)
        for node in other.json.get("nodes", []):
            if "mesh" in node:
                node["mesh"] += off["meshes"]
            if "children" in node:
                node["children"] = [c + off["nodes"] for c in node["children"]]
            node.pop("skin", None)
            node.pop("camera", None)
            self.nodes.append(node)
        for ext in other.json.get("extensionsUsed", []):
            if ext.startswith("GODOT_"):  # GODOT_single_root only describes the (now nested) scene root
                continue
            self.use_extension(ext, ext in other.json.get("extensionsRequired", []))
        roots = [r + off["nodes"] for r in other.root_nodes]
        if len(roots) == 1:
            root = roots[0]
        else:  # several scene roots: group them
            root = self.add_node(other.json.get("scenes", [{}])[0].get("name", "Scene"), None)
            self.nodes[root]["children"] = roots
        self.nodes[parent].setdefault("children", []).append(root)
        return root

    # ---------- compaction ----------
    def compact(self) -> None:
        """Drop everything unreachable from the scene and rebuild the binary buffer."""
        js = self.json
        live_nodes: list[int] = []
        for r in self.root_nodes:
            live_nodes.extend(self.descendants(r))
        live_nodes = sorted(set(live_nodes))
        node_map = {old: new for new, old in enumerate(live_nodes)}

        mesh_ids = sorted({js["nodes"][i]["mesh"] for i in live_nodes if "mesh" in js["nodes"][i]})
        mesh_map = {old: new for new, old in enumerate(mesh_ids)}
        meshes = [js["meshes"][i] for i in mesh_ids] if mesh_ids else []

        mat_ids: set[int] = set()
        acc_ids: set[int] = set()
        for m in meshes:
            for p in m.get("primitives", []):
                if "material" in p:
                    mat_ids.add(p["material"])
                acc_ids.update(p["attributes"].values())
                if "indices" in p:
                    acc_ids.add(p["indices"])
                for t in p.get("targets", []):
                    acc_ids.update(t.values())

        # animations: keep channels whose node survives
        anims_out = []
        for anim in js.get("animations", []):
            channels = [c for c in anim.get("channels", []) if c["target"].get("node") in node_map]
            if not channels:
                continue
            used_samplers = sorted({c["sampler"] for c in channels})
            smap = {old: new for new, old in enumerate(used_samplers)}
            samplers = [anim["samplers"][i] for i in used_samplers]
            for s in samplers:
                acc_ids.add(s["input"])
                acc_ids.add(s["output"])
            for c in channels:
                c["sampler"] = smap[c["sampler"]]
                c["target"]["node"] = node_map[c["target"]["node"]]
            anims_out.append({**anim, "channels": channels, "samplers": samplers})

        mat_list = sorted(mat_ids)
        mat_map = {old: new for new, old in enumerate(mat_list)}
        materials = [js["materials"][i] for i in mat_list] if mat_list else []
        tex_ids: set[int] = set()
        for m in materials:
            _walk_texture_infos(m, lambda ti: tex_ids.add(ti["index"]))
        tex_list = sorted(tex_ids)
        tex_map = {old: new for new, old in enumerate(tex_list)}
        textures = [js["textures"][i] for i in tex_list] if tex_list else []
        img_ids = sorted({t["source"] for t in textures if "source" in t})
        img_map = {old: new for new, old in enumerate(img_ids)}
        images = [js["images"][i] for i in img_ids] if img_ids else []
        smp_ids = sorted({t["sampler"] for t in textures if "sampler" in t})
        smp_map = {old: new for new, old in enumerate(smp_ids)}
        samplers_out = [js["samplers"][i] for i in smp_ids] if smp_ids else []

        acc_list = sorted(acc_ids)
        acc_map = {old: new for new, old in enumerate(acc_list)}
        accessors = [js["accessors"][i] for i in acc_list] if acc_list else []
        bv_ids = sorted(
            {a["bufferView"] for a in accessors if "bufferView" in a}
            | {i["bufferView"] for i in images if "bufferView" in i}
        )
        bv_map = {old: new for new, old in enumerate(bv_ids)}
        new_bin = bytearray()
        buffer_views = []
        for old in bv_ids:
            bv = dict(js["bufferViews"][old])
            start = bv.get("byteOffset", 0)
            data = bytes(self.bin[start : start + bv["byteLength"]])
            if len(new_bin) % 4:
                new_bin += b"\0" * (4 - len(new_bin) % 4)
            bv["byteOffset"] = len(new_bin)
            bv["buffer"] = 0
            new_bin += data
            buffer_views.append(bv)

        for a in accessors:
            if "bufferView" in a:
                a["bufferView"] = bv_map[a["bufferView"]]
        for i in images:
            if "bufferView" in i:
                i["bufferView"] = bv_map[i["bufferView"]]
        for t in textures:
            if "source" in t:
                t["source"] = img_map[t["source"]]
            if "sampler" in t:
                t["sampler"] = smp_map[t["sampler"]]
        for m in materials:
            _walk_texture_infos(m, lambda ti: ti.__setitem__("index", tex_map[ti["index"]]))
        for m in meshes:
            for p in m.get("primitives", []):
                p["attributes"] = {k: acc_map[v] for k, v in p["attributes"].items()}
                if "indices" in p:
                    p["indices"] = acc_map[p["indices"]]
                if "material" in p:
                    p["material"] = mat_map[p["material"]]
                if "targets" in p:
                    p["targets"] = [{k: acc_map[v] for k, v in t.items()} for t in p["targets"]]
        for anim in anims_out:
            for s in anim["samplers"]:
                s["input"] = acc_map[s["input"]]
                s["output"] = acc_map[s["output"]]
        nodes = []
        for old in live_nodes:
            n = js["nodes"][old]
            if "mesh" in n:
                n["mesh"] = mesh_map[n["mesh"]]
            if "children" in n:
                n["children"] = [node_map[c] for c in n["children"] if c in node_map]
                if not n["children"]:
                    del n["children"]
            nodes.append(n)
        for sc in js.get("scenes", []):
            sc["nodes"] = [node_map[r] for r in sc.get("nodes", []) if r in node_map]

        for key, value in (
            ("nodes", nodes),
            ("meshes", meshes),
            ("materials", materials),
            ("textures", textures),
            ("images", images),
            ("samplers", samplers_out),
            ("accessors", accessors),
            ("bufferViews", buffer_views),
            ("animations", anims_out),
        ):
            if value:
                js[key] = value
            else:
                js.pop(key, None)
        self.bin = new_bin
        if new_bin:
            js["buffers"] = [{"byteLength": len(new_bin)}]
        else:
            js.pop("buffers", None)
        for key in ("extensionsUsed", "extensionsRequired"):
            if key in js and not js[key]:
                del js[key]


_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def mat4_mul(a: list[float], b: list[float]) -> list[float]:
    """Column-major product a·b."""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def node_local_matrix(node: dict[str, Any]) -> list[float]:
    if "matrix" in node:
        return list(node["matrix"])
    t = node.get("translation", [0.0, 0.0, 0.0])
    q = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    s = node.get("scale", [1.0, 1.0, 1.0])
    x, y, z, w = q
    r = [
        1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w),
        2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w),
        2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y),
    ]  # fmt: skip
    return [
        r[0] * s[0], r[1] * s[0], r[2] * s[0], 0.0,
        r[3] * s[1], r[4] * s[1], r[5] * s[1], 0.0,
        r[6] * s[2], r[7] * s[2], r[8] * s[2], 0.0,
        t[0], t[1], t[2], 1.0,
    ]  # fmt: skip


def set_trs(
    node: dict[str, Any],
    translation: list[float] | None,
    rotation: list[float] | None,
    scale: list[float] | None,
) -> None:
    node.pop("matrix", None)
    for key, value, default in (
        ("translation", translation, [0.0, 0.0, 0.0]),
        ("rotation", rotation, [0.0, 0.0, 0.0, 1.0]),
        ("scale", scale, [1.0, 1.0, 1.0]),
    ):
        if value is None:
            continue
        value = _clean(value)
        if value == default:
            node.pop(key, None)
        else:
            node[key] = value


def set_matrix(node: dict[str, Any], m: list[float]) -> None:
    """Store a column-major 4x4 as TRS (animated nodes must not carry `matrix`)."""
    t, q, s = trs_from_mat4(m)
    node.pop("translation", None)
    node.pop("rotation", None)
    node.pop("scale", None)
    set_trs(node, t, q, s)


def _sampler_kwargs(s: dict[str, Any]) -> dict[str, int]:
    return {
        "wrap_s": s.get("wrapS", 10497),
        "wrap_t": s.get("wrapT", 10497),
        "mag": s.get("magFilter", 9729),
        "minf": s.get("minFilter", 9987),
    }


def _walk_texture_infos(value: Any, fn: Any) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("index"), int) and set(value) <= TEXTURE_INFO_KEYS:
            fn(value)
            return
        for v in value.values():
            _walk_texture_infos(v, fn)
    elif isinstance(value, list):
        for v in value:
            _walk_texture_infos(v, fn)
