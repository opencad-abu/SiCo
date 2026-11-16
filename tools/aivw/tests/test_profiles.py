from __future__ import annotations

from aivworkbench.profiles import available_profiles, load_profile
from aivworkbench.toolchain import parse_setup_exports, parse_setup_modules


def test_amsverify_profile_encodes_approved_contract() -> None:
    profile = load_profile("amsverify")
    assert "amsverify" in available_profiles()
    assert profile.workspace_root.as_posix() == "/workarea/xh/amsVerify"
    assert profile.modules == (
        "ic/23.10.130",
        "xcelium/23.09.002",
        "spectre/23.10.538",
    )
    assert set(profile.pilots) == {"m0-e", "m0-s", "m1-ai"}
    assert profile.storage is not None
    assert profile.storage.payload_env == "PROJ_AMS_DB_DIR"
    assert {item.name for item in profile.inputs} >= {
        "m1_ai_schematic",
        "m1_ai_maestro",
        "m1_ai_manual",
    }
    assert profile.projects["adc"]["environment"]["PROJECT"] == (
        "/workarea/xh/amsVerify/adc_verification_v5.0_001"
    )
    assert profile.projects["pll"]["environment"]["PROJECT"] == (
        "/workarea/xh/amsVerify/pll_verification_ws_v2.0"
    )
    assert profile.projects["ldo"]["root"] == "/workarea/xh/amsVerify/DESIGNS/LDO_IP"
    assert profile.projects["ldo"]["cds_lib"] == "/workarea/xh/amsVerify/cds.lib"
    assert profile.projects["ldo"]["source_cds_lib"] == (
        "/workarea/xh/amsVerify/DESIGNS/LDO_IP/cds.lib"
    )
    assert profile.projects["ldo"]["model_root"] == (
        "/workarea/xh/amsVerify/DESIGNS/LDO_IP/models/spectre"
    )
    assert profile.projects["ldo"]["topologies"]["LDO_AON"]["ports"] == [
        "VDD",
        "VSS",
        "EN",
        "VOUT",
    ]
    assert profile.projects["ldo"]["connect_rule"]["name"] == "CR_full_fast"
    assert profile.projects["ldo"]["connect_rule"]["policy"] == (
        "approved_cadence_builtin_staged_read_only"
    )
    assert "smic28" not in profile.source_path.read_text(encoding="utf-8").lower()


def test_setup_script_matches_profile_without_executing_it() -> None:
    profile = load_profile("amsverify")
    assert parse_setup_modules(profile.setup_script) == profile.modules
    assert parse_setup_exports(profile.setup_script, ("PROJ_AMS_DB_DIR",)) == {
        "PROJ_AMS_DB_DIR": "/workarea/xh/amsVerify/data/ams"
    }
    text = profile.setup_script.read_text(encoding="utf-8")
    assert "ckUpdateEnv" not in text
