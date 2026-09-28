"""Path field contracts and the two file formats' explicit path bases."""

from pathlib import Path

import pytest

from cadconfig.paths import PATH_KEYS, SELECTION_PATH_KEYS, resolve_path
from cadprofile.model import _FLOW_ALLOWED_PATHS, path_keys
from test_config_contract import profile_values, put, read_profile, read_request


PATH_FIELDS = [(flow, field) for flow, paths in _FLOW_ALLOWED_PATHS.items()
               for field in sorted(PATH_KEYS & paths)]


def test_profile_path_keys_exposes_shared_vocabulary():
    assert path_keys() == PATH_KEYS | SELECTION_PATH_KEYS


@pytest.mark.parametrize("flow,field", PATH_FIELDS)
@pytest.mark.parametrize("value", [0, True, [], None])
def test_path_fields_reject_non_text(flow, field, value, tmp_path, monkeypatch):
    raw = profile_values(flow, tmp_path)
    put(raw, field, value)
    with pytest.raises(ValueError, match="must be a string"):
        read_request(raw, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="must be a string"):
        read_profile(raw, tmp_path, monkeypatch)


def test_same_path_resolves_from_file_directory_in_profile_and_request(tmp_path, monkeypatch):
    raw = profile_values("RCE", tmp_path)
    raw["extract"]["tech_dir"] = "./tech/../tech"
    profile = read_profile(raw, tmp_path, monkeypatch)
    request = read_request(raw, tmp_path, monkeypatch)
    expected = str(tmp_path / "tech")
    assert profile.as_dict()["extract.tech_dir.resolved"] == expected
    assert request.path("extract", "tech_dir") == expected


def test_portable_environment_value_is_deferred_until_execution(tmp_path, monkeypatch):
    monkeypatch.delenv("CAD_CONTRACT_MISSING", raising=False)
    raw = profile_values("RCE", tmp_path)
    raw["extract"]["tech_dir"] = "${CAD_CONTRACT_MISSING}/tech"
    profile = read_profile(raw, tmp_path, monkeypatch)
    request = read_request(raw, tmp_path, monkeypatch)
    assert profile.as_dict()["extract.tech_dir.resolved"] == ""
    with pytest.raises(ValueError, match="Undefined or empty environment"):
        request.path("extract", "tech_dir")
    monkeypatch.setenv("CAD_CONTRACT_MISSING", str(tmp_path))
    assert request.path("extract", "tech_dir") == str(tmp_path / "tech")


def test_lef_output_base_is_a_file_adapter_choice(tmp_path, monkeypatch):
    raw = profile_values("LEF", tmp_path)
    raw["output"]["lef_file"] = "top.lef"
    profile = read_profile(raw, tmp_path, monkeypatch)
    request = read_request(raw, tmp_path, monkeypatch)
    assert profile.as_dict()["output.lef_file.resolved"] == str(tmp_path / "top.lef")
    assert request.output_lef == tmp_path / "runs/top.lef"
    raw["output"]["lef_file"] = str(request.output_lef)
    assert read_profile(raw, tmp_path, monkeypatch).as_dict()["output.lef_file.resolved"] == str(request.output_lef)


def test_symlink_expansion_and_executable_name_preservation(tmp_path):
    target = tmp_path / "target"
    target.touch()
    link = tmp_path / "abstract"
    link.symlink_to(target)
    assert resolve_path("abstract", tmp_path) == target
    assert resolve_path("abstract", tmp_path, resolve_symlinks=False) == link


def test_corner_temperature_table_remains_a_readonly_mapping(tmp_path):
    from rcepy.config import RceConfig
    cfg = RceConfig({"extract": {
        "corners": ["typ", "slow"], "temperature": "25",
        "corner_temperatures": {"slow": "125"},
    }}, tmp_path / "run.toml")
    assert cfg.corner_temperatures() == ("25", "125")
