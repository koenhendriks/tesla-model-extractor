# Rules files

The scene converter finds *what* is in a vehicle scene; the rules say what it *means* for the card. Rules live in
`src/tesla_view_extractor/rules/`: `_default.yaml` applies to every scene, `<codename>.yaml` is deep-merged on top
for one vehicle. Pass `--rules DIR` to add or override files without changing the package.

## Bindings are the key

Every vehicle scene root exports NodePath properties (from `mobile/scripts/Vehicles/Vehicle.gd` and the model's own
script): `drl_path`, `headlights_path`, `brake_lights_center_path`, `lf_door_path`, `plate_eu_path`,
`interior_rhd_path`, `lf_wheel_path`, `fascia_perf_path`, … The extractor records each binding's *leaf node name*.
Rules refer to bindings, never to raw node names, so the same rule works across scenes whose node names differ
(`DRL` on the Premium, `DRL_Left`/`DRL_Right` on the Standard). Bindings a scene does not have are skipped.

`tesla-view-extract inspect <bundle> <codename>` prints the bindings, animation players / clips, markers and node
names of a scene – everything you need to write its rules.

## `_default.yaml` sections

| key | meaning |
|---|---|
| `lights.<channel>.on` / `.off` | bindings whose nodes are visible while the light is on / off; `on_nodes` / `off_nodes` are literal node names for parts without a binding |
| `lights_eu` | channel overrides used with an EU plate; emitted only when the scene has `*_global_path` bindings |
| `variants.<name>` | `requires` (bindings that must exist), `show`, `hide` (bindings), `show_nodes`, `hide_nodes` |
| `defaults` | `hide_bindings`, `hide_nodes`, `show_bindings`, `show_nodes` applied right after load |
| `closures.<channel>` | ordered regexes matched against the AnimationPlayer node name, then the clip name |
| `pivots` | wheel / brake / charge port / front / rear bindings, in card order LF, RF, RL, RR |
| `markers.aliases` | `marker_name` → card marker id (`chargePort` → `charge_port`) |
| `brakes.sets` | root property prefix → brake set name; `brakes.default` |
| `exclude_paths` | substrings of files never packed (seasonal wraps) |

## `<codename>.yaml`

```yaml
id: bayberry                     # model id in the manifest, ^[a-z0-9_]+$ (default: slug of the codename)
name: "Model Y (2025+) Premium / Performance"
codename: Bayberry               # folder under Ego/
scene: Ego/Bayberry/Bayberry.tscn
aliases: [juniper]               # other ids that should resolve to this model
wheel_family: Ego/Wheels_Bayberry   # default: folder whose name contains the codename, else the model_key's default wheel family
default_wheel: Crossflow19       # API name (enum without underscores); default: DefaultWheelForVehicleType
lights:                          # merged with the defaults – add nodes that have no binding
  drl: { on_nodes: [Headlights_Beam] }
closures:
  charge_port: ['^ChargeCapAnimation$']   # replaces the default list for this channel
```

Unknown codenames get `_default.yaml` only: the pack is still usable, and anything the rules could not resolve is
listed in the model's `warnings`.

## Checklist for a new vehicle

1. `inspect` the scene; note which light / door / plate bindings exist and how the AnimationPlayers are named.
2. Write the yaml with `id`, `name`, `codename`, `scene`, `wheel_family`, `default_wheel`.
3. Extract with `--models <id>` and read the summary line: closures / lights / variants counts and warnings.
4. Load the pack in the card (Tesla View dev harness: `npm run dev:assets pack.zip`) and check closures, lights,
   hotspot anchors and wheels visually.
5. Add a synthetic test if the scene needs new converter behaviour (never commit app files).
