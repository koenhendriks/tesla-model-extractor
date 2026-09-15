# Tesla View asset pack – format 1

A pack is a zip archive. `manifest.json` sits at the root; every other file keeps the path it had inside the
recovered Godot project (`res://` stripped), e.g. `Ego/Bayberry/Bayberry.glb`, `Ego/Bayberry/Textures/Body_BC.png`,
`Ego/Wheels_Bayberry/GeminiDark/GeminiDark.glb`, `shared_misc_textures/New_Studio.png`. All path strings inside the
JSON files are pack-relative in the same way.

Generated files use two suffixes:

| file | content |
|---|---|
| `<dir>/<Scene>.overrides.json` | scene overrides for `<dir>/<Scene>.tscn` (see below) |
| `<dir>/animations/<Player>.json` | one closure animation, named after its `AnimationPlayer` node |

Home Assistant limits uploads to 100 MiB per zip; the extractor therefore produces one pack per vehicle by default.
The Tesla View integration unpacks each upload into `<config>/tesla_view/packs/<pack_id>/` and merges the manifests
of all installed packs into an `index.json` for the card.

## manifest.json

```jsonc
{
  "format": 1,
  "generated_by": { "tool": "tesla-view-extractor", "version": "0.1.0", "gdre": "2.6.4" },
  "app_version": "4.60.0-4542",          // from the bundle file name, may be null
  "generated_at": "2026-09-15T12:00:00Z",
  "models":   { "<model id>": Model },   // ^[a-z0-9_]+$
  "wheels":   { "<ApiName>": Wheel },    // e.g. "Crossflow19" (WheelType enum name without underscores)
  "wheel_aliases": { "<API wheel_type string>": "<WheelType enum>" },   // Tesla API → enum, e.g. "Apollo19": "Gemini"
  "cables":   { "<port type>": Cable },  // "CCS", "EU", "US", "GB", …
  "paints":   { "base_roughness": 0.04, "base_metallic": 0.3, "fallback": Paint, "colors": { "<Name>": Paint } },
  "environment": Environment,
  "warnings": [ "…" ]
}
```

### Model

```jsonc
{
  "name": "Model Y (2025+) Premium / Performance",
  "codename": "Bayberry",                 // folder name in the app
  "aliases": ["juniper"],                 // other ids configs may use
  "api_match": { "model_key": "modely", "fascia_type": ["baseBayberry", "performanceBayberry"], "chassis_type": [...] },
  "kind": "glb",                          // "glb": root instances a GLB  |  "obj": MeshInstances with .obj meshes
  "dir": "Ego/Bayberry",
  "glb": "Ego/Bayberry/Bayberry.glb",     // null for kind "obj"
  "overrides": "Ego/Bayberry/Bayberry.overrides.json",
  "root": "ROOT",                         // scene root node name
  "animations": { "HoodAnimation": "Ego/Bayberry/animations/HoodAnimation.json", … },
  "closures":   { "frunk": ["HoodAnimation"], "trunk": ["TrunkAnimation"], "charge_port": ["ChargeportAnimation"],
                  "door_fl": [...], "door_fr": [...], "door_rl": [...], "door_rr": [...],
                  "window_fl": [...], "window_fr": [...], "window_rl": [...], "window_rr": [...] },
  "bindings":   { "drl_path": "DRL", "lf_wheel_path": "Wheel_LF_Spatial", … },   // root export → leaf node name
  "show": ["Fascia_Standard", "LHD", …],  // visibility to apply right after loading
  "hide": ["Tesla_Badge", "Fascia_Perf", "DRL", …],       // includes every light "on" node
  "variants": {                           // optional looks; null when the scene has no such parts
    "performance": { "show": [...], "hide": [...] } | null,
    "rhd":         { "show": [...], "hide": [...] } | null,
    "plate_eu":    { "show": ["Plate_EU"], "hide": ["Plate_US"] } | null,
    "plate_us":    { ... } | null,
    "seats_7":     { ... } | null
  },
  "lights": {                             // channel → nodes visible while on (and while off)
    "drl":        { "on": ["DRL", "Headlights_Beam"] },
    "headlights": { "on": [...] }, "parking": { "on": [...] },
    "brake":      { "on": ["Brake_Lights_On", "BrakeLightProjection"], "off": ["Brake_Lights_Off"] },
    "turn_l": {...}, "turn_r": {...}, "reverse": {...}, "fog": {...}
  },
  "lights_eu": { … } | null,              // full table to use instead of `lights` with an EU plate (rear lights differ)
  "pivots": { "wheels": ["Wheel_LF_Spatial", "Wheel_RF_Spatial", "Wheel_RL_Spatial", "Wheel_RR_Spatial"],
              "brakes": ["Brake_LF_Spatial", …], "charge_port": "ChargePortMarker", "front": "FrontMarker", "rear": "RearMarker" },
  "markers": [                            // hotspot anchors
    { "name": "frunk", "node": "FrunkMarker", "parent": "Spatials", "position": [0, 0.83, -1.76], "kind": "marker" },
    { "name": "lock",  "node": "Lock_Marker", "parent": null, "position": [0, 1.73, 0], "kind": "locator" }, …
  ],                                      // kind "marker" = the app's tap targets (Marker.gd), "locator" = root marker_locators_paths
  "brakes": { "standard": { "front": "Ego/Brakes/Brakes_Std_F.overrides.json", "rear": "…_R.overrides.json" },
              "performance": { … } },
  "brakes_default": "standard",
  "wheel_family": "Ego/Wheels_Bayberry",
  "default_wheel": "Crossflow19",
  "inline_meshes": ["Airflow_left", …],   // nodes whose mesh could not be exported (decorative)
  "excluded": ["/Textures/Skins/"],
  "warnings": []
}
```

