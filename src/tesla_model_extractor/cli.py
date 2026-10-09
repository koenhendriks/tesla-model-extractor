"""tesla-model-extract: command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import GDRE_VERSION, __version__
from .catalog import Catalog, resolve_wheel_family
from .legacy import compare_legacy
from .report import console, print_warnings, vehicle_table
from .service import (
    ExtractError,
    Session,
    build_pack,
    export_dae,
    export_glb,
    open_source,
    pack_targets,
    plan_zip_groups,
    wheel_filter_for_extract,
    wheel_option,
)
from .unreal.daewriter import summarize_dae_warnings
from .unreal.materials import DEFAULT_PAINT_BRIGHTNESS
from .unreal.scene import DEFAULT_VARIANTS, ExportOptions
from .validate import DEFAULT_MAX_MIB, validate_pack

EXIT_OK, EXIT_ERROR, EXIT_SELECT, EXIT_INVALID = 0, 1, 2, 3


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tesla-model-extract",
        description=(
            "Extract the Tesla app's 3D vehicle models from a bundle you own (.apks/.apkm/.xapk/.apk) or a "
            "GDRE-recovered project directory: `unreal` writes self-contained GLB files for Unreal / Blender / any glTF "
            "importer, `extract` builds asset packs for the Tesla View Home Assistant card."
        ),
        epilog="Never downloads the Tesla app. Downloads only the pinned, checksum-verified GDRE Tools release when no local copy is found.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__} (GDRE Tools {GDRE_VERSION})")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "source",
            help="Tesla_x.y.z.apks / .apkm / .xapk / .apk, an extracted assets/godot dir, or a recovered project dir",
        )
        sp.add_argument(
            "--recovered", metavar="DIR", help="use this GDRE-recovered project directory (skips unpack + recovery)"
        )
        sp.add_argument(
            "--keep-recovered", metavar="DIR", help="store the recovered project here so later runs can reuse it"
        )
        sp.add_argument(
            "--gdre", metavar="PATH", help="GDRE Tools binary (default: $GDRE_TOOLS, PATH, cache, download)"
        )
        sp.add_argument("--no-download", action="store_true", help="never download GDRE Tools")
        sp.add_argument("--rules", metavar="DIR", help="extra directory with <codename>.yaml rules files")

    ex = sub.add_parser("extract", help="build Tesla View (Home Assistant) asset pack(s) (default command)")
    common(ex)
    ex.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="output zip (single pack) or directory (per-model packs); default: ./packs/",
    )
    ex.add_argument("--models", metavar="ID[,ID…]", help="vehicle ids / codenames / aliases to include (see `list`)")
    ex.add_argument("--all", action="store_true", help="every vehicle in the bundle")
    ex.add_argument(
        "--wheels",
        metavar="all|family|NAME[,NAME…]",
        default="family",
        help="wheels to include: the model's family (default), all wheel scenes, or a list of API names",
    )
    ex.add_argument(
        "--bundle", action="store_true", help="one zip with all selected models (default: one zip per model)"
    )
    ex.add_argument(
        "--split",
        action="store_true",
        help="like --bundle, but spread the models over as few zips as needed to keep each under --max-size",
    )
    ex.add_argument("--dir", action="store_true", help="write an unzipped pack directory instead of a zip")
    ex.add_argument(
        "--max-size",
        type=float,
        default=DEFAULT_MAX_MIB,
        metavar="MIB",
        help=f"fail if a zip exceeds this (default {DEFAULT_MAX_MIB})",
    )
    ex.add_argument("--yes", "-y", action="store_true", help="no interactive selection; default to the first Model Y")
    ex.add_argument("--json", action="store_true", help="print a machine-readable summary to stdout")

    ls = sub.add_parser("list", help="show the vehicles / wheels the bundle contains")
    common(ls)
    ls.add_argument("--json", action="store_true")

    ins = sub.add_parser(
        "inspect",
        help="dump what the converter sees for one vehicle (bindings, players, markers) – rules authoring aid",
    )
    common(ins)
    ins.add_argument("vehicle", help="vehicle id or codename")

    un = sub.add_parser(
        "unreal",
        help="export self-contained GLB files (materials, wheels, brakes, closure animations) for Unreal Engine",
    )
    common(un)
    un.add_argument("-o", "--output", metavar="DIR", default="unreal", help="output directory (default: ./unreal/)")
    un.add_argument("--models", metavar="ID[,ID…]", help="vehicle ids / codenames / aliases (see `list`)")
    un.add_argument("--all", action="store_true", help="every vehicle in the bundle")
    un.add_argument(
        "--wheels",
        metavar="default|NAME|none",
        default="default",
        help="wheel attached under the pivots: the model's default (default), an API wheel name, or none",
    )
    un.add_argument(
        "--brakes", metavar="default|SET|none", default="default", help="brake set (standard / performance …)"
    )
    un.add_argument(
        "--paint", metavar="NAME", help="paint from the app's table (see `list`); default: the app's fallback"
    )
    un.add_argument(
        "--variant",
        metavar="V[,V…]",
        default=",".join(sorted(DEFAULT_VARIANTS)),
        help="looks to bake in: performance, rhd, plate_eu, plate_us, seats_7 (default: plate_eu)",
    )
    un.add_argument("--keep-all", action="store_true", help="keep every part (variants are only flagged, not removed)")
    un.add_argument("--yaw", type=float, default=0.0, metavar="DEG", help="extra rotation about the up axis")
    un.add_argument(
        "--separate-wheels", action="store_true", help="also write every wheel of the family as its own GLB"
    )
    un.add_argument("--cables", action="store_true", help="also write the charge cables as GLBs")
    un.add_argument("--keep-normal-y", action="store_true", help="do not flip the green channel of normal maps")
    un.add_argument(
        "--paint-brightness",
        type=float,
        default=DEFAULT_PAINT_BRIGHTNESS,
        metavar="FACTOR",
        help=f"brighten the app's (very dark) paint colours for a normally lit scene; 1 = the raw app values "
        f"(default {DEFAULT_PAINT_BRIGHTNESS:g})",
    )
    un.add_argument("--yes", "-y", action="store_true", help="no interactive selection; default to the first Model Y")
    un.add_argument("--json", action="store_true", help="print a machine-readable summary to stdout")

    da = sub.add_parser(
        "dae",
        help="export COLLADA (.dae) + textures (same options as `obj`, better material fidelity for Sweet Home 3D)",
    )
    common(da)
    da.add_argument("-o", "--output", metavar="DIR", default="dae", help="output directory (default: ./dae/)")
    da.add_argument("--models", metavar="ID[,ID…]")
    da.add_argument("--all", action="store_true")
    da.add_argument("--wheels", metavar="default|NAME|none", default="default")
    da.add_argument("--brakes", metavar="default|SET|none", default="default")
    da.add_argument("--paint", metavar="NAME")
    da.add_argument("--variant", metavar="V[,V…]", default=",".join(sorted(DEFAULT_VARIANTS)))
    da.add_argument("--keep-all", action="store_true")
    da.add_argument("--yaw", type=float, default=0.0, metavar="DEG")
    da.add_argument("--keep-normal-y", action="store_true")
    da.add_argument("--paint-brightness", type=float, default=DEFAULT_PAINT_BRIGHTNESS, metavar="FACTOR")
    da.add_argument("--exclude", metavar="PATTERN[,PATTERN…]", default="")
    da.add_argument("--yes", "-y", action="store_true")
    da.add_argument("--json", action="store_true")
    da.add_argument("--quiet-warnings", action="store_true")

    va = sub.add_parser("validate", help="validate a pack zip or directory")
    va.add_argument("pack")
    va.add_argument("--max-size", type=float, default=DEFAULT_MAX_MIB, metavar="MIB")

    cl = sub.add_parser("compare-legacy", help="compare a pack with the pre-pack Tesla View asset folder (dev aid)")
    cl.add_argument("pack")
    cl.add_argument("legacy_dir")

    sub.add_parser("gui", help="open the desktop app (needs the `gui` extra: pip install 'tesla-model-extractor[gui]')")
    return p


def _notify(level: str, msg: str) -> None:
    if level == "warning":
        head, _, rest = msg.partition(": ")
        console.print(f"[yellow]{head}:[/] {rest}" if rest else f"[yellow]{msg}[/]")
    else:
        console.print(f"[dim]{msg}[/]")


def _session(args: argparse.Namespace, accept_pack: bool = False) -> Session:
    """Resolve the source to a recovered project + catalog (running GDRE if needed); exits on failure."""
    try:
        session = open_source(
            args.source,
            recovered=args.recovered,
            keep_recovered=args.keep_recovered,
            gdre_path=args.gdre,
            allow_download=not args.no_download,
            rules_dir=args.rules,
            accept_pack=accept_pack,
            notify=_notify,
        )
    except ExtractError as e:
        console.print(f"[red]{e}[/]")
        sys.exit(EXIT_ERROR)
    if session.kind in ("bundle", "apk", "godot_root") and not args.recovered and not args.keep_recovered:
        console.print("[dim]tip: pass --keep-recovered DIR to reuse the recovery next time[/]")
    return session


def _select(cat: Catalog, args: argparse.Namespace) -> list:
    present = [v for v in cat.vehicles if v.present]
    if getattr(args, "all", False):
        return present
    if args.models:
        out = []
        for key in args.models.split(","):
            v = cat.vehicle(key.strip())
            if v is None or not v.present:
                console.print(f"[red]unknown or missing vehicle {key!r}[/]; run `list` to see what the bundle contains")
                sys.exit(EXIT_ERROR)
            out.append(v)
        return out
    console.print(vehicle_table(cat))
    default = next((v for v in present if v.model_keys == ["modely"]), present[0] if present else None)
    if default is None:
        console.print("[red]no vehicle scenes found[/]")
        sys.exit(EXIT_ERROR)
    if args.yes or not sys.stdin.isatty():
        if not args.yes:
            console.print("[red]no --models given and not a terminal; pick with --models ID[,ID] or --all (exit 2)[/]")
            sys.exit(EXIT_SELECT)
        return [default]
    answer = console.input(f"Which vehicle(s)? ids separated by commas [default: {default.id}]: ").strip()
    if not answer:
        return [default]
    return [_must(cat, k.strip()) for k in answer.split(",")]


def _must(cat: Catalog, key: str):
    v = cat.vehicle(key)
    if v is None or not v.present:
        console.print(f"[red]unknown vehicle {key!r}[/]")
        sys.exit(EXIT_ERROR)
    return v


def cmd_extract(args: argparse.Namespace) -> int:
    session = _session(args)
    cat = session.catalog
    assert cat is not None
    print_warnings(cat.warnings, "catalog warnings")
    vehicles = _select(cat, args)
    wheel_filter = wheel_filter_for_extract(cat, args.wheels)
    groups, built = None, None
    if args.split and not args.dir:
        groups, built = plan_zip_groups(session, vehicles, wheel_filter, args.max_size, notify=_notify)
    targets = pack_targets(
        vehicles,
        Path(args.output) if args.output else None,
        bundle=args.bundle or args.split,
        as_dir=args.dir,
        app_version=session.app_version,
        groups=groups,
    )
    results = []
    for group, target in targets:
        out = build_pack(
            session,
            group,
            wheel_filter,
            target,
            as_dir=args.dir,
            max_mib=args.max_size,
            built=built if len(targets) == 1 else None,
        )
        ids = "-".join(out.models)
        for mid, text in out.summaries.items():
            console.print(f"[bold]{mid}[/]: {text}")
        print_warnings(out.warnings, f"{ids} warnings")
        if out.missing:
            print_warnings(out.missing, f"{ids}: referenced files not found in the recovered project")
        st = out.stats
        if args.dir:
            console.print(f"[green]wrote[/] {target}  ({st.files} files, {st.raw_mib:.1f} MiB)")
        else:
            console.print(
                f"[green]wrote[/] {target}  ({st.files} files, {st.raw_mib:.1f} MiB raw → {st.zip_mib:.1f} MiB zip)"
            )
        print_warnings(out.validation.warnings, "validation warnings")
        if not out.validation.ok:
            for e in out.validation.errors:
                console.print(f"[red]✗ {e}[/]")
            if len(targets) == 1 and len(group) > 1:
                console.print("[red]hint: drop --bundle to get one pack per model[/]")
            return EXIT_INVALID
        results.append(out.summary())
    if args.json:
        print(json.dumps({"app_version": session.app_version, "packs": results}, indent=1))
    console.print(
        "[green]done.[/] Upload the zip in Home Assistant: Settings → Devices & services → Tesla View → Configure → Upload asset pack"
    )
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    session = _session(args)
    cat, app_version = session.catalog, session.app_version
    assert cat is not None
    if args.json:
        print(
            json.dumps(
                {
                    "app_version": app_version,
                    "vehicles": [
                        {
                            "id": v.id,
                            "name": v.name,
                            "codename": v.codename,
                            "present": v.present,
                            "scene": v.scene,
                            "api_match": v.api_match,
                            "wheel_family": resolve_wheel_family(cat, v) if v.present else None,
                            "raw_bytes": v.size_bytes,
                        }
                        for v in cat.vehicles
                    ],
                    "wheels": [
                        {"api_name": w.api_name, "enum": w.enum_name, "family": w.family, "present": w.present}
                        for w in cat.wheels
                    ],
                    "paints": sorted(cat.options.paints),
                    "warnings": cat.warnings,
                },
                indent=1,
            )
        )
    else:
        console.print(vehicle_table(cat))
        console.print(
            f"{len([w for w in cat.wheels if w.present])} wheel scenes, {len(cat.options.paints)} paints, "
            f"cables: {', '.join(cat.options.cable_map)}; app version {app_version or 'unknown'}"
        )
        print_warnings(cat.warnings)
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    session = _session(args)
    cat = session.catalog
    assert cat is not None
    v = _must(cat, args.vehicle)
    from .convert.scene import convert_scene

    res = convert_scene(cat.root, v.scene)
    f = res.facts
    out = {
        "scene": v.scene,
        "root": {"name": f.root_name, "type": f.root_type, "instance": f.root_instance, "script": f.root_script},
        "bindings": {k: {"path": p, "leaf": leaf} for k, (p, leaf) in f.bindings.items()},
        "players": [
            {"player": p.player, "clip": p.clip, "source": p.ext_rel or f"sub_resource#{p.sub_id}"} for p in f.players
        ],
        "markers": [m.__dict__ for m in f.markers],
        "packed_scenes": f.packed_scene_props,
        "inline_meshes": f.inline_meshes,
        "nodes": sorted(f.node_names),
        "material_kinds": sorted({m["kind"] for m in res.overrides["materials"].values()}),
        "rules_id": v.id,
        "warnings": f.warnings,
    }
    print(json.dumps(out, indent=1))
    return EXIT_OK


def export_options(args: argparse.Namespace) -> ExportOptions:
    return ExportOptions(
        variants=frozenset(v.strip() for v in args.variant.split(",") if v.strip()),
        keep_all=args.keep_all,
        wheel=wheel_option(args.wheels),
        brakes=None if args.brakes == "none" else args.brakes,
        paint=args.paint,
        yaw_deg=args.yaw,
        flip_normal_green=not args.keep_normal_y,
        paint_brightness=args.paint_brightness,
        exclude=frozenset(p.strip() for p in getattr(args, "exclude", "").split(",") if p.strip()),
    )


def cmd_unreal(args: argparse.Namespace) -> int:
    session = _session(args, accept_pack=True)
    ids: list[str] | None
    if session.is_pack:
        ids = args.models.split(",") if args.models else None
    else:
        assert session.catalog is not None
        print_warnings(session.catalog.warnings, "catalog warnings")
        ids = [v.id for v in _select(session.catalog, args)]
    try:
        result = export_glb(
            session,
            ids,
            export_options(args),
            Path(args.output),
            wheels=args.wheels,
            separate_wheels=args.separate_wheels,
            cables=args.cables,
        )
    except ExtractError as e:
        console.print(f"[red]{e}[/]")
        return EXIT_ERROR
    print_warnings(result.warnings, "pack warnings")
    for o in result.outputs:
        console.print(
            f"[green]wrote[/] {o.glb}  ({o.glb_bytes / 1048576:.1f} MiB, {o.animations} animations"
            + (f", {len(o.wheels)} wheel GLBs" if o.wheels else "")
            + (f", {len(o.cables)} cables" if o.cables else "")
            + ")"
        )
        print_warnings(o.warnings, f"{o.model} warnings")
    if args.json:
        print(json.dumps(result.summary(), indent=1))
    console.print("[green]done.[/] Import guide: docs/unreal-export.md (each folder has an unreal.json sidecar)")
    return EXIT_OK


def cmd_dae(args: argparse.Namespace) -> int:
    session = _session(args, accept_pack=True)
    if session.is_pack:
        ids = args.models.split(",") if args.models else None
    else:
        assert session.catalog is not None
        print_warnings(session.catalog.warnings, "catalog warnings")
        ids = [v.id for v in _select(session.catalog, args)]
    try:
        result = export_dae(session, ids, export_options(args), Path(args.output), wheels=args.wheels)
    except ExtractError as e:
        console.print(f"[red]{e}[/]")
        return EXIT_ERROR
    pack_warnings = summarize_dae_warnings(result.warnings) if args.quiet_warnings else result.warnings
    print_warnings(pack_warnings, "pack warnings")
    for o in result.outputs:
        console.print(f"[green]wrote[/] {o.glb}  ({o.glb_bytes / 1024:.1f} KiB)")
        vehicle_warnings = summarize_dae_warnings(o.warnings) if args.quiet_warnings else o.warnings
        print_warnings(vehicle_warnings, f"{o.model} warnings")
    if args.json:
        print(json.dumps(result.summary(), indent=1))
    console.print("[green]done.[/] Each folder has <Codename>.dae + textures/ + dae.json (notes/warnings)")
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    rep = validate_pack(Path(args.pack), args.max_size)
    console.print(
        f"{args.pack}: {rep.files} files, {rep.raw_bytes / 1048576:.1f} MiB raw"
        + (f", {rep.zip_bytes / 1048576:.1f} MiB zip" if rep.zip_bytes else "")
    )
    for w in rep.warnings:
        console.print(f"  [yellow]warning[/] {w}")
    for e in rep.errors:
        console.print(f"  [red]error[/] {e}")
    console.print("[green]valid[/]" if rep.ok else "[red]invalid[/]")
    return EXIT_OK if rep.ok else EXIT_INVALID


def cmd_compare(args: argparse.Namespace) -> int:
    cmp = compare_legacy(Path(args.pack), Path(args.legacy_dir))
    console.print(
        f"identical: {len(cmp.identical)}   json-equal: {len(cmp.json_equal)}   different: {len(cmp.different)}   "
        f"pack-only: {len(cmp.pack_only)}   legacy-only: {len(cmp.legacy_only)}"
    )
    for rel, diffs in cmp.different.items():
        console.print(f"[yellow]{rel}[/]")
        for d in diffs[:10]:
            console.print(f"    {d}")
    for rel in cmp.pack_only:
        console.print(f"[dim]pack only: {rel}[/]")
    for rel in cmp.legacy_only:
        console.print(f"[dim]legacy only: {rel}[/]")
    return EXIT_OK if not cmp.different else EXIT_INVALID


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in (
        "extract",
        "list",
        "inspect",
        "unreal",
        "dae",
        "validate",
        "compare-legacy",
        "gui",
        "-h",
        "--help",
        "--version",
    ):
        argv.insert(0, "extract")
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if args.cmd is None:
        _parser().print_help()
        return EXIT_ERROR
    if args.cmd == "gui":
        from .gui import main as gui_main

        return gui_main()
    return {
        "extract": cmd_extract,
        "list": cmd_list,
        "inspect": cmd_inspect,
        "unreal": cmd_unreal,
        "dae": cmd_dae,
        "validate": cmd_validate,
        "compare-legacy": cmd_compare,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
