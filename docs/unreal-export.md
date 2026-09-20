# Unreal Engine export

`tesla-view-extract unreal` turns the vehicles of a Tesla app bundle into **self-contained glTF 2.0 binaries** that
Unreal Engine 5 imports directly (Interchange glTF importer, UE 5.1+). Unlike the Home Assistant asset pack, which
leaves the interpretation to the card at runtime, these files already contain everything the engine can express:

* the vehicle mesh hierarchy with the app's real PBR materials (repacked metallic / roughness / occlusion textures,
  normal maps, emission, alpha blending, double-sided flags, UV tiling),
* the closure animations (frunk, trunk with struts, doors, windows, mirrors, charge port, falcon doors, Cybertruck
  tonneau / suspension) as glTF node animations on the pivot nodes that already exist in the mesh,
* the default wheel of the vehicle under each wheel pivot and the standard brake set under the brake pivots,
* the app's hotspot markers as empty nodes,
* one look baked in (standard fascia, LHD, EU plate, 5 seats, the app's fallback paint) with the alternatives removed.

Everything glTF cannot carry (which meshes are light groups, which parts are hidden until a state turns them on,
paint table, environment presets, marker semantics) is written next to the GLB in `unreal.json`.

## Running it

```bash
tesla-view-extract unreal Tesla_4.60.5.apkm --all -o unreal/               # every vehicle, default look
tesla-view-extract unreal Tesla_4.60.5.apkm --models bayberry --paint Quicksilver --variant performance,plate_us
tesla-view-extract unreal tesla-view-pack-bayberry-4.60.0.zip -o unreal/   # from an existing asset pack
tesla-view-extract unreal recovered-dir --models model_s --wheels none --brakes none --keep-all
```

| option | meaning |
|---|---|
| `--models ID,…` / `--all` | which vehicles (ids as shown by `list`) |
| `--paint NAME` | paint from the app's table (`list` prints the names). Default: the app's fallback paint; the Cybertruck keeps its stainless shader values unless a paint is given |
| `--variant V,…` | looks to bake in: `performance`, `rhd`, `plate_eu` (default), `plate_us`, `seats_7`. Parts of the other looks are removed |
| `--keep-all` | keep every part; variants are only listed in the sidecar |
| `--wheels default\|NAME\|none` | wheel merged under the four wheel pivots (`Wheel_LF` … `Wheel_RR`) |
| `--brakes default\|SET\|none` | brake set merged under the brake pivots (`standard`, `performance`, …) |
| `--separate-wheels` | also write every wheel of the vehicle's family to `wheels/<ApiName>.glb` |
| `--cables` | also write the charge cables to `cables/<PORT>.glb` |
| `--yaw DEG` | extra rotation about the up axis applied to the root |
| `--keep-normal-y` | do not flip the green channel of normal maps (see below) |

Output per vehicle:

```
unreal/bayberry/
├── Bayberry.glb          the vehicle (17 MiB for the Model Y)
├── unreal.json           sidecar: hidden/removed nodes, lights, closures, markers, paints, environment, warnings
├── paints.json           the app's paint table (albedo, metallic, roughness per colour name)
├── studio_panorama.png   the app's equirectangular studio used for reflections
├── wheels/*.glb          with --separate-wheels
└── cables/*.glb          with --cables
```

## Coordinates and scale

glTF: metres, Y up, right-handed. All vehicles are exported in the app's shared space: **nose towards −Z**, ground
at Y = 0, wheel pivots at their arches. (The app itself rotates every vehicle except the 2025 Model Y family by 90°
at the scene root; the exporter bakes that in so all cars line up.) Unreal converts to centimetres and Z-up on import.
If your project wants the nose on +X, pass `--yaw 90` (or −90) or tick *Force Front X Axis* in the import dialog.

## Importing in Unreal Engine 5

1. Content Drawer → Import → pick `Bayberry.glb`. The Interchange import dialog opens.
2. **Static meshes + Level Sequence** (default): keep *Import Animations* ticked. You get one Static Mesh per part,
   a Blueprint / actor hierarchy with the pivots, and a Level Sequence per glTF animation
   (`HoodAnimation`, `TrunkAnimation`, `LFDoorAnimation`, …).
3. **Skeletal mesh + Animation Sequences** (recommended for gameplay): in the dialog set
   *Common Meshes → Force All Mesh As Type → Skeletal Mesh*. Interchange turns the node hierarchy into a skeleton with
   one bone per node (`Hood_Spatial`, `Door_LF_Spatial`, `Wheel_LF`, …), the whole car into one Skeletal Mesh and every
   glTF animation into an Animation Sequence. Wheel pivots and markers become bones you can use as sockets.
4. Materials arrive as Material Instances of the engine's glTF material functions. Check the list
   `additive_materials` in `unreal.json` (the light lenses): switch those instances to *Blend Mode: Additive*.
   The car paint uses `KHR_materials_clearcoat`; UE maps it to the clear-coat shading model.
5. Hide the components / bones listed under `hidden_nodes` at BeginPlay (lights off, badge, defrost overlays) and
   turn them on from state: `lights.<channel>.on` lists the nodes to show for DRL, headlights, parking, brake,
   turn signals, reverse and fog; `lights.brake.off` the ones to hide meanwhile. `lights_eu` replaces `lights`
   for cars with EU rear lights.
6. Closures: time 0 is closed, the end of the clip is open (1.25 s in the app). Play forward to open and backwards
   to close, or scrub. `closures` in `unreal.json` maps `frunk`, `trunk`, `door_fl` … `window_rr`, `charge_port` to
   the animation names. Wheel steering is a yaw of `Wheel_LF_Spatial` / `Wheel_RF_Spatial`.
7. Ground planes: `ground_nodes` lists the app's contact-shadow / light-projection quads (`GroundPlane`, `Floor`).
   Hide or delete them when your level has real lighting.

### What to check after import

* 11 (Model Y) to 21 (Model X) animations on the asset, each moving the expected pivot.
* `Wheel_LF` … `Wheel_RR` and `Brake_LF` … `Brake_RR` sit in the arches; the wheels come with their tyre material.
* Materials: paint on `Static_Exterior` / doors / hood, tinted glass, black textile interior, chrome badge.
* The EU plate is present and the US plate is gone (default variant).

## Material translation notes

| Godot | glTF |
|---|---|
| `albedo_color` (gamma space, the app renders in GLES2 gamma) | `baseColorFactor` converted sRGB → linear, so the on-screen colour matches |
| `metallic_texture` / `roughness_texture` with `*_texture_channel` | one `metallicRoughnessTexture`, G = roughness, B = metallic (repacked with Pillow) |
| `ao_texture` (+ `ao_on_uv2`) | `occlusionTexture` in R, `texCoord` 1 when on UV2 |
| `normal_texture` (Y down) | `normalTexture` with the green channel inverted (glTF is Y up); `--keep-normal-y` disables this |
| `emission` + `emission_texture` × `emission_energy` | `emissiveFactor` / `emissiveTexture` + `KHR_materials_emissive_strength` |
| `flags_transparent` / alpha < 1 | `alphaMode: BLEND` |
| `params_blend_mode = add` | `BLEND` + `extras.godot_blend_mode: "add"`, listed in `additive_materials` |
| `params_cull_mode = disabled` | `doubleSided` |
| `flags_unshaded` | `KHR_materials_unlit` |
| `uv1_scale` / `uv1_offset` | `KHR_texture_transform` |
| car paint shader (`opaque_skybox`, `paint_mix`) | PBR with the paint table's albedo / metallic / roughness, `CarPaint_AO` occlusion, `KHR_materials_clearcoat` |
| glass shader (`glass_skybox`) | PBR, colour + alpha from the shader, `BLEND` |
| badge, beam glow, power flow, plate, defrost shaders | approximations (metallic chrome, unlit blend); listed in `warnings` |

The paint values are the app's: deliberately dark, because the app's look comes from the studio panorama's
reflections. In a lit Unreal scene you will probably want a brighter base colour; `paints.json` has the full table
to build a Material Parameter Collection or a data table from.

## FBX

Unreal reads the GLB directly. If you need FBX (older engine versions, other tools), convert with Blender 3.6+:

```bash
blender --background --python scripts/glb_to_fbx.py -- unreal/bayberry/Bayberry.glb unreal/bayberry/Bayberry.fbx
```

The script imports the GLB, keeps the node hierarchy and bakes the animations into the FBX.

## Limitations

* Node visibility is not part of glTF: hidden parts are exported visible and flagged (`extras.visible = false`,
  `hidden_nodes`). Parts of non-selected variants are removed instead (unless `--keep-all`).
* Animation tracks that are not transforms (`method`, `bezier`, `visible`, material colour changes on the Model X
  sensors) are skipped with a warning.
* The number-plate text (rendered by the app through a viewport shader) is not reproduced; the plate gets the
  plate texture and its background colour.
* The recovered GLBs are GDRE's conversion of Godot's imported meshes (positions, normals, tangents, two UV sets,
  16-bit indices), not Tesla's source art.

The exported files contain Tesla-owned material. Use them for your own projects and do not redistribute them.
