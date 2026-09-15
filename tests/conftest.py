"""Synthetic "recovered project" fixture: a fake vehicle codename `Kiwi` with the same *structure* the Tesla app uses
(root bindings, AnimationPlayers, Marker nodes, brakes, a wheel family, VehicleOptions/ProductManager tables) but
invented content. Nothing here comes from the Tesla app."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc00000030001"
    "6d84d4270000000049454e44ae426082"
)


def make_glb(nodes: list[str], materials: list[str]) -> bytes:
    """A GLB with the given node names (no geometry) – enough for the header reader."""
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": nodes[0], "children": list(range(1, len(nodes)))}] + [{"name": n} for n in nodes[1:]],
        "materials": [{"name": m} for m in materials],
        "meshes": [],
    }
    js = json.dumps(doc).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


KIWI_TSCN = """[gd_scene load_steps=12 format=2]

[ext_resource path="res://Ego/Kiwi/Script/Kiwi.gd" type="Script" id=2]
[ext_resource path="res://Ego/Brakes/Brakes_Std_F.tscn" type="PackedScene" id=3]
[ext_resource path="res://Ego/Brakes/Brakes_Std_R.tscn" type="PackedScene" id=7]
[ext_resource path="res://Ego/Kiwi/Kiwi.glb" type="PackedScene" id=6]
[ext_resource path="res://Ego/Kiwi/Paint.material" type="Material" id=14]
[ext_resource path="res://Ego/Kiwi/Glass.tres" type="Material" id=16]
[ext_resource path="res://Ego/Kiwi/HoodAnimation.tres" type="Animation" id=25]
[ext_resource path="res://mobile/scripts/Marker.gd" type="Script" id=36]

[sub_resource type="Animation" id=8]
resource_name = "TrunkAnimation"
length = 1.2
tracks/0/type = "transform"
tracks/0/path = NodePath("Trunk_Spatial")
tracks/0/keys = PoolRealArray( 0, 1, 0, 1.4, 1.3, 0, 0, 0, 1, 1, 1, 1, 1.2, 1, 0, 1.4, 1.3, -0.5, 0, 0, 0.866, 1, 1, 1 )
tracks/1/type = "value"
tracks/1/path = NodePath("Trunk_Spatial/Strut:transform")
tracks/1/keys = {
"times": PoolRealArray( 0, 1.2 ),
"transitions": PoolRealArray( 1, 1 ),
"update": 0,
"values": [ Transform( 1, 0, 0, 0, 1, 0, 0, 0, 1, -0.5, 0.2, 0.5 ), Transform( 1, 0, 0, 0, 0.99, 0.13, 0, -0.13, 0.99, -0.5, 0.2, 0.5 ) ]
}

[sub_resource type="SpatialMaterial" id=9]
resource_name = "Interior"
albedo_color = Color( 0.1, 0.1, 0.1, 1 )
roughness = 0.9

[node name="ROOT" instance=ExtResource( 6 )]
script = ExtResource( 2 )
has_us_plate = true
body_path = NodePath("../ROOT/Static_Exterior")
plate_us_path = NodePath("Plate_US")
plate_eu_path = NodePath("Plate_EU")
drl_path = NodePath("../ROOT/DRL")
headlights_path = NodePath("../ROOT/Headlights_On")
brake_lights_center_path = NodePath("Trunk_Spatial/Brake_Lights_On")
turn_signal_l_path = NodePath("../ROOT/Left_Turn_On")
turn_signal_r_path = NodePath("../ROOT/Right_Turn_On")
charge_port_path = NodePath("Spatials/ChargePortMarker")
lf_wheel_path = NodePath("../ROOT/Wheel_LF_Spatial")
lr_wheel_path = NodePath("../ROOT/Wheel_RL_Spatial")
rf_wheel_path = NodePath("../ROOT/Wheel_RF_Spatial")
rr_wheel_path = NodePath("../ROOT/Wheel_RR_Spatial")
lf_brake_path = NodePath("../ROOT/Brake_LF_Spatial")
lr_brake_path = NodePath("../ROOT/Brake_RL_Spatial")
rf_brake_path = NodePath("../ROOT/Brake_RF_Spatial")
rr_brake_path = NodePath("../ROOT/Brake_RR_Spatial")
interior_rhd_path = NodePath("RHD")
interior_lhd_path = NodePath("LHD")
fascia_standard_path = NodePath("Fascia_Standard")
fascia_perf_path = NodePath("Fascia_Perf")
tesla_badge_path = NodePath("Hood_Spatial/Tesla_Badge")
hide_tesla_badge = true
brakes_standard_front_left = ExtResource( 3 )
brakes_standard_rear_left = ExtResource( 7 )
marker_locators_paths = [ NodePath("Hood_Spatial/Frunk_Marker"), NodePath("Lock_Marker") ]