Marker names used by the card: `frunk`, `trunk`, `charge_port`, `lock`, `lights` (optional; the card synthesises a
lights anchor from the body bounding box when absent), `door_fl` … `door_rr` (display only).

### Wheel

```jsonc
{ "internal": "Crossflow_19", "family": "Ego/Wheels_Bayberry", "dir": "Ego/Wheels_Bayberry/GeminiDark",
  "scene": "Ego/Wheels_Bayberry/GeminiDark/GeminiDark.tscn",
  "kind": "glb" | "obj" | "unsupported",
  "glb": "Ego/Wheels_Bayberry/GeminiDark/GeminiDark.glb",       // kind glb
  "root": "GeminiDark", "overrides": "…/GeminiDark.overrides.json",
  "root_matrix": null | [16 floats],      // scene root transform (e.g. Arachnid is scaled 0.92)
  "glb_fallback": true,                   // present when the scene used an inline mesh and the imported GLB was used instead
  "reason": "…"                           // kind unsupported
}
```

Wheel scenes are instanced under each of the model's `pivots.wheels` nodes; the pivots already carry the arch
position, the −90° axle rotation and the 1.05 scale from the vehicle `.tscn`.

### Cable

```jsonc
{ "overrides": "mobile/geometry/Charging_Cable/Charging_Cable_CCS2_V3.overrides.json", "flow_node": "Charger_Cable", "root": "Spatial" }
```

`flow_node` is the mesh whose material is the app's `PowerflowSingle` shader (`kind: "power_flow"` in the overrides,
with `params.texture_bc` / `params.texture_mra`); the card renders it with its own port of that shader.

### Paint

```jsonc
{ "albedo": "#2b2d35", "metallic": 0.85, "roughness": 0.2 }
```

Values feed the app's `opaque_skybox` car-paint shader; they are deliberately dark, the studio reflections give the
colour its look.

### Environment

