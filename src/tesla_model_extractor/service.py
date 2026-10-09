"""The extraction pipeline without a terminal: the CLI and the desktop app both drive it.

Nothing here prints, prompts or exits. Failures raise `ExtractError`; progress goes through the optional `notify`
callback and the module loggers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bundle import BundleError, detect, recover
from .catalog import Catalog, VehicleEntry, build_catalog, resolve_default_wheel, resolve_wheel_family
from .gdre import Gdre, GdreError, Progress
from .godot.resolve import ResourceRoot
from .manifest import BuildResult, PackBuilder, summarize_model
from .pack import MIB, PackStats, write_dir, write_zip
from .unreal.export import VehicleOutput, export_dae_pack, export_pack
from .unreal.packsource import is_pack, load_pack
from .unreal.scene import ExportOptions
from .validate import DEFAULT_MAX_MIB, Report, validate_pack

Notify = Callable[[str, str], None]  # (level "info" | "warning", message)


class ExtractError(RuntimeError):
    pass


class PackInvalid(ExtractError):
    def __init__(self, output: PackOutput, hint: str | None = None):
        self.output = output
        self.hint = hint
        super().__init__(f"{output.target} failed validation: " + "; ".join(output.validation.errors))


@dataclass
class VehicleRow:
    """One line of the vehicle picker, for bundles and asset packs alike."""

    id: str
    name: str
    codename: str
    api: str
    present: bool
    model_keys: list[str] = field(default_factory=list)
    size_bytes: int = 0
    wheel_family: str | None = None
    default_wheel: str | None = None
    wheels: list[str] = field(default_factory=list)
    brake_sets: list[str] = field(default_factory=list)


@dataclass
class Session:
    source: Path
    kind: str  # bundle | apk | godot_root | recovered | pack
    app_version: str | None
    catalog: Catalog | None = None
    pack: BuildResult | None = None
    gdre: Gdre | None = None
    recovered: Path | None = None

    @property
    def is_pack(self) -> bool:
        return self.pack is not None

    def vehicles(self) -> list[VehicleRow]:
        if self.pack is not None:
            return _pack_rows(self.pack)
        assert self.catalog is not None
        return _catalog_rows(self.catalog)

    def paints(self) -> list[str]:
        if self.pack is not None:
            return sorted((self.pack.manifest.get("paints") or {}).get("colors") or {})
        assert self.catalog is not None
        return sorted(self.catalog.options.paints)

    def all_wheels(self) -> list[str]:
        if self.pack is not None:
            return sorted(self.pack.manifest.get("wheels") or {})
        assert self.catalog is not None
        return [w.api_name for w in self.catalog.wheels if w.present]

    def default_vehicle(self) -> VehicleRow | None:
        present = [v for v in self.vehicles() if v.present]
        return next((v for v in present if v.model_keys == ["modely"]), present[0] if present else None)


def _catalog_rows(cat: Catalog) -> list[VehicleRow]:
    rows = []
    for v in cat.vehicles:
        fam = resolve_wheel_family(cat, v) if v.present else None
        sets = ((v.rules.get("brakes") or {}).get("sets") or {}).values()
        rows.append(
            VehicleRow(
                id=v.id,
                name=v.name,
                codename=v.codename,
                api=", ".join(v.model_keys) + (f" ({', '.join(v.fascia_types)})" if v.fascia_types else ""),
                present=v.present,
                model_keys=v.model_keys,
                size_bytes=v.size_bytes,
                wheel_family=fam,
                default_wheel=resolve_default_wheel(cat, v, fam) if v.present else None,
                wheels=[w.api_name for w in cat.wheels_in_family(fam)] if fam else [],
                brake_sets=sorted({str(s) for s in sets}),
            )
        )
    return rows


def _pack_rows(pack: BuildResult) -> list[VehicleRow]:
    wheels = pack.manifest.get("wheels") or {}
    rows = []
    for mid, m in (pack.manifest.get("models") or {}).items():
        fam = m.get("wheel_family")
        api = m.get("api_match") or {}
        rows.append(
            VehicleRow(
                id=mid,
                name=str(m.get("name") or mid),
                codename=str(m.get("codename") or ""),
                api=str(api.get("model_key") or ""),
                present=True,
                model_keys=[str(api["model_key"])] if api.get("model_key") else [],
                wheel_family=fam,
                default_wheel=m.get("default_wheel"),
                wheels=sorted(n for n, w in wheels.items() if w.get("family") == fam),
                brake_sets=sorted(m.get("brakes") or {}),
            )
        )
    return rows


def _say(notify: Notify | None, level: str, msg: str) -> None:
    if notify:
        notify(level, msg)


def locate_gdre(
    gdre_path: str | None = None,
    allow_download: bool = True,
    progress: Progress | None = None,
    notify: Notify | None = None,
) -> Gdre | None:
    """GDRE is only required for bundles, so a failure here is a warning, not an error."""
    try:
        return Gdre.locate(gdre_path, allow_download=allow_download, progress=progress)
    except (GdreError, OSError) as e:
        _say(notify, "warning", f"GDRE Tools unavailable: {e}")
        return None


def open_source(
    source: Path | str,
    *,
    recovered: Path | str | None = None,
    keep_recovered: Path | str | None = None,
    gdre: Gdre | None = None,
    gdre_path: str | None = None,
    allow_download: bool = True,
    rules_dir: Path | str | None = None,
    accept_pack: bool = False,
    drop_godot_root: bool = False,
    progress: Progress | None = None,
    notify: Notify | None = None,
) -> Session:
    """Turn a bundle, an extracted / recovered directory (or, with `accept_pack`, an asset pack) into a `Session`.

    A bundle is unpacked and recovered with GDRE Tools; `keep_recovered` stores that recovery for reuse."""
    src = Path(source)
    if accept_pack and is_pack(src):
        try:
            pack = load_pack(src)
        except (OSError, ValueError) as e:
            raise ExtractError(str(e)) from e
        return Session(src, "pack", pack.manifest.get("app_version"), pack=pack)
    if gdre is None:
        gdre = locate_gdre(gdre_path, allow_download, progress, notify)
    try:
        info = detect(src)
    except (BundleError, OSError) as e:
        raise ExtractError(str(e)) from e
    if recovered:
        recovered_dir = Path(recovered)
    elif info.kind == "recovered":
        recovered_dir = info.source
    else:
        if gdre is None:
            raise ExtractError("a bundle needs GDRE Tools for recovery – pass --gdre PATH or allow the download")
        keep = Path(keep_recovered) if keep_recovered else None
        work = keep.parent if keep else Path(tempfile.mkdtemp(prefix="tve-"))
        _say(notify, "info", f"unpacking {src.name} …")
        try:
            recovered_dir = recover(src, work, gdre, keep, drop_godot_root=drop_godot_root)
        except (BundleError, GdreError, OSError) as e:
            raise ExtractError(str(e)) from e
        _say(notify, "info", f"recovered project: {recovered_dir}")
    root = ResourceRoot(recovered_dir, gdre)
    catalog = build_catalog(root, Path(rules_dir) if rules_dir else None)
    return Session(src, info.kind, info.app_version, catalog=catalog, gdre=gdre, recovered=recovered_dir)


def find_vehicles(session: Session, keys: list[str]) -> list[VehicleEntry]:
    assert session.catalog is not None
    out = []
    for key in keys:
        v = session.catalog.vehicle(key.strip())
        if v is None or not v.present:
            raise ExtractError(f"unknown or missing vehicle {key!r}; run `list` to see what the bundle contains")
        out.append(v)
    return out


def pack_model_ids(manifest: dict[str, Any], keys: list[str] | None) -> list[str]:
    """Model ids of an asset pack matching ids, codenames or aliases (all of them when `keys` is None)."""
    models = manifest.get("models") or {}
    ids = list(models)
    if keys is None:
        return ids
    wanted = [k.strip().lower() for k in keys]
    ids = [
        i
        for i in ids
        if i in wanted
        or str(models[i].get("codename", "")).lower() in wanted
        or any(a.lower() in wanted for a in models[i].get("aliases", []))
    ]
    if len(ids) != len(wanted):
        raise ExtractError(f"pack contains {sorted(models)}, not all of {wanted}")
    return ids


# ---------- asset packs (Tesla View) ----------


def wheel_filter_for_extract(cat: Catalog, spec: str) -> list[str] | None:
    """`family` → None (the builder picks the family), `all` → every present wheel, else a comma list."""
    if spec == "family":
        return None
    if spec == "all":
        return [w.api_name for w in cat.wheels if w.present]
    return [w.strip() for w in spec.split(",") if w.strip()]


@dataclass
class PackOutput:
    target: Path
    models: list[str]
    stats: PackStats
    validation: Report
    summaries: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "pack": str(self.target),
            "models": self.models,
            "files": self.stats.files,
            "raw_bytes": self.stats.raw_bytes,
            "zip_bytes": self.stats.zip_bytes,
            "sha256": self.stats.sha256,
            "warnings": self.warnings,
        }


def pack_targets(
    vehicles: list[VehicleEntry],
    output: Path | None,
    *,
    bundle: bool = False,
    as_dir: bool = False,
    app_version: str | None = None,
    output_is_target: bool = True,
    groups: list[list[VehicleEntry]] | None = None,
) -> list[tuple[list[VehicleEntry], Path]]:
    """Group the vehicles into packs (unless `groups` is given) and name each output: with `output_is_target`, an
    explicit `.zip` / `--dir` path is used as is for a single pack, otherwise
    `<output>/tesla-view-pack-<ids>[-<version>][.zip]`."""
    if groups is None:
        groups = [vehicles] if bundle or len(vehicles) == 1 else [[v] for v in vehicles]
    out_arg = output if output is not None else Path("packs")
    explicit = output is not None and output_is_target
    out = []
    for group in groups:
        ids = "-".join(v.id for v in group)
        if len(groups) == 1 and explicit and (out_arg.suffix == ".zip" or as_dir):
            target = out_arg
        else:
            suffix = "" if as_dir else ".zip"
            target = out_arg / f"tesla-view-pack-{ids}{('-' + app_version) if app_version else ''}{suffix}"
        out.append((group, target))
    return out


def build_pack(
    session: Session,
    group: list[VehicleEntry],
    wheel_filter: list[str] | None,
    target: Path,
    *,
    as_dir: bool = False,
    max_mib: float = DEFAULT_MAX_MIB,
    built: BuildResult | None = None,
    notify: Notify | None = None,
) -> PackOutput:
    """Build (unless `built` already holds this group), write and validate one pack. The caller decides what an
    invalid pack means; `notify` gets the same lines the CLI prints."""
    assert session.catalog is not None
    res = built or PackBuilder(session.catalog).build(group, wheel_filter)
    res.manifest["app_version"] = session.app_version
    res.files["manifest.json"] = (json.dumps(res.manifest, indent=1, ensure_ascii=False) + "\n").encode()
    _say(notify, "info", f"writing {target.name} …")
    st = write_dir(res, target) if as_dir else write_zip(res, target)
    out = PackOutput(
        target=target,
        models=[v.id for v in group],
        stats=st,
        validation=validate_pack(target, max_mib),
        summaries={v.id: summarize_model(res.manifest["models"][v.id]) for v in group},
        warnings=res.warnings,
        missing=res.missing,
    )
    if notify:
        for mid, text in out.summaries.items():
            notify("info", f"{mid}: {text}")
        for w in out.warnings:
            notify("warning", w)
        for m in out.missing:
            notify("warning", f"referenced file not found in the recovered project: {m}")
        size = f"{st.raw_mib:.1f} MiB" if as_dir else f"{st.raw_mib:.1f} MiB raw → {st.zip_mib:.1f} MiB zip"
        notify("info", f"wrote {target} ({st.files} files, {size})")
        for w in out.validation.warnings:
            notify("warning", w)
    return out


# Local file header and central directory record of a zip entry (without the name), and the end record.
_ZIP_ENTRY, _ZIP_END = 30 + 46, 22


class _ZipSizer:
    """Size of each file as a deflated zip entry, computed once per file (the same level `write_zip` uses)."""

    def __init__(self, level: int = 9):
        self.level = level
        self._cache: dict[tuple[str, str], int] = {}

    def entry(self, rel: str, content: bytes | Path) -> int:
        key = (rel, str(content) if isinstance(content, Path) else hashlib.sha1(content).hexdigest())
        if key not in self._cache:
            data = content if isinstance(content, bytes) else Path(content).read_bytes()
            c = zlib.compressobj(self.level, zlib.DEFLATED, -15)
            self._cache[key] = len(c.compress(data)) + len(c.flush()) + _ZIP_ENTRY + 2 * len(rel.encode())
        return self._cache[key]


@contextmanager
def _quiet(logger_name: str) -> Iterator[None]:
    logger = logging.getLogger(logger_name)
    level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(level)


def first_fit(entries: dict[str, dict[str, int]], limit: float) -> list[list[str]]:
    """Group items (id → {file: zip entry size}) so each group's zip stays under `limit`, biggest items first.

    A file shared by several items counts once per group, except the manifest, which grows with every model.
    Groups and their members keep the order of `entries`."""
    groups: list[tuple[list[str], dict[str, int]]] = []
    for key in sorted(entries, key=lambda k: -sum(entries[k].values())):
        mine = entries[key]
        for members, files in groups:
            extra = sum(n for rel, n in mine.items() if rel not in files or rel == "manifest.json")
            if _ZIP_END + sum(files.values()) + extra <= limit:
                members.append(key)
                for rel, n in mine.items():
                    files[rel] = files.get(rel, 0) + n if rel == "manifest.json" else n
                break
        else:
            groups.append(([key], dict(mine)))
    order = {k: i for i, k in enumerate(entries)}
    return sorted((sorted(m, key=order.__getitem__) for m, _ in groups), key=lambda g: order[g[0]])


def plan_zip_groups(
    session: Session,
    vehicles: list[VehicleEntry],
    wheel_filter: list[str] | None,
    max_mib: float,
    notify: Notify | None = None,
) -> tuple[list[list[VehicleEntry]], BuildResult | None]:
    """Spread the vehicles over as few zips as possible, each under `max_mib`.

    Vehicles share files (a wheel family, the cables, the studio panorama), so a group is sized by the union of its
    files, each counted at its real deflated size. Returns the groups and, when everything fits in one zip, the
    already built pack so it is not built twice."""
    assert session.catalog is not None
    cat = session.catalog
    limit = max_mib * MIB * 0.99  # headroom for the manifest of a merged group
    built = PackBuilder(cat).build(vehicles, wheel_filter)
    raw = sum(len(c) if isinstance(c, bytes) else Path(c).stat().st_size for c in built.files.values())
    # deflate never grows data by more than a few bytes per 16 KiB block, so the raw size bounds the zip
    bound = _ZIP_END + raw * 1.001 + sum(64 + _ZIP_ENTRY + 2 * len(rel.encode()) for rel in built.files)
    if len(vehicles) == 1 or bound <= limit:
        return [vehicles], built
    _say(notify, "info", "estimating the zip size …")
    sizer = _ZipSizer()
    total = _ZIP_END + sum(sizer.entry(rel, c) for rel, c in built.files.items())
    if total <= limit:
        return [vehicles], built
    _say(notify, "info", f"one zip would be about {total / MIB:.0f} MiB; sizing each vehicle to split it …")
    entries = {}
    for v in vehicles:
        with _quiet("tesla_model_extractor.manifest"):  # only measuring; the real build below logs
            res = PackBuilder(cat).build([v], wheel_filter)
        entries[v.id] = {rel: sizer.entry(rel, c) for rel, c in res.files.items()}
        _say(notify, "info", f"  {v.name}: about {sum(entries[v.id].values()) / MIB:.0f} MiB with its wheels")
    by_id = {v.id: v for v in vehicles}
    out = [[by_id[i] for i in group] for group in first_fit(entries, limit)]
    _say(notify, "info", f"splitting the {len(vehicles)} vehicles over {len(out)} zips of at most {max_mib:g} MiB")
    return out, None


def build_packs(
    session: Session,
    vehicles: list[VehicleEntry],
    output: Path,
    *,
    wheels: str = "family",
    bundle: bool = False,
    split: bool = False,
    as_dir: bool = False,
    max_mib: float = DEFAULT_MAX_MIB,
    output_is_target: bool = True,
    notify: Notify | None = None,
) -> list[PackOutput]:
    """Every pack for the selection; raises `PackInvalid` at the first pack that fails validation.

    `split` is `bundle` that spreads the vehicles over several zips when one would exceed `max_mib`."""
    assert session.catalog is not None
    wheel_filter = wheel_filter_for_extract(session.catalog, wheels)
    groups: list[list[VehicleEntry]] | None = None
    built: BuildResult | None = None
    if split and not as_dir:
        groups, built = plan_zip_groups(session, vehicles, wheel_filter, max_mib, notify)
    outputs = []
    targets = pack_targets(
        vehicles,
        output,
        bundle=bundle or split,
        as_dir=as_dir,
        app_version=session.app_version,
        output_is_target=output_is_target,
        groups=groups,
    )
    for group, target in targets:
        out = build_pack(
            session,
            group,
            wheel_filter,
            target,
            as_dir=as_dir,
            max_mib=max_mib,
            built=built if len(targets) == 1 else None,
            notify=notify,
        )
        if not out.validation.ok:
            several = len(targets) == 1 and len(group) > 1
            raise PackInvalid(out, "build one pack per vehicle instead of one for all" if several else None)
        outputs.append(out)
    return outputs


# ---------- GLB export ----------


def wheel_filter_for_unreal(cat: Catalog, spec: str) -> list[str] | None:
    """A named wheel also keeps every family's wheels in the build, so `--separate-wheels` still has them."""
    if spec in ("default", "none"):
        return None
    return [w.strip() for w in spec.split(",") if w.strip()] + [w.api_name for w in cat.wheels if w.present]


