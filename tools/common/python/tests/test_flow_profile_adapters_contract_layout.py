from __future__ import annotations

from flow_profile_adapter_fixtures import *

def test_profile_layout_only_stretches_config_file_field() -> None:
    source = _source("common/skill++/PROFILEGUI.ils")

    assert "list(list(inst->profileFile 'stretch 1)" in source
    for button in ("profileLoad", "profileSave", "profileSaveAs"):
        assert f"list(inst->{button} 'stretch" not in source


def test_profile_handoff_has_no_legacy_slot() -> None:
    source = _source("common/skill/SICO_profile.il")

    assert "procedure(SICO_profileSetLoadData(flow version data)" in source
    assert "cadProfileLoadData=list(SICO_profileFlowKey(flow) version data)" in source
    assert "cadddr(cadProfileLoadData)" not in source
    assert "cadProfileLegacySource" not in source
