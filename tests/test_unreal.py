import io
import json
import zipfile
from pathlib import Path

from PIL import Image

from conftest import HOOD_ANIM, make_glb
from tesla_model_extractor.catalog import build_catalog
from tesla_model_extractor.cli import main
from tesla_model_extractor.convert.anim import convert_clip
from tesla_model_extractor.godot.resolve import ResourceRoot
from tesla_model_extractor.godot.text import parse_text
from tesla_model_extractor.manifest import PackBuilder
from tesla_model_extractor.pack import write_zip
from tesla_model_extractor.unreal.anim import add_animation
from tesla_model_extractor.unreal.gltf import Document, set_matrix, trs_from_mat4
from tesla_model_extractor.unreal.materials import MaterialBuilder, Paint
from tesla_model_extractor.unreal.objmesh import add_obj_mesh, parse_obj
from tesla_model_extractor.unreal.packsource import load_pack
from tesla_model_extractor.unreal.scene import Assembler, ExportOptions
from tesla_model_extractor.unreal.textures import TextureRepacker


def _png(pixels: list[tuple[int, int, int, int]], size=(2, 1)) -> bytes:
    img = Image.new("RGBA", size)
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _decode(doc: Document, tex_index: int) -> Image.Image:
    img = doc.json["images"][doc.json["textures"][tex_index]["source"]]
    bv = doc.json["bufferViews"][img["bufferView"]]
    return Image.open(io.BytesIO(bytes(doc.bin[bv["byteOffset"] : bv["byteOffset"] + bv["byteLength"]]))).convert(
        "RGBA"
    )


# ---------- gltf.py ----------


def test_glb_roundtrip_detach_compact():
    raw = make_glb(["Root", "A", "B", "C"], ["M1", "M2"], prims={"B": 2})
    doc = Document.from_glb(raw)
    again = Document.from_glb(doc.to_glb())
    assert again.json == doc.json and again.bin == doc.bin
    assert len(doc.meshes) == 3 and len(doc.json["accessors"]) == 20
    b = doc.find("B")
    doc.detach(b)
    doc.compact()
    assert [n["name"] for n in doc.nodes] == ["Root", "A", "C"]
    assert len(doc.meshes) == 2 and len(doc.json["accessors"]) == 10
    assert len(doc.bin) < len(raw)
    assert doc.json["buffers"][0]["byteLength"] == len(doc.bin)
    for bv in doc.json["bufferViews"]:
        assert bv["byteOffset"] + bv["byteLength"] <= len(doc.bin)


def test_merge_offsets_and_clone_shares_meshes():
    car = Document.empty("Car", "Car_Root")
    car.merge(Document.from_glb(make_glb(["Car", "Body", "Pivot"], ["Paint"])), car.root)
    wheel = Document.from_glb(make_glb(["Wheel", "Rim"], ["Rubber"]))
    pivot = car.find("Pivot")
    root = car.merge(wheel, pivot)
    assert car.nodes[root]["name"] == "Wheel" and root in car.nodes[pivot]["children"]
    rim = car.find("Rim")
    prim = car.meshes[car.nodes[rim]["mesh"]]["primitives"][0]
    assert car.materials[prim["material"]]["name"] == "Rubber"
    assert car.json["accessors"][prim["attributes"]["POSITION"]]["max"] == [1, 1, 0]
    assert "GODOT_single_root" not in car.json.get("extensionsUsed", [])
    clone = car.clone_subtree(root, car.find("Body"))
    assert len(car.meshes) == 3  # Body, Pivot, Rim: shared, not copied
    names = [n["name"] for n in car.nodes]
    assert names.count("Wheel") == 2 and names.count("Rim") == 2
    car.compact()
    assert len(car.json["images"]) == 2 and len(car.materials) == 2
    assert car.nodes[car.find("Body")]["children"] == [clone] or clone in car.nodes[car.find("Body")]["children"]


