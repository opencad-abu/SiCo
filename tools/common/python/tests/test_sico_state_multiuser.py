"""One shared configuration selects private state in each user's work directory."""

import os
from types import SimpleNamespace

import pytest

from sicostate import publish, root, validate_directory
from sicotemp import initialize, initialize_project


@pytest.mark.parametrize("setting", [None, "", ".sico", "./.sico", ".//.sico"])
@pytest.mark.parametrize("legacy", [None, ""])
def test_shared_configuration_selects_independent_workspaces(tmp_path, setting, legacy):
    configuration = {}
    if setting is not None:
        configuration["SICO_TEMP_DIR"] = setting
    if legacy is not None:
        configuration["CAD_TEMP_DIR"] = legacy
    for user in ("user_a", "user_b"):
        project = tmp_path / user
        project.mkdir()
        environment = dict(configuration)
        expected = project / ".sico"
        assert root(project, environment=environment) == expected
        assert initialize(environment, cwd=project) == expected / "ai"
        assert environment["SICO_TEMP_DIR"] == str(expected)
        assert "CAD_TEMP_DIR" not in environment
        for path in [expected, *expected.rglob("*")]:
            assert path.stat().st_mode & 0o777 == 0o700
        # Hand-off after chdir must keep the initial identity, including flow.
        assert initialize_project(environment, cwd=tmp_path) == expected
        assert root(environment=environment) == expected
    assert not (tmp_path / ".sico").exists()


def test_relative_configuration_uses_logical_launch_not_later_cwd(tmp_path, monkeypatch):
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(physical)
    monkeypatch.chdir(physical)
    environment = {"PWD": str(alias), "SICO_TEMP_DIR": ".sico"}
    assert publish(environment) == alias / ".sico"
    monkeypatch.chdir(tmp_path)
    assert initialize(environment) == alias / ".sico/ai"
    assert not (tmp_path / ".sico").exists()


def test_misspelled_relative_path_is_diagnosed_without_guessing(tmp_path):
    with pytest.raises(ValueError, match="SICO_TEMP_DIR") as error:
        initialize({"SICO_TEMP_DIR": ".//heng.xia/.sico"}, cwd=tmp_path)
    assert str(tmp_path / "heng.xia") in str(error.value)
    assert list(tmp_path.iterdir()) == []


def test_relative_environment_cannot_override_bound_state(tmp_path):
    with pytest.raises(ValueError, match="conflicts"):
        initialize({"SICO_TEMP_DIR": ".sico"}, cwd=tmp_path,
                   temporary=tmp_path / "another/.sico")
    assert list(tmp_path.iterdir()) == []


def test_empty_new_setting_does_not_reactivate_retired_identity(tmp_path):
    with pytest.raises(ValueError, match="CAD_TEMP_DIR was removed"):
        initialize({"SICO_TEMP_DIR": "", "CAD_TEMP_DIR": str(tmp_path / ".cad")},
                   cwd=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_another_users_state_is_rejected_without_chmod():
    class ForeignDirectory:
        def lstat(self):
            return SimpleNamespace(st_mode=0o40700, st_uid=os.getuid() + 1)

        def __str__(self):
            return "/project/another-user/.sico"

    with pytest.raises(ValueError, match="own project work directory"):
        validate_directory(ForeignDirectory())
