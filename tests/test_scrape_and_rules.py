from pathlib import Path

from tesla_model_extractor.godot.resolve import ResourceRoot
from tesla_model_extractor.rules import load_rules, rules_for_scene, slug
from tesla_model_extractor.scrape.environment import scrape_environment
from tesla_model_extractor.scrape.options import api_wheel_name, scrape_vehicle_options
from tesla_model_extractor.scrape.product import scrape_product_table


def test_vehicle_options(recovered: Path):
    o = scrape_vehicle_options((recovered / "mobile/scripts/VehicleOptions.gd").read_text())
    assert o.base_roughness == 0.04 and o.base_metallic == 0.3
    assert o.paints["SolidBlack"] == {"albedo": "#0e0e0e", "metallic": 1.0, "roughness": 0.04}
    assert o.paints["PearlWhite"]["roughness"] == 0.2
    assert o.fallback_paint["albedo"] == "#161616"
    assert o.wheel_types == ["Apollo", "Orbit_19", "Orbit_20", "Halo22"]  # missing comma tolerated
    assert o.wheel_paths["Orbit_19"] == "Ego/Wheels_Kiwi/Orbit/Orbit.tscn"
    assert o.wheel_aliases["Orbit19Dark"] == "Orbit_19"
    assert o.default_wheels["modelk"] == "Orbit_19"
    assert o.cable_map["CCS"] == "mobile/geometry/Charging_Cable/Charging_Cable_CCS2.tscn"
    assert api_wheel_name("HelixV2_20_Dark") == "HelixV220Dark"
    assert o.warnings == []


def test_product_table(recovered: Path):
    t = scrape_product_table((recovered / "mobile/scripts/data/ProductManager.gd").read_text())
    kiwi = [r for r in t.routes if r.scene == "Ego/Kiwi/Kiwi.tscn"]
    assert {r.model_key for r in kiwi} == {"modelk"}
    assert ["baseKiwi", "performanceKiwi"] in [r.fascia_type for r in kiwi]
    assert any(r.fascia_type is None for r in kiwi)  # the `_:` default branch
    assert "Ego/S/Model_S.tscn" in t.scenes()


def test_environment(recovered: Path):
    env = scrape_environment(ResourceRoot(recovered))
    m = env.to_manifest()
    assert m["panorama"] == "shared_misc_textures/Studio.png"
    assert m["presets"]["parked"]["camera"]["pivot_deg"] == [68.6, -138, 0]
    assert m["presets"]["top_down"]["env_energy"] == 6 and m["presets"]["top_down"]["sky_rot_deg"] == [-10, -10, 0]
    assert m["bg"] == {"dark": "#161718", "light": "#F7F7F7"}
    assert m["fov"] == 40.0 and m["flash"] == {"duration_s": 1.5, "interval_s": 1.0}
    assert m["renderer"] == "gles2_gamma"
    assert env.warnings == []


def test_rules_merge_and_boolean_keys(rules_dir: Path):
    r = load_rules("Kiwi", rules_dir)
    assert r["id"] == "kiwi" and r["aliases"] == ["testcar"]
    assert "on" in r["lights"]["drl"]  # YAML `on:` must stay a string key
    assert "off_nodes" in r["lights"]["brake"]
    assert r["wheel_family"] == "Ego/Wheels_Kiwi"


def test_unknown_codename_gets_defaults():
    r = rules_for_scene("Ego/Mango/Mango.tscn")
    assert r["id"] == "mango" and r["name"] == "Mango" and r["closures"]["frunk"]
    assert slug("BayberryE41") == "bayberry_e41" and slug("S_Palladium") == "s_palladium" and slug("3_High") == "3_high"
