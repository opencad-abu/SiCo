"""Result publication and read-only access obey the project state boundary."""

from pathlib import Path

import pytest

from cadai.result_contract import ResultError
from cadai.result_tools import ResultStore
from test_result_contract import fixture_data


def test_result_path_preserves_nas_alias(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(project, target_is_directory=True)
    store = ResultStore(alias)
    _, _, snapshot, _ = fixture_data()
    ref, artifact = store.put("results", snapshot)
    assert Path(artifact["path"]).is_relative_to(alias / ".sico")
    assert store.get(ref) == snapshot
    assert not (project / ".cad").exists()


def test_missing_result_read_does_not_create_state(tmp_path):
    with pytest.raises(ResultError):
        ResultStore(tmp_path).get("results_" + "0" * 64)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("component", ["ai", "measurement-results"])
def test_result_reader_rejects_symlink_ancestors(tmp_path, component):
    store = ResultStore(tmp_path)
    _, _, snapshot, _ = fixture_data()
    ref, artifact = store.put("results", snapshot)
    path = Path(artifact["path"]).parent
    if component == "ai":
        path = path.parent
    outside = tmp_path / "outside"
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ResultError):
        store.get(ref)
