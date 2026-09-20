"""Tolerant helpers for reading constants out of decompiled GDScript (Godot 3.2 syntax, GDRE output)."""

from __future__ import annotations

import re
from typing import Any

from ..godot.values import Call, GodotValueError, Ident, parse_value


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        in_str = False
        esc = False
        cut = len(line)
        for i, ch in enumerate(line):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "#":
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def balanced(text: str, start: int, open_ch: str = "{", close_ch: str = "}") -> str:
    """Return the text of the bracketed block starting at `start` (index of `open_ch`), inclusive."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
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
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise GodotValueError(f"unbalanced {open_ch} at {start}")


def const_block(text: str, name: str, open_ch: str = "{", close_ch: str = "}") -> str | None:
    """`const NAME[: Type] = {` … `}` → block text including braces."""
    m = re.search(rf"^\s*(?:const|var)\s+{re.escape(name)}\b[^=\n]*=\s*", text, re.M)
    if not m:
        return None
    i = text.find(open_ch, m.end())
    if i < 0 or text[m.end() : i].strip():
        return None
    return balanced(text, i, open_ch, close_ch)


def const_scalar(text: str, name: str) -> Any:
    m = re.search(rf"^\s*const\s+{re.escape(name)}\b[^=\n]*=\s*(.+?)\s*;?\s*$", text, re.M)
    if not m:
        return None
    try:
        return parse_value(m.group(1))
    except GodotValueError:
        return m.group(1)


def parse_dict(block: str, consts: dict[str, Any] | None = None) -> dict[Any, Any]:
    """Parse a GDScript dictionary literal; bare identifiers are resolved through `consts` when possible."""
    v = parse_value(block)
    if not isinstance(v, dict):
        raise GodotValueError("not a dictionary")
    return {_res(k, consts): _res(x, consts) for k, x in v.items()}


def _res(v: Any, consts: dict[str, Any] | None) -> Any:
    if isinstance(v, Ident) and consts and v.name in consts:
        return consts[v.name]
    if isinstance(v, dict):
        return {_res(k, consts): _res(x, consts) for k, x in v.items()}
    if isinstance(v, list):
        return [_res(x, consts) for x in v]
    return v


def enum_members(text: str, name: str) -> list[tuple[str, int]]:
    """`enum Name{ A, B = 3, C D }` → [(A,0),(B,3),(C,4),(D,5)] — tolerates a missing comma (present in VehicleOptions.gd)."""
    m = re.search(rf"^\s*enum\s+{re.escape(name)}\s*\{{", text, re.M)
    if not m:
        return []
    body = balanced(text, m.end() - 1)[1:-1]
    members: list[tuple[str, int]] = []
    nxt = 0
    for tok in re.finditer(r"([A-Za-z_]\w*)\s*(?:=\s*(-?\d+))?", body):
        ident, val = tok.group(1), tok.group(2)
        if val is not None:
            nxt = int(val)
        members.append((ident, nxt))
        nxt += 1
    return members


def ident_name(v: Any) -> str | None:
    if isinstance(v, Ident):
        return v.name
    return None


def node_path_arg(v: Any) -> str | None:
    if isinstance(v, Call) and v.name == "NodePath" and v.args and isinstance(v.args[0], str):
        return v.args[0]
    return None
