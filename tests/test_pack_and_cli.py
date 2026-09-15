import json
import zipfile
from pathlib import Path

import pytest

from tesla_view_extractor.catalog import build_catalog
from tesla_view_extractor.cli import main
from tesla_view_extractor.godot.resolve import ResourceRoot
from tesla_view_extractor.manifest import PackBuilder
from tesla_view_extractor.pack import write_zip
from tesla_view_extractor.validate import validate_pack


def test_catalog(recovered: Path, rules_dir: Path):
    cat = build_catalog(ResourceRoot(recovered), rules_dir)
    kiwi = cat.vehicle("testcar")
    assert kiwi is not None and kiwi.id == "kiwi" and kiwi.present
    assert kiwi.api_match == {"model_key": "modelk", "fascia_type": ["baseKiwi", "performanceKiwi"]}
    s = cat.vehicle("s")
    assert s is not None and not s.present
    assert [w.api_name for w in cat.wheels_in_family("Ego/Wheels_Kiwi")] == ["Orbit19"]  # Orbit20 scene does not exist


def test_manifest_model(recovered: Path, rules_dir: Path):
    cat = build_catalog(ResourceRoot(recovered), rules_dir)
    res = PackBuilder(cat).build([cat.vehicle("kiwi")])
    m = res.manifest
    assert m["format"] == 1
    model = m["models"]["kiwi"]
    assert model["kind"] == "glb" and model["glb"] == "Ego/Kiwi/Kiwi.glb"
    assert model["overrides"] == "Ego/Kiwi/Kiwi.overrides.json"
    assert model["closures"] == {"frunk": ["HoodAnimation"], "trunk": ["TrunkAnimation"]}
    assert model["animations"] == {
        "HoodAnimation": "Ego/Kiwi/animations/HoodAnimation.json",
        "TrunkAnimation": "Ego/Kiwi/animations/TrunkAnimation.json",
    }
    assert model["lights"]["drl"] == {"on": ["DRL"]}
    assert model["lights"]["headlights"]["on"] == ["Headlights_On", "DRL"]
    assert model["lights"]["brake"] == {"on": ["Brake_Lights_On"], "off": ["Brake_Lights_Off"]}
    assert model["lights_eu"] is None
    assert model["variants"]["performance"] == {"show": ["Fascia_Perf"], "hide": ["Fascia_Standard"]}
    assert model["variants"]["rhd"] == {"show": ["RHD"], "hide": ["LHD"]}
    assert model["variants"]["plate_eu"] == {"show": ["Plate_EU"], "hide": ["Plate_US"]}
    assert model["variants"]["seats_7"] is None
    assert "Tesla_Badge" in model["hide"] and "Fascia_Perf" in model["hide"] and "DRL" in model["hide"]
    assert "Fascia_Standard" in model["show"] and "Brake_Lights_Off" in model["show"] and "LHD" in model["show"]
    assert model["pivots"]["wheels"] == ["Wheel_LF_Spatial", "Wheel_RF_Spatial", "Wheel_RL_Spatial", "Wheel_RR_Spatial"]
    assert model["pivots"]["charge_port"] == "ChargePortMarker"
    marker_names = {(x["name"], x["kind"]) for x in model["markers"]}
    assert (
        ("frunk", "marker") in marker_names
        and ("charge_port", "marker") in marker_names
        and ("lock", "locator") in marker_names
    )
    assert model["brakes"] == {
        "standard": {
            "front": "Ego/Brakes/Brakes_Std_F.overrides.json",
            "rear": "Ego/Brakes/Brakes_Std_R.overrides.json",
        }
    }
    assert model["wheel_family"] == "Ego/Wheels_Kiwi" and model["default_wheel"] == "Orbit19"
    assert model["aliases"] == ["testcar"] and model["warnings"] == []
    # wheels / cables / paints / environment
    assert m["wheels"]["Orbit19"]["kind"] == "glb" and m["wheels"]["Orbit19"]["family"] == "Ego/Wheels_Kiwi"
    assert m["cables"]["CCS"]["flow_node"] == "Charger_Cable"
    assert m["paints"]["colors"]["SolidBlack"]["albedo"] == "#0e0e0e"
    assert m["environment"]["panorama"] == "shared_misc_textures/Studio.png"
    # files: referenced textures in, skins out
    assert "Ego/Kiwi/Textures/Glass_BC.png" in res.files
    assert not any("Skins" in f for f in res.files)
    assert res.missing == []


def test_zip_is_deterministic_and_valid(recovered: Path, rules_dir: Path, tmp_path: Path):
    cat = build_catalog(ResourceRoot(recovered), rules_dir)
    res = PackBuilder(cat).build([cat.vehicle("kiwi")])
    a = write_zip(res, tmp_path / "a.zip")
    b = write_zip(res, tmp_path / "b.zip")
    assert a.sha256 == b.sha256
    with zipfile.ZipFile(tmp_path / "a.zip") as z:
        assert "manifest.json" in z.namelist()
        assert json.loads(z.read("manifest.json"))["models"]["kiwi"]["name"] == "Kiwi test car"
    rep = validate_pack(tmp_path / "a.zip")
    assert rep.ok, rep.errors
    assert rep.warnings == []


def test_validate_rejects_broken_pack(tmp_path: Path):
    p = tmp_path / "bad.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 1, "models": {"Bad Id": {"overrides": "x.json"}}}))
    rep = validate_pack(p)
    assert not rep.ok
    assert any("bad model id" in e for e in rep.errors)
    assert any("x.json" in e for e in rep.errors)


def test_cli_list_and_extract(recovered: Path, rules_dir: Path, tmp_path: Path, capsys):
    assert main(["list", str(recovered), "--rules", str(rules_dir), "--json", "--no-download"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [v["id"] for v in data["vehicles"] if v["present"]] == ["kiwi"]
    out = tmp_path / "pack.zip"
    rc = main(
        [str(recovered), "--rules", str(rules_dir), "--models", "kiwi", "-o", str(out), "--no-download", "--json"]
    )
    assert rc == 0 and out.exists()
    summary = json.loads(capsys.readouterr().out)
    assert summary["packs"][0]["models"] == ["kiwi"]
    assert main(["validate", str(out)]) == 0


def test_cli_non_tty_without_selection_exits_2(recovered: Path, rules_dir: Path, tmp_path: Path):
    with pytest.raises(SystemExit) as ex:
        main([str(recovered), "--rules", str(rules_dir), "-o", str(tmp_path / "x"), "--no-download"])
    assert ex.value.code == 2
