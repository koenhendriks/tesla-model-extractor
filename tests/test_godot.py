import math

import pytest

from tesla_model_extractor.godot.math import decompose_transform, transform_to_mat4
from tesla_model_extractor.godot.text import parse_header_attrs, parse_text
from tesla_model_extractor.godot.values import Call, ExtRef, GodotValueError, Ident, SubRef, parse_value, pool_floats


def test_literals():
    assert parse_value("true") is True
    assert parse_value("false") is False
    assert parse_value("null") is None
    assert parse_value("-7") == -7
    assert parse_value("1.5e-3") == pytest.approx(0.0015)
    assert parse_value('"a \\"quoted\\" string"') == 'a "quoted" string'


def test_calls_and_refs():
    assert parse_value("ExtResource( 6 )") == ExtRef(6)
    assert parse_value("SubResource(8)") == SubRef(8)
    v = parse_value("Color( 0.5, 0.25, 0, 1 )")
    assert isinstance(v, Call) and v.floats() == [0.5, 0.25, 0, 1]
    assert parse_value('NodePath("A/B:transform")') == Call("NodePath", ("A/B:transform",))
    assert pool_floats(parse_value("PoolRealArray( 0, 1, 2.5 )")) == [0, 1, 2.5]


def test_unary_minus_with_space_from_decompiled_gdscript():
    assert parse_value("Vector3(68.6, - 138, 0)") == Call("Vector3", (68.6, -138, 0))


def test_nested_dict_and_array():
    v = parse_value(
        '{"times": PoolRealArray( 0, 0.8 ), "values": [ Transform( 1,0,0, 0,1,0, 0,0,1, 1,2,3 ) ], WheelType.A: BASE }'
    )
    assert pool_floats(v["times"]) == [0, 0.8]
    assert v["values"][0].name == "Transform"
    assert v[Ident("WheelType.A")] == Ident("BASE")


def test_trailing_garbage_is_error():
    with pytest.raises(GodotValueError):
        parse_value("1 2")


def test_header_attrs_with_call():
    a = parse_header_attrs(' name="ROOT" instance=ExtResource( 6 )')
    assert a == {"name": "ROOT", "instance": ExtRef(6)}


def test_parse_text_blocks():
    gf = parse_text(
        '[gd_scene load_steps=2 format=2]\n\n[ext_resource path="res://a/b.glb" type="PackedScene" id=1]\n\n'
        '[sub_resource type="Animation" id=3]\nlength = 1.2\ntracks/0/keys = {\n"times": PoolRealArray( 0, 1 ),\n"values": [ 1, 2 ]\n}\n\n'
        '[node name="ROOT" instance=ExtResource( 1 )]\nvisible = false\n\n[node name="Child" parent="." index="0"]\n'
    )
    assert gf.header.kind == "gd_scene"
    assert gf.ext[1].rel == "a/b.glb"
    assert gf.subs[3].props["length"] == 1.2
    assert pool_floats(gf.subs[3].props["tracks/0/keys"]["times"]) == [0, 1]
    assert [n.name for n in gf.nodes] == ["ROOT", "Child"]
    assert gf.nodes[0].props["visible"] is False
    assert gf.ext_rel(gf.nodes[0].attrs["instance"]) == "a/b.glb"


def test_transform_roundtrip():
    # 90° about Y with scale 1.05 and translation
    c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
    t = [c * 1.05, 0, s * 1.05, 0, 1.05, 0, -s * 1.05, 0, c * 1.05, -0.78, 0.35, -1.5]
    m = transform_to_mat4(t)
    assert m[12:15] == [-0.78, 0.35, -1.5] and m[15] == 1.0
    pos, quat, scale = decompose_transform(t)
    assert pos == [-0.78, 0.35, -1.5]
    assert scale == pytest.approx([1.05, 1.05, 1.05])
    assert abs(sum(q * q for q in quat) - 1) < 1e-9
    assert quat[1] == pytest.approx(math.sin(math.pi / 4), abs=1e-6)  # rotation about Y


def test_mirrored_transform_gets_negative_scale():
    _, _, scale = decompose_transform([-1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
    assert scale[0] < 0


def test_multiline_node_header_keeps_properties_on_its_own_node():
    text = (
        '[gd_scene format=2]\n\n[node name="Door" parent="."]\nmaterial/0 = 1\n\n'
        '[node name="Door_Handle_Signature" parent="." groups=[\n"signature",\n]]\nvisible = false\n'
    )
    nodes = {b.name: b for b in parse_text(text, None).nodes}
    assert "visible" not in nodes["Door"].props
    assert nodes["Door_Handle_Signature"].props["visible"] is False
    assert nodes["Door_Handle_Signature"].attrs["groups"] == ["signature"]