```jsonc
{
  "panorama": "shared_misc_textures/New_Studio.png",   // equirectangular studio used for reflections + ambient
  "renderer": "gles2_gamma",                           // the app renders in GLES2 gamma space: no sRGB decode, no tone mapping
  "presets": { "parked":   { "sky_rot_deg": [0, -7, 83], "env_energy": 4, "amb_energy": 4,
                             "camera": { "pivot_deg": [68.6, -138, 0], "offset": [-0.06, 6.7, 0] } },
               "top_down": { … }, "charging": { … }, "drive": { … }, "climate": { … } },
  "fallback": "parked",
  "fallback_energy": { "env_energy": 3, "amb_energy": 7 },
  "fov": 40,
  "bg": { "dark": "#161718", "light": "#F7F7F7" },
  "flash": { "duration_s": 1.5, "interval_s": 1.0 }
}
```

`camera.pivot_deg` is the Euler rotation (degrees, XYZ) of the camera pivot at the car's origin and `offset` the
camera position inside that pivot (the app places the camera 6.7 m up the pivot's Y axis and rotates the pivot).

## `*.overrides.json`

Produced for every `.tscn` (vehicles, wheels, brakes, cables):

```jsonc
{
  "source": "Ego/Bayberry/Bayberry.tscn",
  "root_props": { … every property on the scene root, simplified … },
  "nodes": {
    "<node name>": { "parent": "Door_LF_Spatial", "type": "Spatial", "instance": "…tscn", "visible": false,
                     "matrix": [16 floats, column-major], "mesh": "….obj", "inline_mesh": true,
                     "materials": { "<surface index>": "<material key>" } }
  },
  "materials": { "<key = file path | sub_resource#N>": MaterialDesc },
  "materials_by_name": { "<file stem | resource_name>": "<material key>" }
}
```

`materials_by_name` lets a renderer re-apply the importer materials that sit next to the scene to GLB surfaces the
`.tscn` does not override (the GLB export flattens blend modes, UV2 emission and unshaded flags).

### MaterialDesc

```jsonc
// SpatialMaterial
{ "kind": "pbr", "name": "Glass", "source": "Ego/Bayberry/Glass_Windows_Dark.tres",
  "albedo": [r, g, b], "alpha": 0.86, "albedo_texture": "…png" | null,
  "metallic": 1.0, "metallic_texture": "…png" | null, "metallic_channel": 0,    // Godot channel: 0 R 1 G 2 B 3 A 4 gray
  "specular": 0.5, "roughness": 0.05, "roughness_texture": "…png" | null, "roughness_channel": 1,
  "ao_texture": "…", "ao_channel": 2, "ao_on_uv2": false, "ao_light_affect": 1.0,        // when ao_enabled
  "normal_texture": "…", "normal_scale": 1.0,                                            // when normal_enabled
  "emission": [r, g, b], "emission_energy": 1.0, "emission_texture": "…", "emission_on_uv2": true, "emission_operator": 0,
  "transparent": true, "unshaded": false, "cull_mode": 0, "blend_mode": 0, "depth_draw_mode": 0, "render_priority": 0 }

// ShaderMaterial
{ "kind": "car_paint" | "tinted_glass" | "chrome_badge" | "beam_glow" | "power_flow" | "shader",
  "shader": "shaders/opaque_skybox.shader", "params": { "<param>": number | [floats] | "…png" }, "render_priority": 0 }
```

`kind` tells the card which of its own material implementations to use; `params` carries the shader uniforms
(colours as `[r,g,b,a]`, textures as paths).

## `animations/<Player>.json`

```jsonc
{ "name": "TrunkAnimation", "length": 1.2,
  "tracks": [ { "nodePath": "Trunk_Spatial", "type": "transform",
                "keys": [ { "time": 0, "position": [x,y,z], "quaternion": [x,y,z,w], "scale": [1,1,1] }, … ] },
              { "nodePath": "Trunk_Spatial/Left_Strut_Higher", … } ] }
```

Time 0 is closed, `length` is fully open; the app plays forward to open and backwards to close. Node paths are
relative to the scene root as written in the animation; renderers should resolve by the last path segment when the
full path does not exist in their scene graph.

## Versioning

`format` is bumped only for incompatible changes. Additive fields may appear in any release; consumers must ignore
unknown keys.
