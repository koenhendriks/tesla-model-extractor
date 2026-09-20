from pathlib import Path

from tesla_model_extractor.convert.anim import convert_animation_file, convert_clip
from tesla_model_extractor.convert.scene import convert_scene
from tesla_model_extractor.godot.resolve import ResourceRoot


def test_scene_overrides_and_facts(recovered: Path):
    root = ResourceRoot(recovered)
    res = convert_scene(root, "Ego/Kiwi/Kiwi.tscn")
    ov, f = res.overrides, res.facts
    assert f.root_instance == "Ego/Kiwi/Kiwi.glb"
    assert f.root_script == "Ego/Kiwi/Script/Kiwi.gd"
    assert f.bindings["drl_path"] == ("../ROOT/DRL", "DRL")
    assert f.bindings["brake_lights_center_path"][1] == "Brake_Lights_On"
    # materials by slot, keyed by project-relative path / sub_resource id
    slots = ov["nodes"]["Static_Exterior"]["materials"]
    assert slots == {"0": "Ego/Kiwi/Paint.material", "1": "Ego/Kiwi/Glass.tres", "2": "sub_resource#9"}
    assert ov["materials"]["Ego/Kiwi/Paint.material"]["kind"] == "car_paint"
    glass = ov["materials"]["Ego/Kiwi/Glass.tres"]
    assert (
        glass["kind"] == "pbr"
        and glass["transparent"] is True
        and glass["albedo_texture"] == "Ego/Kiwi/Textures/Glass_BC.png"
    )
    assert ov["materials"]["sub_resource#9"]["roughness"] == 0.9
    assert ov["materials_by_name"]["Glass"] == "Ego/Kiwi/Glass.tres"
    # wheel pivot transform → column-major matrix with translation last
    m = ov["nodes"]["Wheel_LF_Spatial"]["matrix"]
    assert len(m) == 16 and m[12:15] == [-0.78, 0.35, -1.5]
    assert ov["nodes"]["Tesla_Badge"]["visible"] is False
    # animation players, both external and inline
    players = {p.player: p for p in f.players}
    assert players["HoodAnimation"].ext_rel == "Ego/Kiwi/HoodAnimation.tres"
    assert players["TrunkAnimation"].sub_id == 8
    # markers: Marker.gd nodes and root locators
    names = {(m.kind, m.name) for m in f.markers}
    assert ("marker", "frunk") in names and ("marker", "chargePort") in names
    assert ("locator", "lock") in names and ("locator", "frunk") in names
    lock = next(m for m in f.markers if m.node == "Lock_Marker")
    assert lock.position == [0, 1.7, 0]
    assert f.packed_scene_props["brakes_standard_front_left"] == "Ego/Brakes/Brakes_Std_F.tscn"


def test_anim_external_transform_track(recovered: Path):
    root = ResourceRoot(recovered)
    r = convert_animation_file(root.load("Ego/Kiwi/HoodAnimation.tres"), "HoodAnimation")
    assert r.warnings == []
    d = r.data
    assert d["name"] == "HoodAnimation" and d["length"] == 1.25
    assert len(d["tracks"]) == 1 and d["tracks"][0]["nodePath"] == "Hood_Spatial"
    keys = d["tracks"][0]["keys"]
    assert len(keys) == 2 and keys[1]["time"] == 1.25 and keys[1]["quaternion"] == [0.38, 0, 0, 0.92]


def test_anim_inline_value_track_decomposes_transforms(recovered: Path):
    root = ResourceRoot(recovered)
    gf = root.load("Ego/Kiwi/Kiwi.tscn")
    r = convert_clip(gf.subs[8].props, "TrunkAnimation")
    assert r.warnings == []
    tracks = {t["nodePath"]: t for t in r.data["tracks"]}
    assert set(tracks) == {"Trunk_Spatial", "Trunk_Spatial/Strut"}
    strut = tracks["Trunk_Spatial/Strut"]["keys"]
    assert strut[0]["position"] == [-0.5, 0.2, 0.5]
    assert strut[0]["quaternion"] == [0, 0, 0, 1]
    assert strut[1]["quaternion"][0] < 0  # rotated about X
    assert all(abs(s - 1) < 1e-2 for s in strut[1]["scale"])


def test_obj_scene(recovered: Path):
    res = convert_scene(ResourceRoot(recovered), "Ego/Brakes/Brakes_Std_F.tscn")
    assert res.facts.root_instance is None
    n = res.overrides["nodes"]["Caliper_Std_F"]
    assert n["mesh"] == "Ego/Brakes/Objects/Caliper_Std_F.obj" and n["materials"] == {"0": "Ego/Brakes/Brakes_Std.tres"}
    mat = res.overrides["materials"]["Ego/Brakes/Brakes_Std.tres"]
    assert mat["roughness_channel"] == 1 and mat["metallic_texture"] == "Ego/Brakes/Textures/Brakes_Std_MRA.png"


def test_anim_old_style_vector_tracks_merge_into_partial_transform_keys():
    from tesla_model_extractor.convert.anim import convert_clip, quat_from_euler_yxz_deg
    from tesla_model_extractor.godot.values import parse_value

    props = {
        "resource_name": "Hood",
        "length": 1.0,
        "tracks/0/type": "value",
        "tracks/0/path": parse_value('NodePath("Hood_Spatial:rotation_degrees")'),
        "tracks/0/keys": parse_value(
            '{"times": PoolRealArray( 0, 1 ), "values": [ Vector3( 0, 0, 0 ), Vector3( 60, 0, 0 ) ]}'
        ),
        "tracks/1/type": "value",
        "tracks/1/path": parse_value('NodePath("Hood_Spatial:translation")'),
        "tracks/1/keys": parse_value(
            '{"times": PoolRealArray( 0, 0.5, 1 ), "values": [ Vector3( 0, 1, -1 ), Vector3( 0, 1.1, -1 ), Vector3( 0, 1.2, -1 ) ]}'
        ),
    }
    r = convert_clip(props, "Hood")
    assert r.warnings == []
    (t,) = r.data["tracks"]
    assert t["nodePath"] == "Hood_Spatial" and t["partial"] is True and t["fields"] == ["position", "quaternion"]
    assert [k["time"] for k in t["keys"]] == [0, 0.5, 1]
    assert t["keys"][1]["position"] == [0, 1.1, -1]
    assert t["keys"][1]["quaternion"] == quat_from_euler_yxz_deg([30, 0, 0])  # interpolated rotation
    assert "scale" not in t["keys"][0]
    q = quat_from_euler_yxz_deg([0, 90, 0])
    assert abs(q[1] - 0.70710678) < 1e-6 and abs(q[3] - 0.70710678) < 1e-6
