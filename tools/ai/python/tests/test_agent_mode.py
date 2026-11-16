"""Project agent mode defaults to design and requires an explicit revision."""

import pytest
from sico.agent_resources import mode


def test_default_mode_is_design_and_generation_round_trips(tmp_path):
    initial = mode.read(tmp_path)
    assert initial["pdk_workflow"] == "design"
    assert initial["revision"] is None

    saved = mode.save(tmp_path, "generation", expected_revision=None)
    assert saved["pdk_workflow"] == "generation"
    assert mode.read(tmp_path)["pdk_workflow"] == "generation"

    with pytest.raises(mode.ModeConflictError):
        mode.save(tmp_path, "design", expected_revision=None)

    mode.save(tmp_path, "design", expected_revision=saved["revision"])
    assert mode.read(tmp_path)["pdk_workflow"] == "design"


def test_invalid_mode_is_rejected_without_replacing_the_file(tmp_path):
    mode.save(tmp_path, "generation", expected_revision=None)
    before = mode.read(tmp_path)
    with pytest.raises(ValueError, match="design 或 generation"):
        mode.save(tmp_path, "maintenance", expected_revision=before["revision"])
    assert mode.read(tmp_path) == before