[node name="Static_Exterior" parent="." index="0"]
material/0 = ExtResource( 14 )
material/1 = ExtResource( 16 )
material/2 = SubResource( 9 )

[node name="Wheel_LF_Spatial" parent="." index="1"]
transform = Transform( 0, 0, -1.05, 0, 1.05, 0, 1.05, 0, 0, -0.78, 0.35, -1.5 )

[node name="Hood_Spatial" parent="." index="2"]

[node name="Frunk_Marker" type="Spatial" parent="Hood_Spatial" index="0"]
transform = Transform( 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, -0.09, -1.05 )

[node name="Tesla_Badge" parent="Hood_Spatial" index="1"]
visible = false

[node name="Lock_Marker" type="Spatial" parent="." index="3"]
transform = Transform( 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1.7, 0 )

[node name="Spatials" type="Spatial" parent="." index="4"]

[node name="FrunkMarker" type="Spatial" parent="Spatials" index="0"]
transform = Transform( 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.83, -1.75 )
script = ExtResource( 36 )
marker_name = "frunk"

[node name="ChargePortMarker" type="Spatial" parent="Spatials" index="1"]
transform = Transform( 1, 0, 0, 0, 1, 0, 0, 0, 1, -0.78, 1.02, 1.97 )
script = ExtResource( 36 )
marker_name = "chargePort"

[node name="Plate_US" parent="." index="5"]
visible = false

[node name="HoodAnimation" type="AnimationPlayer" parent="." index="6"]
anims/HoodAnimation = ExtResource( 25 )

[node name="TrunkAnimation" type="AnimationPlayer" parent="." index="7"]
anims/TrunkAnimation = SubResource( 8 )
"""

HOOD_ANIM = """[gd_resource type="Animation" format=2]

[resource]
resource_name = "HoodAnimation"
length = 1.25
tracks/0/type = "transform"
tracks/0/path = NodePath("Hood_Spatial")
tracks/0/keys = PoolRealArray( 0, 1, 0, 1.05, -1.15, 0, 0, 0, 1, 1, 1, 1, 1.25, 1, 0, 1.05, -1.15, 0.38, 0, 0, 0.92, 1, 1, 1 )
"""

PAINT_MATERIAL = """[gd_resource type="ShaderMaterial" load_steps=2 format=2]

[ext_resource path="res://shaders/opaque_skybox.shader" type="Shader" id=1]

[resource]
shader = ExtResource( 1 )
shader_param/albedo = Color( 0.2, 0.2, 0.2, 1 )
shader_param/roughness = 0.2
"""

GLASS_TRES = """[gd_resource type="SpatialMaterial" load_steps=2 format=2]

[ext_resource path="res://Ego/Kiwi/Textures/Glass_BC.png" type="Texture" id=1]

[resource]
resource_name = "Glass"
flags_transparent = true
albedo_color = Color( 0, 0, 0, 0.86 )
albedo_texture = ExtResource( 1 )
metallic = 1.0
roughness = 0.05
"""

BRAKES_TSCN = """[gd_scene load_steps=3 format=2]

[ext_resource path="res://Ego/Brakes/Objects/Caliper_Std_{S}.obj" type="ArrayMesh" id=1]
[ext_resource path="res://Ego/Brakes/Brakes_Std.tres" type="Material" id=5]

[node name="Spatial" type="Spatial"]

[node name="Caliper_Std_{S}" type="MeshInstance" parent="."]
mesh = ExtResource( 1 )
material/0 = ExtResource( 5 )
"""

BRAKES_TRES = """[gd_resource type="SpatialMaterial" load_steps=2 format=2]

[ext_resource path="res://Ego/Brakes/Textures/Brakes_Std_MRA.png" type="Texture" id=2]

[resource]
resource_name = "Brakes"
albedo_color = Color( 0.66, 0.66, 0.66, 1 )
metallic = 1.0
metallic_texture = ExtResource( 2 )
roughness_texture = ExtResource( 2 )
roughness_texture_channel = 1
"""

WHEEL_TSCN = """[gd_scene load_steps=3 format=2]

[ext_resource path="res://Ego/Wheels_Kiwi/Orbit/Orbit.tres" type="Material" id=1]
[ext_resource path="res://Ego/Wheels_Kiwi/Orbit/Orbit.glb" type="PackedScene" id=2]

[node name="Orbit" instance=ExtResource( 2 )]

[node name="Orbit" parent="." index="0"]
material/0 = ExtResource( 1 )
"""

WHEEL_TRES = """[gd_resource type="SpatialMaterial" format=2]

[resource]
resource_name = "Orbit"
albedo_color = Color( 0.3, 0.3, 0.3, 1 )
metallic = 0.8
"""

CABLE_TSCN = """[gd_scene load_steps=5 format=2]