def test_trs_from_column_major_matrix():
    # Godot Transform( 0,0,-1.05, 0,1.05,0, 1.05,0,0, -0.78,0.35,-1.5 ) as the pack stores it (column-major 4x4)
    m = [0.0, 0.0, 1.05, 0.0, 0.0, 1.05, 0.0, 0.0, -1.05, 0.0, 0.0, 0.0, -0.78, 0.35, -1.5, 1.0]
    t, q, s = trs_from_mat4(m)
    assert t == [-0.78, 0.35, -1.5]
    assert [round(x, 3) for x in s] == [1.05, 1.05, 1.05]
    assert [round(x, 3) for x in q] == [0.0, -0.707, 0.0, 0.707]  # the app's -90° axle yaw
    node = {"matrix": m}
    set_matrix(node, m)
    assert "matrix" not in node and node["translation"] == [-0.78, 0.35, -1.5]


# ---------- textures / materials ----------


def test_pbr_material_repacks_channels_and_flags():
    mra = _png([(200, 100, 50, 255), (0, 255, 128, 255)])  # R metallic, G roughness, B ao
    normal = _png([(128, 0, 255, 255), (128, 255, 255, 255)])
    files = {"Textures/Trim_MRA.png": mra, "Textures/Trim_Normal.png": normal, "Textures/Trim_BC.png": mra}
    doc = Document.empty("t", "root")
    mb = MaterialBuilder(doc, TextureRepacker(files.get))
    desc = {
        "kind": "pbr",
        "name": "Trim",
        "albedo": [0.5, 0.5, 0.5],
        "alpha": 1.0,
        "albedo_texture": "Textures/Trim_BC.png",
        "metallic": 1.0,
        "metallic_texture": "Textures/Trim_MRA.png",
        "metallic_channel": 0,
        "roughness": 1.0,
        "roughness_texture": "Textures/Trim_MRA.png",
        "roughness_channel": 1,
        "ao_texture": "Textures/Trim_MRA.png",
        "ao_channel": 2,
        "ao_on_uv2": True,
        "normal_texture": "Textures/Trim_Normal.png",
        "normal_scale": 0.8,
        "emission": [0.0, 0.0, 0.0],
        "emission_energy": 0.8,
        "emission_texture": "Textures/Trim_BC.png",
        "uv1_scale": [10.0, 10.0, 1.0],
        "transparent": False,
        "cull_mode": 2,
        "blend_mode": 1,
        "unshaded": False,
    }
    idx = mb.build("Trim.tres", desc)
    mat = doc.materials[idx]
    pbr = mat["pbrMetallicRoughness"]
    assert pbr["baseColorFactor"][:3] == [0.21404] * 3  # sRGB 0.5 → linear
    mr = _decode(doc, pbr["metallicRoughnessTexture"]["index"])
    assert mr.getpixel((0, 0))[1:3] == (100, 200) and mr.getpixel((1, 0))[1:3] == (255, 0)  # G rough, B metal
    ao = _decode(doc, mat["occlusionTexture"]["index"])
    assert ao.getpixel((0, 0))[0] == 50 and mat["occlusionTexture"]["texCoord"] == 1
    n = _decode(doc, mat["normalTexture"]["index"])
    assert n.getpixel((0, 0))[1] == 255 and n.getpixel((1, 0))[1] == 0  # green inverted
    assert mat["normalTexture"]["scale"] == 0.8
    assert pbr["baseColorTexture"]["extensions"]["KHR_texture_transform"] == {"scale": [10.0, 10.0]}
    assert mat["emissiveFactor"] == [1.0, 1.0, 1.0]
    assert mat["extensions"]["KHR_materials_emissive_strength"] == {"emissiveStrength": 0.8}
    assert mat["doubleSided"] is True and mat["alphaMode"] == "BLEND" and mat["extras"]["godot_blend_mode"] == "add"
    assert mb.additive == ["Trim"]
    assert mb.build("Trim.tres", desc) == idx  # memoised
    # a primitive without TEXCOORD_1 gets a variant without the UV2 occlusion map
    uv0 = doc.materials[mb.build("Trim.tres", desc, texcoords=frozenset({0}))]
    assert "occlusionTexture" not in uv0 and uv0["name"] == "Trim_uv0" and "normalTexture" in uv0
    assert "KHR_texture_transform" in doc.json["extensionsUsed"]


