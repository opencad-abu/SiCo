from pathlib import Path

import pytest

from finfet_support import config, write
from rcepy.config import RceConfig
from rcepy.gen_gds import generate_gds
from rcepy.gen_lvs import generate_lvs
from rcepy.input_preflight import validate_input_locations
from rcepy.source_inputs import cdl_header_file


def test_mode_and_paths_are_frozen_and_explicit_request_wins(tmp_path, monkeypatch):
    assert not RceConfig({}, tmp_path / "rce.toml").flag("extract", "finfet_flow")
    monkeypatch.setenv("RCE_FINFET_FLOW", "1")
    monkeypatch.setenv("RCE_GDS_OBJECT_MAP", "maps/object map")
    cfg = RceConfig({}, tmp_path / "rce.toml")
    monkeypatch.setenv("RCE_FINFET_FLOW", "0")
    monkeypatch.setenv("RCE_GDS_OBJECT_MAP", "changed")
    assert cfg.flag("extract", "finfet_flow")
    assert cfg.path("input", "layout", "object_map") == str(tmp_path / "maps/object map")
    override = RceConfig({"extract": {"finfet_flow": False},
                          "input": {"layout": {"object_map": ""}}}, tmp_path / "rce.toml")
    assert not override.flag("extract", "finfet_flow")
    assert override.path("input", "layout", "object_map") == ""


@pytest.mark.parametrize("mode", ["true", "yes", "2", ""])
def test_invalid_mode_is_rejected(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("RCE_FINFET_FLOW", mode)
    with pytest.raises(ValueError, match="0 or 1"):
        RceConfig({}, tmp_path / "rce.toml")


def test_streamout_omits_missing_optional_layout_files(tmp_path):
    cfg = config(tmp_path).replace("input", "type", value="CDL+LAY")
    cfg = cfg.replace("input", "layout", value={"lib": "work", "cell": "top"})
    for mode in (True, False):
        text = generate_gds(
            cfg.replace("extract", "finfet_flow", value=mode), cfg.context()
        ).read_text()
        for name in ("objectMap", "propMap", "userSkillFile"):
            assert name not in text
    for field in ("object_map", "property_map", "user_skill_file"):
        cfg = cfg.replace("input", "layout", field, value=str(write(tmp_path / field)))
    text = generate_gds(cfg, cfg.context()).read_text()
    for name in ("objectMap", "propMap", "userSkillFile"):
        assert name in text
    cfg = cfg.replace("input", "layout", "object_map", value="missing")
    assert "objectMap" not in generate_gds(cfg, cfg.context()).read_text()


def test_lvs_preserves_pdk_and_rebases_recursive_dependencies(tmp_path):
    cfg = config(tmp_path)
    deck = Path(cfg.path("lvs", "runset_file"))
    before = deck.read_bytes()
    extra = write(tmp_path / "extra.svrf", "LVS ISOLATE SHORTS YES\n")
    cfg = cfg.replace("lvs", "extra_file", value=str(extra))
    text = generate_lvs(cfg, cfg.context()).read_text()
    adapted = (cfg.context().log_dir / "lvs_process/000_lvs.cal").read_text()
    assert "example" not in adapted
    assert "LVS REPORT MAXIMUM 1000" in adapted
    assert f'MASK SVDB DIRECTORY "{cfg.context().svdb_dir}" QUERY CCI SI' in adapted
    nested = (cfg.context().log_dir / "lvs_process/001_rules").read_text()
    encrypted = tmp_path / "common_file/DFM/device.encrypt"
    assert f'INCLUDE "{encrypted}"' in nested
    assert str(extra) in text
    wrapper = (cfg.context().log_dir / "source.with_devices.cdl").read_text()
    assert cfg.context().source_path in wrapper and "source.added" in wrapper
    assert deck.read_bytes() == before


def test_cdl_header_combines_user_header_and_source_added(tmp_path):
    cfg = config(tmp_path)
    header = write(tmp_path / "user header.cdl")
    cfg = cfg.replace("input", "schematic", "cdl_header_file", value=str(header))
    text = Path(cdl_header_file(cfg, cfg.context())).read_text()
    assert text.splitlines()[0] == f'.INCLUDE "{header}"'
    assert text.count("source.added") == 1


def test_preflight_rejects_auto_discovered_symlink_into_managed_data(tmp_path):
    cfg = config(tmp_path)
    source = Path(cfg.path("extract", "tech_dir")) / "source.added"
    source.unlink()
    source.symlink_to(write(cfg.context().log_dir / "saved-source"))
    with pytest.raises(ValueError, match="overlaps RCE-managed"):
        validate_input_locations(cfg, cfg.context(), ())


def test_lvs_missing_and_cyclic_dependencies_fail(tmp_path):
    cfg = config(tmp_path)
    deck = Path(cfg.path("lvs", "runset_file"))
    deck.write_text("INCLUDE missing.svrf\n")
    with pytest.raises(FileNotFoundError, match="RCE_LVS_INCLUDE_DIR"):
        generate_lvs(cfg, cfg.context())
    deck.write_text("INCLUDE lvs.cal\n")
    with pytest.raises(ValueError, match="Cyclic"):
        generate_lvs(cfg, cfg.context())


def test_lvs_comments_do_not_swallow_rewritten_controls_or_includes(tmp_path):
    cfg = config(tmp_path)
    deck = Path(cfg.path("lvs", "runset_file"))
    deck.write_text(
        '/* example INCLUDE missing\n*/ INCLUDE ./DFM/rules /* trailing\n'
        'comment */\n// INCLUDE missing\n'
        '#IFDEF MULTI_DEVICE_EXTRACTION\n'
        'MASK SVDB DIRECTORY old QUERY CCI SI /* note\ncontinued */\n'
        '#ELSE\nMASK SVDB DIRECTORY old QUERY CCI\n#ENDIF\n'
        'LAYOUT PATH example.gds /* note\ncontinued */\n'
        'LVS REPORT MAXIMUM 1000\n')
    generate_lvs(cfg, cfg.context())
    adapted = (cfg.context().log_dir / "lvs_process/000_lvs.cal").read_text()
    assert "/*" not in adapted and "*/" not in adapted
    assert "missing" not in adapted and "example.gds" not in adapted
    assert "001_rules" in adapted and "#IFDEF MULTI_DEVICE_EXTRACTION" in adapted
    assert "QUERY CCI SI" in adapted and "LVS REPORT MAXIMUM 1000" in adapted
