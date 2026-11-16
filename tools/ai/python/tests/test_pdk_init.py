"""PDK initialization is ensured once per session and blocks design on failure."""

import pytest
from pdk_fixture import Bridge

from cadai.pdk_init import ensure_selected
from cadai.pdk_preparation import PdkPreparation
from cadai.pdk_schema import PdkUnavailable


def bound_workspace(bridge, library="smic28hkmg"):
    bridge.ctx["libraries"] = [
        {"name": library, "resolved_path": "/" + library, "status": "complete",
         "technology_library": library, "technology_status": "known", "pdk_ref": None,
         "pdk_version": None, "pdk_status": "unknown", "source": "test"},
        {"name": "sxTest", "resolved_path": "/tmp/sxTest", "status": "complete",
         "technology_library": library, "technology_status": "known", "pdk_ref": None,
         "pdk_version": None, "pdk_status": "unknown", "source": "test"},
    ]
    bridge.data["identity"]["target"]["library"] = "sxTest"


def test_builtin_libraries_need_no_initialization():
    bridge = Bridge()
    preparation = PdkPreparation(bridge)
    result = ensure_selected(preparation, {"library": "basic"})
    assert result["status"] == "not_required_builtin"
    assert not bridge.calls


def test_skill_cache_hit_is_reported_as_cached():
    bridge = Bridge()
    bridge.init_result = {"ok": True, "library": "smic28hkmg", "init_file": "libInit.il",
                          "init_path": "/synthetic/pdk/smic28hkmg/libInit.il",
                          "status": "initialized", "cached": True, "initialized": True}
    preparation = PdkPreparation(bridge)
    result = ensure_selected(preparation, {"library": "smic28hkmg"})
    assert result["status"] == "initialized" and result["cached"] is True
    assert len([call for call in bridge.calls
                if "aiPdkInitializeLibrary" in call[1]]) == 1


def test_first_use_initializes_once_then_reports_cached():
    bridge = Bridge()
    bridge.init_loaded = False
    preparation = PdkPreparation(bridge)
    first = ensure_selected(preparation, {"library": "smic28hkmg"})
    assert first["status"] == "initialized" and first["initialized"] is True
    assert first["init_path"].endswith("smic28hkmg/libInit.il")
    loads = [call for call in bridge.calls if "aiPdkInitializeLibrary" in call[1]]
    assert len(loads) == 1
    second = ensure_selected(preparation, {"library": "smic28hkmg"})
    assert second["cached"] is True
    assert len([call for call in bridge.calls if "aiPdkInitializeLibrary" in call[1]]) == 1


def test_failed_initialization_blocks_design_tools(tmp_path):
    bridge = Bridge()
    bridge.init_loaded = False
    bridge.init_result = {"ok": False, "code": "pdk_init_failed",
                          "message": "intentional init failure"}
    bound_workspace(bridge)
    preparation = PdkPreparation(bridge, workspace=tmp_path)
    info = preparation.inspect({"target_library": "sxTest"})
    assert info["selection_required"] is False
    assert info["pdk_init"]["ok"] is False
    assert info["pdk_init"]["code"] == "pdk_init_failed"
    blocked = preparation.guard("preview_simulation_recipe")
    assert blocked is not None and blocked["ok"] is False
    assert blocked["code"] == "pdk_init_failed"
    assert blocked["next_action"].startswith("init_pdk")
    assert preparation.guard("init_pdk") is None


def test_resolved_selection_reports_initialization(tmp_path):
    bridge = Bridge()
    bound_workspace(bridge)
    preparation = PdkPreparation(bridge, workspace=tmp_path)
    info = preparation.inspect({"target_library": "sxTest"})
    assert info["selected_library"] == "smic28hkmg"
    assert info["pdk_init"]["ok"] is True
    assert info["pdk_init"]["library"] == "smic28hkmg"


def test_missing_library_name_is_rejected():
    bridge = Bridge()
    preparation = PdkPreparation(bridge)
    with pytest.raises(PdkUnavailable) as failure:
        ensure_selected(preparation, {})
    assert failure.value.code == "pdk_init_library_missing"