def test_shader_materials():
    doc = Document.empty("t", "root")
    mb = MaterialBuilder(doc, TextureRepacker(lambda rel: None), Paint("Quicksilver", [0.17, 0.18, 0.21], 0.85, 0.2))
    mb.paint_explicit = True
    paint = doc.materials[
        mb.build(
            "Paint.material",
            {"kind": "car_paint", "shader": "shaders/opaque_skybox.shader", "params": {"color": [0, 0, 1, 1]}},
        )
    ]
    assert paint["pbrMetallicRoughness"]["metallicFactor"] == 0.85
    assert paint["extensions"]["KHR_materials_clearcoat"]["clearcoatFactor"] == 1.0
    assert paint["extras"]["paint"] == "Quicksilver"
    glass = doc.materials[
        mb.build(
            "Glass.tres",
            {"kind": "tinted_glass", "params": {"color": [0, 0, 0, 0.86], "roughness": 0.2, "metalic": 0.0}},
        )
    ]
    assert glass["alphaMode"] == "BLEND" and glass["pbrMetallicRoughness"]["baseColorFactor"][3] == 0.86
    other = doc.materials[
        mb.build("x.material", {"kind": "shader", "name": "Defrost", "shader": "d.shader", "params": {}})
    ]
    assert "KHR_materials_unlit" in other["extensions"] and any("Defrost" in w for w in mb.warnings)
    # the Cybertruck paint_mix shader keeps its own stainless values unless a paint was chosen explicitly
    mb2 = MaterialBuilder(doc, TextureRepacker(lambda rel: None), Paint("fallback", [0.09, 0.09, 0.09], 0.6, 0.04))
    mix = doc.materials[
        mb2.build(
            "Paint_Mix.material",
            {
                "kind": "car_paint",
                "shader": "Ego/Cybertruck/Shaders/paint_mix.shader",
                "params": {"color_bright": [0.2, 0.2, 0.2, 1], "metallic_bright": 0.9},
            },
        )
    ]
    assert mix["pbrMetallicRoughness"]["metallicFactor"] == 0.9 and "paint" not in mix.get("extras", {})


# ---------- anim / obj ----------


def test_animation_channels():
    gf = parse_text(HOOD_ANIM, None)
    data = convert_clip(gf.resource.props, "HoodAnimation").data
    doc = Document.from_glb(make_glb(["Root", "Hood_Spatial", "Other"], ["M"]))
    st = add_animation(doc, data, {"Hood_Spatial": doc.find("Hood_Spatial")})
    anim = doc.animations[0]
    paths = sorted(c["target"]["path"] for c in anim["channels"])
    assert paths == ["rotation", "translation"]  # constant unit scale dropped
    assert st.channels == 2 and st.missing_nodes == []
    rot = next(s for c, s in zip(anim["channels"], anim["samplers"], strict=True) if c["target"]["path"] == "rotation")
    acc = doc.json["accessors"][rot["input"]]
    assert acc["count"] == 2 and acc["min"] == [0.0] and acc["max"] == [1.25]
    st2 = add_animation(
        doc, {"name": "X", "tracks": [{"nodePath": "Gone", "keys": [{"time": 0, "position": [0, 0, 0]}]}]}, {}
    )
    assert st2.missing_nodes == ["Gone"] and len(doc.animations) == 1