[ext_resource path="res://mobile/geometry/Charging_Cable/Objects/Cable.obj" type="ArrayMesh" id=5]
[ext_resource path="res://mobile/geometry/Charging_Cable/Textures/Cable_BC.png" type="Texture" id=1]
[ext_resource path="res://mobile/materials/PowerflowSingle.shader" type="Shader" id=7]

[sub_resource type="ShaderMaterial" id=1]
shader = ExtResource( 7 )
shader_param/pulse_length = 1.5
shader_param/flow_state = 0
shader_param/texture_bc = ExtResource( 1 )

[node name="Spatial" type="Spatial"]

[node name="Charger_Cable" type="MeshInstance" parent="."]
mesh = ExtResource( 5 )
material/0 = SubResource( 1 )
"""

VEHICLE_OPTIONS_GD = """extends Node

const BASE_METALLIC = 0.3;
const BASE_ROUGHNESS = 0.04;

const ExteriorColorValue: Dictionary = {
	"SolidBlack": {"color": Color("#0e0e0e"), "metallic": 1.0, "roughness": BASE_ROUGHNESS},
	"PearlWhite": {"color": Color("#181818"), "metallic": 0.25, "roughness": 0.2},
}
const FALLBACK_EXTERIOR_COLOR = {"color": Color("#161616"), "metallic": 0.6, "roughness": BASE_ROUGHNESS}

enum WheelType{
	Apollo,
	Orbit_19 Orbit_20,
	Halo22,
}

const DefaultWheelForVehicleType = {
	"modelk": WheelType.Orbit_19,
}

const WheelTypeToPathMap: Dictionary = {
	WheelType.Apollo: NodePath("res://Ego/Wheels/Wheel_Apollo.tscn"),
	WheelType.Orbit_19: NodePath("res://Ego/Wheels_Kiwi/Orbit/Orbit.tscn"),
	WheelType.Orbit_20: NodePath("res://Ego/Wheels_Kiwi/Orbit20/Orbit20.tscn"),
}

const MobileWheelTypeEnumMap: Dictionary = {
	"Orbit19": WheelType.Orbit_19,
	"Orbit19Dark": WheelType.Orbit_19,
}

const ChargePortTypeToCableMap = {
	"CCS": NodePath("res://mobile/geometry/Charging_Cable/Charging_Cable_CCS2.tscn"),
}
"""

PRODUCT_MANAGER_GD = """extends Node

func get_vehicle_node_path(model_key: String, fascia_type: String, chassis_type: String, default_node_path: NodePath):
	match model_key:
		"modelk":
			match fascia_type:
				"baseKiwi", "performanceKiwi":
					return NodePath("res://Ego/Kiwi/Kiwi.tscn")
				_:
					return NodePath("res://Ego/Kiwi/Kiwi.tscn")
		"models":
			return NodePath("res://Ego/S/Model_S.tscn")
		_:
			return default_node_path
"""

MOBILE_ENV_TEST_GD = """extends Node

enum CameraPosition{
	PARKED, TOP_DOWN
}

const CameraSettings = {
	CameraPosition.PARKED: {"rot": Vector3(68.6, - 138, 0), "offset": Vector3( - 0.06, 6.7, 0)},
	CameraPosition.TOP_DOWN: {"rot": Vector3(0, 0, 0), "offset": Vector3(0, 10, 0)},
}
const EnvironmentSettings = {
	CameraPosition.PARKED: {"rot": Vector3(0, - 7, 83), "env_energy": 4, "amb_energy": 4},
	CameraPosition.TOP_DOWN: {"rot": Vector3( - 10, - 10, 0), "env_energy": 6, "amb_energy": 2.5},
}
"""

ENV_MANAGER_GD = """extends Node
var light_background_color: Color = Color("#F7F7F7")
var dark_background_color: Color = Color("#161718")
func setup(data):
	env_energy = data.get("env_energy", 3)
	amb_energy = data.get("amb_energy", 7)
"""

ENV_TRES = """[gd_resource type="Environment" load_steps=3 format=2]

[ext_resource path="res://shared_misc_textures/Studio.png" type="Texture" id=1]

[sub_resource type="PanoramaSky" id=1]
panorama = ExtResource( 1 )

[resource]
background_mode = 2
background_sky = SubResource( 1 )
ambient_light_energy = 2.5
"""

MOBILE_TSCN = """[gd_scene format=2]

[node name="Root" type="Spatial"]

[node name="Camera" type="Camera" parent="."]
fov = 40.0
"""

VEHICLE_MANAGER_GD = """extends Node
func on_flash_headlights(data: Dictionary):
	var flash_duration: float = data.get("flash_duration", 1.5)
	var flash_interval: float = data.get("flash_interval", 1)
