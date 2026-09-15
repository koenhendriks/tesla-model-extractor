from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml

RULES_DIR = Path(__file__).parent

Rules = dict[str, Any]


def _deep_merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def _fix_keys(v: Any) -> Any:
    """YAML 1.1 turns bare `on` / `off` keys into booleans – map them back (rules use them as group names)."""
    if isinstance(v, dict):
        return {("on" if k is True else "off" if k is False else k): _fix_keys(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_fix_keys(x) for x in v]
    return v


def _load_yaml(p: Path) -> dict[str, Any]:
    with open(p, encoding="utf-8") as fh:
        data = _fix_keys(yaml.safe_load(fh) or {})
    if not isinstance(data, dict):
        raise ValueError(f"{p}: rules must be a mapping")
    return data


def default_rules() -> Rules:
    return _load_yaml(RULES_DIR / "_default.yaml")


def all_codename_rules(extra_dir: Path | None = None) -> dict[str, Rules]:
    """codename → rules file content (without defaults merged)."""
    out: dict[str, Rules] = {}
    dirs = [RULES_DIR] + ([extra_dir] if extra_dir else [])
    for d in dirs:
        for p in sorted(d.glob("*.yaml")):
            if p.name.startswith("_"):
                continue
            data = _load_yaml(p)
            code = str(data.get("codename") or p.stem)
            out[code] = data
    return out


def slug(codename: str) -> str:
    """`BayberryE41` → `bayberry_e41`, `S_Palladium` → `s_palladium`, `3_High` → `3_high`."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", "_", codename)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return s or "vehicle"


def load_rules(codename: str, extra_dir: Path | None = None) -> Rules:
    """Defaults deep-merged with the codename's file (if any). Always sets id/name/codename/scene keys."""
    base = default_rules()
    specific = all_codename_rules(extra_dir).get(codename, {})
    merged = _deep_merge(base, specific)
    merged.setdefault("codename", codename)
    merged.setdefault("id", slug(codename))
    merged.setdefault("name", codename)
    merged.setdefault("aliases", [])
    return merged


def rules_for_scene(scene_rel: str, extra_dir: Path | None = None) -> Rules:
    """Find the rules file whose `scene` matches, else derive the codename from the scene folder."""
    for code, data in all_codename_rules(extra_dir).items():
        if data.get("scene") == scene_rel:
            return load_rules(code, extra_dir)
    folder = scene_rel.rsplit("/", 1)[0].rsplit("/", 1)[-1]
    r = load_rules(folder, extra_dir)
    r["scene"] = scene_rel
    return r