def test_obj_surfaces_per_usemtl_and_uv_flip():
    text = "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nusemtl A\nf 1/1 2/2 3/3 4/4\nusemtl B\nf 1 2 3\n"
    surfaces = parse_obj(text)
    assert [s.material for s in surfaces] == ["A", "B"]
    assert surfaces[0].indices == [0, 1, 2, 0, 2, 3]  # quad fan-triangulated
    assert surfaces[0].uvs[:4] == [0.0, 1.0, 1.0, 1.0]  # v flipped like Godot's importer
    assert surfaces[1].has_normal is False and surfaces[1].normals[:3] == [0.0, 0.0, 1.0]  # flat normal
    doc = Document.empty("t", "root")
    mesh, n = add_obj_mesh(doc, "brake", text)
    assert n == 2 and len(doc.meshes[mesh]["primitives"]) == 2
    assert "TEXCOORD_0" not in doc.meshes[mesh]["primitives"][1]["attributes"]


# ---------- assembler ----------


def _pack(recovered: Path, rules_dir: Path):
    cat = build_catalog(ResourceRoot(recovered), rules_dir)
    return PackBuilder(cat).build([cat.vehicle("kiwi")])


def test_export_vehicle_default_look(recovered: Path, rules_dir: Path):
    res = _pack(recovered, rules_dir)
    out = Assembler(res, ExportOptions(paint="SolidBlack")).export_vehicle("kiwi")
    doc = Document.from_glb(out.glb)
    names = [n["name"] for n in doc.nodes]
    # variants: EU plate + standard fascia + LHD kept, the alternatives removed; lights kept but hidden
    assert "Plate_EU" in names and "Fascia_Standard" in names and "LHD" in names
    assert "Plate_US" not in names and "Fascia_Perf" not in names and "RHD" not in names
    assert sorted(out.sidecar["removed_nodes"]) == ["Fascia_Perf", "Plate_US", "RHD"]
    assert doc.nodes[doc.find("DRL")]["extras"]["visible"] is False
    assert doc.nodes[doc.find("Tesla_Badge")]["extras"]["visible"] is False
    assert "DRL" in out.sidecar["hidden_nodes"] and "Brake_Lights_Off" not in out.sidecar["hidden_nodes"]
    # wheels + brakes under the pivots, renamed per corner, meshes shared
    for tag in ("LF", "RF", "RL", "RR"):
        w = doc.find(f"Wheel_{tag}")
        assert w in doc.nodes[doc.find(f"Wheel_{tag}_Spatial")]["children"]
        assert doc.find(f"Caliper_Std_{'F' if tag.endswith('F') else 'R'}_{tag}") is not None
    assert doc.nodes[doc.find("Wheel_LF_Spatial")]["translation"] == [-0.78, 0.35, -1.5]
    assert (
        doc.nodes[doc.find("Rim_LF")]["mesh"] == doc.nodes[doc.find("Rim_RR")]["mesh"] if doc.find("Rim_LF") else True
    )
    # materials: tscn slots applied to the three Static_Exterior primitives
    prims = doc.meshes[doc.nodes[doc.find("Static_Exterior")]["mesh"]]["primitives"]
    kinds = [doc.materials[p["material"]]["extras"]["godot"]["kind"] for p in prims]
    assert kinds == ["car_paint", "pbr", "pbr"]
    assert doc.materials[prims[0]["material"]]["extras"]["paint"] == "SolidBlack"
    assert doc.materials[prims[1]["material"]]["alphaMode"] == "BLEND"
    # markers become empty nodes, animations target the pivots
    frunk = doc.find("FrunkMarker")
    assert frunk is not None and "mesh" not in doc.nodes[frunk] and doc.nodes[frunk]["extras"]["marker"] == "frunk"
    assert doc.nodes[doc.find("Lock_Marker")]["translation"] == [0.0, 1.7, 0.0]
    anims = {a["name"]: a for a in doc.animations}
    assert set(anims) == {"HoodAnimation", "TrunkAnimation"}
    assert all(doc.nodes[c["target"]["node"]]["name"] == "Hood_Spatial" for c in anims["HoodAnimation"]["channels"])
    assert out.sidecar["closures"] == {"frunk": ["HoodAnimation"], "trunk": ["TrunkAnimation"]}
    assert out.sidecar["wheel"] == "Orbit19" and out.sidecar["brakes"] == "standard"
    assert out.sidecar["paint"] == "SolidBlack" and "SolidBlack" in out.sidecar["paints"]["colors"]
    assert "GODOT_single_root" not in doc.json.get("extensionsUsed", [])
    assert not [w for w in out.warnings if "not found" in w]


