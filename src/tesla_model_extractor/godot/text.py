"""Parser for Godot 3 text resources: `.tscn`, `.tres` and text `.material` files.

A file is a sequence of blocks. Every block starts with a header line `[kind attr=value ...]` and is followed by
`key = value` lines; a value may continue over several lines (arrays / dictionaries). Only the header syntax
`[gd_scene ...]`, `[gd_resource ...]`, `[ext_resource ...]`, `[sub_resource ...]`, `[node ...]`, `[resource]`,
`[connection ...]`, `[editable ...]` is recognised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .values import ExtRef, GodotValueError, SubRef, _Parser, tokenize

_HEADER = re.compile(r"^\[([A-Za-z_]+)(.*)\]\s*$")
_HEADER_START = re.compile(r"^\[(gd_scene|gd_resource|ext_resource|sub_resource|node|resource|connection|editable)\b")
_PROP = re.compile(r"^([A-Za-z_][\w/]*(?:\.[\w/]+)*) = (.*)$")


@dataclass
class ExtResource:
    id: int
    path: str
    type: str | None

    @property
    def rel(self) -> str:
        """Path relative to the project root (`res://` stripped)."""
        return strip_res(self.path)


@dataclass
class Block:
    kind: str
    attrs: dict[str, Any]
    props: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, str] = field(default_factory=dict)
    line: int = 0

    @property
    def name(self) -> str | None:
        v = self.attrs.get("name")
        return v if isinstance(v, str) else None

    @property
    def type(self) -> str | None:
        v = self.attrs.get("type")
        return v if isinstance(v, str) else None

    @property
    def parent(self) -> str | None:
        v = self.attrs.get("parent")
        return v if isinstance(v, str) else None

    @property
    def id(self) -> int | None:
        v = self.attrs.get("id")
        return int(v) if isinstance(v, (int, float)) else None


@dataclass
class GodotFile:
    path: Path | None
    header: Block
    ext: dict[int, ExtResource]
    subs: dict[int, Block]
    blocks: list[Block]  # everything except ext_resource, in file order (sub_resources included)

    @property
    def resource_type(self) -> str | None:
        v = self.header.attrs.get("type")
        return v if isinstance(v, str) else None

    @property
    def nodes(self) -> list[Block]:
        return [b for b in self.blocks if b.kind == "node"]

    @property
    def resource(self) -> Block | None:
        return next((b for b in self.blocks if b.kind == "resource"), None)

    def ext_path(self, v: Any) -> str | None:
        """`ExtRef` → `res://` path, else None."""
        if isinstance(v, ExtRef) and v.id in self.ext:
            return self.ext[v.id].path
        return None

    def ext_rel(self, v: Any) -> str | None:
        p = self.ext_path(v)
        return strip_res(p) if p else None

    def sub(self, v: Any) -> Block | None:
        if isinstance(v, SubRef):
            return self.subs.get(v.id)
        return None


def strip_res(p: str) -> str:
    return p[6:] if p.startswith("res://") else p


def is_text_resource(head: bytes) -> bool:
    return head.lstrip().startswith((b"[gd_resource", b"[gd_scene"))


def parse_header_attrs(text: str) -> dict[str, Any]:
    """`name="X" type="Spatial" parent="." instance=ExtResource( 6 )` → dict (values parsed like Godot values)."""
    attrs: dict[str, Any] = {}
    p = _Parser(tokenize(text))
    while p.peek() is not None:
        kind, key = p.next()
        if kind != "ident":
            raise GodotValueError(f"bad header attribute {key!r} in [{text}]")
        p.expect("=")
        attrs[key] = p.value()
    return attrs


def _join_headers(lines: list[str]) -> list[str]:
    """Fold multi-line block headers (`[node name="X" groups=[` / `"signature",` / `]]`) into one line.

    Godot writes node groups over several lines; a header that is not folded is not recognised and the properties
    that follow it (typically `visible = false`) end up on the previous node."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _HEADER_START.match(line) and not _balanced(line):
            buf = line
            while not _balanced(buf) and i + 1 < len(lines):
                i += 1
                buf += " " + lines[i].strip()
            line = buf
        out.append(line)
        i += 1
    return out


def parse_text(text: str, path: Path | None = None) -> GodotFile:
    lines = _join_headers(text.splitlines())
    header: Block | None = None
    blocks: list[Block] = []
    ext: dict[int, ExtResource] = {}
    cur: Block | None = None
    cur_key: str | None = None

    for lineno, line in enumerate(lines, 1):
        m = _HEADER.match(line)
        if m and (cur is None or cur_key is None or _balanced(cur.raw.get(cur_key, ""))):
            kind, rest = m.group(1), m.group(2)
            attrs = parse_header_attrs(rest) if rest.strip() else {}
            blk = Block(kind=kind, attrs=attrs, line=lineno)
            cur_key = None
            if kind in ("gd_scene", "gd_resource"):
                header = blk
                cur = blk
            elif kind == "ext_resource":
                ext[int(attrs["id"])] = ExtResource(int(attrs["id"]), str(attrs.get("path", "")), attrs.get("type"))
                cur = None
            else:
                blocks.append(blk)
                cur = blk
            continue
        if cur is None:
            continue
        pm = _PROP.match(line)
        if pm and (cur_key is None or _balanced(cur.raw.get(cur_key, ""))):
            cur_key = pm.group(1)
            cur.raw[cur_key] = pm.group(2)
        elif cur_key is not None and line.strip():
            cur.raw[cur_key] += "\n" + line

    if header is None:
        header = Block(kind="gd_resource", attrs={})
    subs: dict[int, Block] = {}
    for b in blocks:
        for k, raw in b.raw.items():
            try:
                b.props[k] = _Parser(tokenize(raw)).value()
            except GodotValueError as e:  # keep going – one odd literal must not kill the whole file
                b.props[k] = raw
                b.props.setdefault("__parse_errors__", []).append(f"{k}: {e}")
        if b.kind == "sub_resource" and b.id is not None:
            subs[b.id] = b
    return GodotFile(path=path, header=header, ext=ext, subs=subs, blocks=blocks)


def _balanced(raw: str) -> bool:
    """True when brackets/parens/braces in `raw` are balanced (ignoring those inside strings)."""
    depth = 0
    in_str = False
    esc = False
    for ch in raw:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    return depth <= 0 and not in_str


def parse_file(path: Path) -> GodotFile:
    return parse_text(path.read_text(encoding="utf-8", errors="replace"), path)