def wheel_option(spec: str) -> str | None:
    return None if spec == "none" else spec.split(",")[0]


@dataclass
class GlbExport:
    outputs: list[VehicleOutput]
    app_version: str | None
    warnings: list[str] = field(default_factory=list)  # from building the pack the export reads

    def summary(self) -> dict[str, Any]:
        return {
            "app_version": self.app_version,
            "vehicles": [
                {
                    "model": o.model,
                    "folder": str(o.folder),
                    "glb": str(o.glb),
                    "glb_bytes": o.glb_bytes,
                    "animations": o.animations,
                    "wheels": o.wheels,
                    "cables": o.cables,
                    "warnings": o.warnings,
                }
                for o in self.outputs
            ],
        }


def glb_source(session: Session, ids: list[str] | None, wheels: str = "default") -> tuple[BuildResult, list[str]]:
    """The in-memory pack the GLB exporter reads, and the model ids to export from it."""
    if session.pack is not None:
        return session.pack, pack_model_ids(session.pack.manifest, ids)
    assert session.catalog is not None
    if not ids:
        raise ExtractError("no vehicles selected")
    vehicles = find_vehicles(session, ids)
    res = PackBuilder(session.catalog).build(vehicles, wheel_filter_for_unreal(session.catalog, wheels))
    res.manifest["app_version"] = session.app_version
    return res, [v.id for v in vehicles]


def export_glb(
    session: Session,
    ids: list[str] | None,
    opt: ExportOptions,
    output: Path,
    *,
    wheels: str = "default",
    separate_wheels: bool = False,
    cables: bool = False,
    notify: Notify | None = None,
) -> GlbExport:
    res, model_ids = glb_source(session, ids, wheels)
    _say(notify, "info", f"exporting {', '.join(model_ids)} …")
    outputs = export_pack(res, model_ids, opt, output, separate_wheels, cables)
    warnings = [] if session.is_pack else res.warnings
    return GlbExport(outputs, session.app_version, warnings)


def export_dae(
    session: Session,
    ids: list[str] | None,
    opt: ExportOptions,
    output: Path,
    *,
    wheels: str = "default",
    notify: Notify | None = None,
) -> GlbExport:
    res, model_ids = glb_source(session, ids, wheels)
    _say(notify, "info", f"exporting DAE for {', '.join(model_ids)} …")
    outputs = export_dae_pack(res, model_ids, opt, output)
    warnings = [] if session.is_pack else res.warnings
    return GlbExport(outputs, session.app_version, warnings)