def test_export_vehicle_variants_and_keep_all(recovered: Path, rules_dir: Path):
    res = _pack(recovered, rules_dir)
    perf = Assembler(
        res, ExportOptions(variants=frozenset({"performance", "plate_us", "rhd"}), wheel=None, brakes=None)
    )
    doc = Document.from_glb(perf.export_vehicle("kiwi").glb)
    names = [n["name"] for n in doc.nodes]
    assert "Fascia_Perf" in names and "Fascia_Standard" not in names
    assert "Plate_US" in names and "Plate_EU" not in names and "RHD" in names and "LHD" not in names
    assert doc.find("Wheel_LF") is None and doc.find("Caliper_Std_F_LF") is None
    keep = Assembler(res, ExportOptions(keep_all=True, yaw_deg=90)).export_vehicle("kiwi")
    doc = Document.from_glb(keep.glb)
    assert doc.find("Plate_US") is not None and doc.find("Fascia_Perf") is not None
    assert keep.sidecar["removed_nodes"] == []
    assert [round(x, 3) for x in doc.nodes[doc.root]["rotation"]] == [0.0, 0.707, 0.0, 0.707]


def test_cli_unreal_from_recovered_and_from_pack(recovered: Path, rules_dir: Path, tmp_path: Path, capsys):
    out = tmp_path / "unreal"
    rc = main(
        [
            "unreal",
            str(recovered),
            "--rules",
            str(rules_dir),
            "--models",
            "kiwi",
            "-o",
            str(out),
            "--json",
            "--separate-wheels",
            "--cables",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    v = summary["vehicles"][0]
    assert v["model"] == "kiwi" and v["animations"] == 2 and v["wheels"] == ["Orbit19"] and v["cables"] == ["CCS"]
    folder = out / "kiwi"
    assert (folder / "Kiwi.glb").exists() and (folder / "wheels" / "Orbit19.glb").exists()
    assert (folder / "cables" / "CCS.glb").exists() and (folder / "studio_panorama.png").exists()
    sidecar = json.loads((folder / "unreal.json").read_text())
    assert sidecar["file"] == "Kiwi.glb" and sidecar["environment_panorama"] == "studio_panorama.png"
    assert sidecar["lights"]["drl"] == {"on": ["DRL"]}
    wheel = Document.from_glb((folder / "wheels" / "Orbit19.glb").read_bytes())
    assert wheel.materials[0]["extras"]["godot"]["source"].endswith("Orbit.tres")
    # the same export from a pack zip
    cat = build_catalog(ResourceRoot(recovered), rules_dir)
    res = PackBuilder(cat).build([cat.vehicle("kiwi")])
    res.files["manifest.json"] = json.dumps(res.manifest).encode()
    zip_path = tmp_path / "pack.zip"
    write_zip(res, zip_path)
    loaded = load_pack(zip_path)
    assert "Ego/Kiwi/Kiwi.glb" in loaded.files
    out2 = tmp_path / "unreal2"
    assert main(["unreal", str(zip_path), "-o", str(out2)]) == 0
    a = Document.from_glb((out / "kiwi" / "Kiwi.glb").read_bytes())
    b = Document.from_glb((out2 / "kiwi" / "Kiwi.glb").read_bytes())
    assert a.json == b.json and a.bin == b.bin
    with zipfile.ZipFile(zip_path) as z:
        assert "manifest.json" in z.namelist()