"""


@pytest.fixture
def recovered(tmp_path: Path) -> Path:
    r = tmp_path / "recovered"
    write(r / "project.godot", '[rendering]\nquality/driver/driver_name="GLES2"\n')
    write(r / "Ego/Kiwi/Kiwi.tscn", KIWI_TSCN)
    write(r / "Ego/Kiwi/Script/Kiwi.gd", "extends Node\n")
    write(r / "Ego/Kiwi/HoodAnimation.tres", HOOD_ANIM)
    write(r / "Ego/Kiwi/Paint.material", PAINT_MATERIAL)
    write(r / "Ego/Kiwi/Glass.tres", GLASS_TRES)
    (r / "Ego/Kiwi/Textures").mkdir(parents=True)
    (r / "Ego/Kiwi/Textures/Glass_BC.png").write_bytes(TINY_PNG)
    (r / "Ego/Kiwi/Textures/Skins").mkdir()
    (r / "Ego/Kiwi/Textures/Skins/Wrap.png").write_bytes(TINY_PNG)
    (r / "Ego/Kiwi/Kiwi.glb").write_bytes(
        make_glb(
            [
                "Kiwi",
                "Static_Exterior",
                "DRL",
                "Headlights_On",
                "Left_Turn_On",
                "Right_Turn_On",
                "Wheel_LF_Spatial",
                "Wheel_RF_Spatial",
                "Wheel_RL_Spatial",
                "Wheel_RR_Spatial",
                "Brake_LF_Spatial",
                "Brake_RF_Spatial",
                "Brake_RL_Spatial",
                "Brake_RR_Spatial",
                "Hood_Spatial",
                "Trunk_Spatial",
                "Brake_Lights_On",
                "Brake_Lights_Off",
                "RHD",
                "LHD",
                "Fascia_Standard",
                "Fascia_Perf",
                "Plate_EU",
                "Plate_US",
                "Tesla_Badge",
            ],
            ["Paint", "Glass", "Interior"],
        )
    )
    for side in ("F", "R"):
        write(r / f"Ego/Brakes/Brakes_Std_{side}.tscn", BRAKES_TSCN.replace("{S}", side))
        write(r / f"Ego/Brakes/Objects/Caliper_Std_{side}.obj", "o caliper\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    write(r / "Ego/Brakes/Brakes_Std.tres", BRAKES_TRES)
    (r / "Ego/Brakes/Textures").mkdir(parents=True)
    (r / "Ego/Brakes/Textures/Brakes_Std_MRA.png").write_bytes(TINY_PNG)
    write(r / "Ego/Wheels_Kiwi/Orbit/Orbit.tscn", WHEEL_TSCN)
    write(r / "Ego/Wheels_Kiwi/Orbit/Orbit.tres", WHEEL_TRES)
    (r / "Ego/Wheels_Kiwi/Orbit/Orbit.glb").write_bytes(make_glb(["Orbit", "Orbit"], ["Orbit"]))
    write(r / "mobile/geometry/Charging_Cable/Charging_Cable_CCS2.tscn", CABLE_TSCN)
    write(r / "mobile/geometry/Charging_Cable/Objects/Cable.obj", "o cable\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    (r / "mobile/geometry/Charging_Cable/Textures").mkdir(parents=True)
    (r / "mobile/geometry/Charging_Cable/Textures/Cable_BC.png").write_bytes(TINY_PNG)
    write(r / "mobile/scripts/VehicleOptions.gd", VEHICLE_OPTIONS_GD)
    write(r / "mobile/scripts/data/ProductManager.gd", PRODUCT_MANAGER_GD)
    write(r / "mobile/scripts/MobileEnvTest.gd", MOBILE_ENV_TEST_GD)
    write(r / "mobile/scripts/EnvironmentManager.gd", ENV_MANAGER_GD)
    write(r / "mobile/scripts/VehicleManager.gd", VEHICLE_MANAGER_GD)
    write(r / "mobile/scripts/Marker.gd", "extends Spatial\nexport (String) var marker_name\n")
    write(r / "env/mobile.tres", ENV_TRES)
    write(r / "mobile.tscn", MOBILE_TSCN)
    (r / "shared_misc_textures").mkdir()
    (r / "shared_misc_textures/Studio.png").write_bytes(TINY_PNG)
    return r


@pytest.fixture
def rules_dir(tmp_path: Path) -> Path:
    d = tmp_path / "rules"
    d.mkdir()
    (d / "kiwi.yaml").write_text(
        "id: kiwi\nname: Kiwi test car\ncodename: Kiwi\nscene: Ego/Kiwi/Kiwi.tscn\naliases: [testcar]\n"
        "wheel_family: Ego/Wheels_Kiwi\ndefault_wheel: Orbit19\n"
    )
    return d
