"""Tokenizer + parser for Godot text-format values (the right-hand side of `key = value`).

Handles everything the Tesla app's resources use: literals, strings, `Name( args )` constructors
(Color, Transform, Vector3, NodePath, ExtResource, SubResource, Pool*Array, AABB, ...), arrays `[ ... ]`,
dictionaries `{ key: value, ... }` and bare identifiers (GDScript constants such as `BASE_ROUGHNESS` or
`WheelType.Apollo`, which appear when the same parser is pointed at decompiled scripts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class GodotValueError(ValueError):
    """Raised when a value cannot be parsed."""


@dataclass(frozen=True)
class Ident:
    """A bare identifier (possibly dotted), e.g. `BASE_ROUGHNESS` or `WheelType.Gemini`."""

    name: str


@dataclass(frozen=True)
class Call:
    """A constructor call such as `Color( 1, 0, 0, 1 )` or `NodePath("a/b")`."""

    name: str
    args: tuple[Any, ...]

    def floats(self) -> list[float]:
        return [float(a) for a in self.args if isinstance(a, (int, float)) and not isinstance(a, bool)]


@dataclass(frozen=True)
class ExtRef:
    id: int


@dataclass(frozen=True)
class SubRef:
    id: int


_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)
  | (?P<ident>[A-Za-z_@][A-Za-z_0-9@]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*)
  | (?P<punct>[()\[\]{}:,=])
  | (?P<minus>-)
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            raise GodotValueError(f"unexpected character {text[pos]!r} at {pos}: {text[max(0, pos - 20) : pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        tokens.append((kind, m.group()))  # type: ignore[arg-type]
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.t = tokens
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise GodotValueError("unexpected end of value")
        self.i += 1
        return tok

    def expect(self, value: str) -> None:
        tok = self.next()
        if tok[1] != value:
            raise GodotValueError(f"expected {value!r}, got {tok[1]!r}")

    def value(self) -> Any:
        kind, text = self.next()
        if kind == "str":
            return _unescape(text[1:-1])
        if kind == "num":
            return _number(text)
        if kind == "minus":  # unary minus followed by whitespace, as in decompiled GDScript `Vector3(68.6, - 138, 0)`
            kind2, text2 = self.next()
            if kind2 != "num":
                raise GodotValueError(f"unary minus before {text2!r}")
            return -_number(text2)
        if kind == "punct" and text == "[":
            items: list[Any] = []
            while True:
                nxt = self.peek()
                if nxt is None:
                    raise GodotValueError("unterminated array")
                if nxt[1] == "]":
                    self.next()
                    return items
                items.append(self.value())
                if self.peek() and self.peek()[1] == ",":  # type: ignore[index]
                    self.next()
        if kind == "punct" and text == "{":
            result: dict[Any, Any] = {}
            while True:
                nxt = self.peek()
                if nxt is None:
                    raise GodotValueError("unterminated dictionary")
                if nxt[1] == "}":
                    self.next()
                    return result
                key = self.value()
                self.expect(":")
                val = self.value()
                result[_dict_key(key)] = val
                if self.peek() and self.peek()[1] == ",":  # type: ignore[index]
                    self.next()
        if kind == "ident":
            if text == "true":
                return True
            if text == "false":
                return False
            if text in ("null", "nil"):
                return None
            if text == "inf":
                return float("inf")
            if text == "nan":
                return float("nan")
            nxt = self.peek()
            if nxt and nxt[1] == "(":
                self.next()
                args: list[Any] = []
                while True:
                    nxt = self.peek()
                    if nxt is None:
                        raise GodotValueError(f"unterminated call {text}(")
                    if nxt[1] == ")":
                        self.next()
                        break
                    args.append(self.value())
                    if self.peek() and self.peek()[1] == ",":  # type: ignore[index]
                        self.next()
                return _make_call(text, tuple(args))
            return Ident(text)
        raise GodotValueError(f"unexpected token {text!r}")


def _dict_key(key: Any) -> Any:
    if isinstance(key, (str, int, float, bool)) or key is None:
        return key
    if isinstance(key, Call) and key.name == "NodePath" and key.args:
        return key  # frozen dataclass → hashable
    if isinstance(key, Ident):
        return key
    return repr(key)


def _make_call(name: str, args: tuple[Any, ...]) -> Any:
    if name == "ExtResource" and len(args) == 1:
        return ExtRef(int(args[0]))
    if name == "SubResource" and len(args) == 1:
        return SubRef(int(args[0]))
    return Call(name, args)


def _number(text: str) -> int | float:
    if re.fullmatch(r"[-+]?\d+", text):
        return int(text)
    return float(text)


def _unescape(s: str) -> str:
    return s.encode("utf-8").decode("unicode_escape") if "\\" in s else s


def parse_value(text: str) -> Any:
    """Parse one Godot value. Trailing garbage is an error."""
    p = _Parser(tokenize(text))
    v = p.value()
    if p.peek() is not None:
        raise GodotValueError(f"trailing tokens after value: {p.peek()!r}")
    return v


def parse_values(text: str) -> list[Any]:
    """Parse a sequence of values separated by commas/whitespace (used for enum bodies etc.)."""
    p = _Parser(tokenize(text))
    out = []
    while p.peek() is not None:
        if p.peek()[1] == ",":  # type: ignore[index]
            p.next()
            continue
        out.append(p.value())
    return out


# ---------- typed helpers ----------


def node_path(v: Any) -> str | None:
    if isinstance(v, Call) and v.name == "NodePath" and v.args and isinstance(v.args[0], str):
        return v.args[0]
    return None


def color(v: Any) -> list[float] | None:
    if isinstance(v, Call) and v.name == "Color":
        if len(v.args) == 1 and isinstance(v.args[0], str):
            return hex_color(v.args[0])
        return v.floats()
    return None


def hex_color(s: str) -> list[float]:
    h = s.lstrip("#")
    if len(h) == 6:
        h += "ff"
    if len(h) != 8:
        raise GodotValueError(f"bad colour literal {s!r}")
    return [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4, 6)]


def transform12(v: Any) -> list[float] | None:
    """Godot `Transform( xx,xy,xz, yx,yy,yz, zx,zy,zz, ox,oy,oz )` → the 12 floats (basis rows + origin)."""
    if isinstance(v, Call) and v.name == "Transform":
        f = v.floats()
        return f if len(f) == 12 else None
    return None


def pool_floats(v: Any) -> list[float] | None:
    if isinstance(v, Call) and v.name in ("PoolRealArray", "PoolIntArray", "PackedFloat32Array", "PackedInt32Array"):
        return [float(a) for a in v.args]
    return None


def vector3(v: Any) -> list[float] | None:
    if isinstance(v, Call) and v.name == "Vector3":
        f = v.floats()
        return f if len(f) == 3 else None
    return None
