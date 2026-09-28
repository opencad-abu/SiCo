"""Independent Python flows share the launch identity and restore vendor settings."""

import pytest

from cadbatch.environment import task_environment
from cadbatch.manifest import load_manifest
from cadenv import restore_eda_temp_environment
from cadlsf.cache_types import default_cache_root, default_monitor_cache_root
from cadview.nl2view_environment import import_environment
from drcpy.rule_expansion import default_cache_dir as rule_cache_directory
from rcepy.dspf.indexer import default_cache_dir as dspf_cache_directory
from rcepy.pathutil import cad_temp_dir, cad_temp_environment
from rcepy.run_artifacts import protected_root_names
from sicotemp import initialize, initialize_project
from batch_controller_fixtures import _python_command, _write_manifest


@pytest.fixture(autouse=True)
def isolated_project(tmp_path, monkeypatch):
    for name in ("CAD_TEMP_DIR", "SICO_TEMP_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("legacy", [False, True])
def test_ai_and_flow_handoffs_share_state_and_original_eda_settings(tmp_path, legacy):
    state = tmp_path / (".cad" if legacy else ".sico")
    if legacy:
        state.mkdir(mode=0o700)
    if legacy:
        with pytest.raises(ValueError, match="migrat"):
            initialize_project({})
        assert list(state.iterdir()) == []
        assert not (tmp_path / ".sico").exists()
        return
    source = {"TMPDIR": "/vendor/tmp", "TEMP": "", "XDG_CACHE_HOME": "/vendor/cache"}
    environment = dict(source)
    assert initialize_project(environment) == state
    assert initialize(environment) == state / "ai"
    assert initialize_project(environment) == state
    restored = restore_eda_temp_environment(environment)
    restored.pop("PYTHONDONTWRITEBYTECODE")
    assert restored == source
    assert not (tmp_path / (".sico" if legacy else ".cad")).exists()


def test_flow_uses_supplied_environment_and_nas_identity_after_chdir(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(project)
    inherited = {"SICO_TEMP_DIR": str(alias / ".sico"), "TMPDIR": "/vendor/tmp"}
    environment = cad_temp_environment(inherited)
    assert environment["TMPDIR"] == str(alias / ".sico")
    monkeypatch.setenv("SICO_TEMP_DIR", environment["SICO_TEMP_DIR"])
    assert cad_temp_dir("rce", "dspf", create=True) == alias / ".sico/rce/dspf"
    assert not (tmp_path / ".sico").exists()
    assert restore_eda_temp_environment(environment)["TMPDIR"] == "/vendor/tmp"


def test_rce_cleanup_keeps_new_state_through_nas_alias(tmp_path, monkeypatch):
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path)
    monkeypatch.setenv("SICO_TEMP_DIR", str(alias / ".sico"))
    cad_temp_dir(create=True)
    keep = protected_root_names(tmp_path, managed_names=(), initial_root_names=set(),
                                output_paths=())
    assert keep == {".sico"}


def test_batch_manifest_conflict_fails_without_creating_state(tmp_path, monkeypatch):
    path = _write_manifest(tmp_path, [_python_command("pass")])
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("SICO_TEMP_DIR", str(other / ".sico"))
    with pytest.raises(ValueError, match="conflicts"):
        load_manifest(path)
    with pytest.raises(ValueError, match="conflicts"):
        task_environment(tmp_path / "launch/.sico")
    assert not (other / ".sico").exists()
    assert not (tmp_path / "launch/.sico").exists()


@pytest.mark.parametrize("value", [" ", "relative/.sico"])
def test_flow_rejects_invalid_new_environment(tmp_path, value):
    with pytest.raises(ValueError):
        cad_temp_environment({"SICO_TEMP_DIR": value})
    assert list(tmp_path.iterdir()) == []


def test_nl2view_preserves_state_path_but_restores_vendor_temp(tmp_path):
    source = {"SICO_TEMP_DIR": str(tmp_path / ".sico"), "TMPDIR": "/vendor/tmp"}
    initialize(source)
    environment, directory = import_environment(True, source)
    assert directory == tmp_path / ".sico"
    assert environment["TMPDIR"] == "/vendor/tmp"
    assert environment["CDS5X_NOLINK"] == "1"
    assert "SICO_TEMP_DIR" not in environment and "CAD_TEMP_DIR" not in environment


def test_flow_refuses_unsafe_existing_runtime_without_chmod(tmp_path):
    state = tmp_path / ".sico"
    state.mkdir(mode=0o700)
    runtime = state / "runtime"
    runtime.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private"):
        initialize_project({})
    assert runtime.stat().st_mode & 0o777 == 0o755


def test_lsf_caches_share_private_launch_root_after_chdir(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    environment = {"SICO_TEMP_DIR": str(project / ".sico")}
    selected = default_cache_root(environment, cwd=tmp_path, user_id=1001, create=True)
    assert default_monitor_cache_root(environment, cwd=tmp_path, user_id=1001) == selected
    assert selected == project / ".sico/lsf/1001"
    assert all(path.stat().st_mode & 0o777 == 0o700
               for path in (selected, selected.parent, selected.parent.parent))
    assert not (tmp_path / ".sico").exists()


def test_lsf_refuses_parallel_root_without_creating_cache(tmp_path):
    for name in (".cad", ".sico"):
        (tmp_path / name).mkdir(mode=0o700)
    with pytest.raises(ValueError, match="activation"):
        default_cache_root({}, cwd=tmp_path, create=True)
    assert not (tmp_path / ".sico/lsf").exists()


@pytest.mark.parametrize("owner", [rule_cache_directory, dspf_cache_directory])
def test_default_design_cache_writers_create_private_ancestors(tmp_path, monkeypatch, owner):
    monkeypatch.delenv("DRC_RULE_GROUP_CACHE_DIR", raising=False)
    monkeypatch.delenv("RCE_DSPF_CACHE_DIR", raising=False)
    path = owner(create=True)
    assert path.is_relative_to(tmp_path / ".sico")
    while path != tmp_path:
        assert path.stat().st_mode & 0o777 == 0o700
        path = path.parent
