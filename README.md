# tesla-view-extractor

Build **asset packs** for the [Tesla View](https://github.com/koenhendriks/tesla-view) Home Assistant card from a
copy of the Tesla mobile app that you own.

Tesla View renders your car in 3D exactly like the Tesla app does. The app ships those 3D models, textures,
animations and the lighting setup inside its Android bundle. This tool unpacks the bundle, recovers the embedded
Godot project with [GDRE Tools](https://github.com/GDRETools/gdsdecomp), converts what the card needs into a
renderer-agnostic format and zips it into an asset pack you upload in Home Assistant.

> **You must own the app bundle.** This tool never downloads the Tesla app. The resulting pack contains Tesla-owned
> material; use it for your own car and do not redistribute it. See [LICENSE](LICENSE) for the trademark note.

## Quick start

1. Get the Android bundle of the Tesla app (`Tesla_<version>.apks` / `.xapk` / `.apk`) from a device you own, e.g.
   with an APK exporter app or `adb`. Version 4.60 or newer contains the models the card supports today.
2. Run the extractor (pick one):

   **Docker** (nothing to install, GDRE Tools bundled):
   ```bash
   docker run --rm -v "$PWD":/work ghcr.io/koenhendriks/tesla-view-extractor /work/Tesla_4.60.0.apks --models bayberry -o /work/packs
   ```

   **Python** (3.11+; GDRE Tools is downloaded once, checksum-verified, into `~/.cache/tesla-view-extractor/`):
   ```bash
   pip install tesla-view-extractor          # or: pipx install tesla-view-extractor
   tesla-view-extract Tesla_4.60.0.apks       # interactive: lists the vehicles and asks which one(s)
   ```

3. Upload the zip in Home Assistant: **Settings → Devices & services → Tesla View → Configure → Upload asset pack**
   (or during the initial setup, or from the *Repairs* item the integration raises while no pack is installed).

The first run takes a few minutes: GDRE recovers the whole 450 MB Godot project. Pass `--keep-recovered DIR` to reuse
it for later runs (`--recovered DIR` skips straight to conversion).

## What you get

One zip per selected vehicle (8–25 MiB each; Home Assistant accepts uploads up to 100 MiB). A pack contains the
vehicle's GLB, textures, per-surface material overrides, closure animations (frunk, trunk, doors, windows, charge
port), the wheels of its family, brake calipers, charge cables, the paint table, the studio panorama and a
`manifest.json` describing all of it. The format is documented in [docs/asset-pack-format.md](docs/asset-pack-format.md).

```
$ tesla-view-extract list Tesla_4.60.0.apks
 id            name                                   codename     API model / fascia                          wheels           raw MB
 bayberry      Model Y (2025+) Premium / Performance  Bayberry     modely (baseBayberry, performanceBayberry)  6 (Crossflow19)  23.6
 bayberry_e41  Model Y (2025+) Standard               BayberryE41  modely (e41Bayberry)                        6 (E4118)        32.8
 bayberry_e80  Model Y L (long wheelbase)             BayberryE80  modely                                      6 (MachinaV219)  25.3
 y_high        Model Y (2020–2024)                    Y_High       modely                                      15 (Gemini)      19.7
 poppyseed     Model 3 (2024+ Highland)               Poppyseed    model3 (basePoppyseed, …)                   4 (Wishbone20)   20.7
 …
```

The card currently renders GLB-based vehicles fully (the Model Y family, Model 3 Highland, …). Packs for the other
vehicles are produced on a best-effort basis: unknown scenes get generic rules, and features the card cannot map yet
are listed under `warnings` in the manifest.

## Usage

```
tesla-view-extract <bundle|recovered-dir> [-o OUT] [--models ID[,ID]] [--all] [--wheels family|all|NAME,…]
                   [--bundle] [--dir] [--max-size MIB] [--keep-recovered DIR | --recovered DIR]
                   [--gdre PATH] [--no-download] [--rules DIR] [--yes] [--json]
tesla-view-extract list     <bundle|recovered-dir>        # vehicles, wheels, paints in the bundle
tesla-view-extract inspect  <bundle|recovered-dir> <id>   # bindings / animation players / markers of one scene
tesla-view-extract validate <pack.zip|dir>                # schema, referenced files, node names, size
```

| option | meaning |
|---|---|
| `--models bayberry,bayberry_e41` | which vehicles (ids, codenames or aliases such as `juniper`). Default: interactive, or the first Model Y with `--yes` |
| `--all` | every vehicle in the bundle, one pack each |
| `--wheels family` | (default) all wheel scenes of the vehicle's wheel family; `all` = every wheel; or a list of API names (`Crossflow19,HelixV220`) |
| `--bundle` | one zip with all selected models – fails validation when it exceeds `--max-size` (100 MiB, the HA upload limit) |
| `--dir` | write an unzipped pack directory (for development against the card) |
| `--keep-recovered DIR` / `--recovered DIR` | keep / reuse the GDRE recovery (≈ 450 MB) |
| `--gdre PATH`, `--no-download` | use your own GDRE Tools binary; never download it |
| `--rules DIR` | extra `<codename>.yaml` rules (see [docs/rules.md](docs/rules.md)) |

Exit codes: 0 ok · 1 error · 2 a selection is required (no TTY) · 3 the produced pack failed validation.

## How it works

1. **Unpack** – the inner APK that contains `assets/godot/project.binary` is located by content and only
   `assets/godot/**` is extracted.
2. **Recover** – `gdre_tools --headless --recover` turns the exported Godot 3.2 project back into `.tscn`/`.tres`/
   `.gd` text, `.glb` models and `.png` textures. Binary `.material` files are converted on demand with `--bin-to-txt`.
3. **Convert** – each vehicle `.tscn` becomes an `*.overrides.json` (node transforms/visibility, per-surface material
   descriptions, the same shape the card's `MaterialDesc` uses); `Animation` resources become JSON keyframes
   (including the inline trunk clip with its strut tracks, and the older per-property translation/rotation tracks);
   GDScript tables are scraped for paints, wheel enums, charge cables, the vehicle routing table and the
   environment/camera presets.
4. **Rules** – `rules/_default.yaml` maps the scene root's exported NodePath *bindings* (`drl_path`, `lf_door_path`,
   `fascia_perf_path`, …) to card channels: light groups, variants (performance / RHD / plates / 7 seats), closures,
   pivots, markers. `rules/<codename>.yaml` adds names, aliases and per-model tweaks. Unknown scenes still produce a
   usable pack from the defaults.
5. **Pack** – only referenced files are copied (seasonal wrap skins excluded), written as a deterministic zip and
   validated.

## Development

```bash
uv venv && uv pip install -e ".[dev]"      # or python -m venv .venv && pip install -e ".[dev]"
pytest -q                                    # synthetic fixtures only – no Tesla content in this repo
ruff check src tests && mypy
python scripts/check_no_assets.py            # CI guard against accidentally committed app material
tesla-view-extract compare-legacy pack.zip <old assets dir>   # regression check against a known-good output
```

Adding support for a vehicle: run `tesla-view-extract inspect <bundle> <codename>` to see its bindings, animation
players and markers, then write `rules/<codename>.yaml` (see [docs/rules.md](docs/rules.md)).

## License

MIT for the code in this repository. Tesla, Model 3, Model Y, Model S, Model X, Cybertruck are trademarks of Tesla,
Inc.; the extracted material is theirs. This project is not affiliated with or endorsed by Tesla.
