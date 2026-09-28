"""Differential profile/request acceptance for the shared field contract."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import warnings

import pytest

from cadconfig.booleans import FLOW_BOOLEAN_PATHS, BOOLEAN_DEFAULTS
from cadprofile import model
from lefpy import config as lef_config
from rcepy.config import RceConfig, load_config
from rcepy.config_legacy import adapt_legacy, LegacyConfigWarning


FIELDS = [(flow, field) for flow, fields in FLOW_BOOLEAN_PATHS.items()
          for field in sorted(fields)]


def profile_values(flow, tmp_path):
    raw = {
        "cad_config": {"format": "sico-flow-profile", "version": 1, "flow": flow},
        "run": {"root": "runs"}, "input": {},
    }
    if flow == "LEF":
        raw.update(abstract={}, output={"technology": True})
        raw["input"] = {"library": "demo", "cell": "top"}
    else:
        raw["runtime"] = {}
        raw["input"] = {"type": "OA"}
        raw["drc" if flow == "DRC" else "lvs"] = {}
        if flow == "RCE":
            raw["extract"] = {"output_type": "dspf", "corner": "typ", "tool": "QRC"}
    return raw


def put(raw, field, value):
    parts = field.split(".")
    for part in parts[:-1]:
        raw = raw.setdefault(part, {})
    raw[parts[-1]] = value


def read_profile(raw, tmp_path, monkeypatch):
    source = tmp_path / "profile.toml"
    source.touch()
    monkeypatch.setattr(model.tomllib, "load", lambda handle: raw)
    return model.load_profile(source, raw["cad_config"]["flow"])


def read_request(raw, tmp_path, monkeypatch):
    request = {key: value for key, value in raw.items() if key != "cad_config"}
    source = tmp_path / "request.toml"
    source.touch()
    if raw["cad_config"]["flow"] != "LEF":
        monkeypatch.setattr(model.tomllib, "load", lambda handle: request)
        return load_config(source)
    (tmp_path / "cds.lib").touch()
    (tmp_path / "options").touch()
    request["run"] = {"run_dir": "runs", "cds_lib": "cds.lib", **request["run"]}
    request["abstract"] = {"options_file": "options", **request["abstract"]}
    request["output"] = {"lef_file": "top.lef", **request["output"]}
    monkeypatch.setattr(lef_config.tomllib, "load", lambda handle: request)
    return lef_config.load_config(source)


LEF_ATTRIBUTES = {
    "steps.pins": "run_pins", "steps.extract": "run_extract",
    "steps.abstract": "run_abstract", "output.geometry": "export_geometry",
    "output.technology": "export_technology",
}


@pytest.mark.parametrize("flow,field", FIELDS)
@pytest.mark.parametrize("value", [True, False])
def test_shared_booleans_have_identical_values(flow, field, value, tmp_path, monkeypatch):
    raw = profile_values(flow, tmp_path)
    put(raw, field, value)
    document = read_profile(raw, tmp_path, monkeypatch)
    request = read_request(raw, tmp_path, monkeypatch)
    actual = getattr(request, LEF_ATTRIBUTES[field]) if flow == "LEF" else request.flag(*field.split("."))
    assert document.as_dict()[field] is actual is value


@pytest.mark.parametrize("flow,field", FIELDS)
@pytest.mark.parametrize("value", ["true", "false", "typo", "", 0, 1, None, [], {}])
def test_shared_booleans_reject_loose_values_at_both_boundaries(
    flow, field, value, tmp_path, monkeypatch,
):
    raw = profile_values(flow, tmp_path)
    put(raw, field, value)
    for read in (read_profile, read_request):
        with pytest.raises(ValueError, match="must be true or false"):
            read(raw, tmp_path, monkeypatch)


@pytest.mark.parametrize("flow,field", FIELDS)
def test_shared_boolean_defaults(flow, field, tmp_path, monkeypatch):
    raw = profile_values(flow, tmp_path)
    raw.get(field.split(".")[0], {}).pop(field.split(".")[1], None)
    document = read_profile(raw, tmp_path, monkeypatch)
    request = read_request(raw, tmp_path, monkeypatch)
    actual = getattr(request, LEF_ATTRIBUTES[field]) if flow == "LEF" else request.flag(*field.split("."))
    assert document.as_dict()[field] is actual is BOOLEAN_DEFAULTS[field]


@pytest.mark.parametrize("flow,field", [
    ("DRC", "runtime.cpus"), ("LVS", "runtime.lvs_cpus"),
    ("RCE", "runtime.ext_cpus"), ("LEF", "run.cpus"),
    ("RCE", "batch.parallel_cells"),
])
@pytest.mark.parametrize("value", ["04", 4, "0", -1, True, "typo", "\u00b2", None])
def test_shared_positive_integer_fields(flow, field, value, tmp_path, monkeypatch):
    raw = profile_values(flow, tmp_path)
    put(raw, field, value)
    if value in ("04", 4) and type(value) is not bool:
        document = read_profile(raw, tmp_path, monkeypatch)
        request = read_request(raw, tmp_path, monkeypatch)
        actual = request.cpus if flow == "LEF" else request.text(*field.split("."))
        assert document.as_dict()[field] == actual == "4"
    else:
        for read in (read_profile, read_request):
            with pytest.raises(ValueError, match="positive integer"):
                read(raw, tmp_path, monkeypatch)


def test_runtime_values_cannot_escape_or_change_after_validation(tmp_path):
    raw = {"lvs": {"case_sensitive": False}, "selection": {"nets": ["VDD"]},
           "vendor": {"nested": [{"option": [1, 2]}]}}
    cfg = RceConfig(raw, tmp_path / "run.toml")
    raw["lvs"]["case_sensitive"] = "typo"
    raw["selection"]["nets"].append("VSS")
    for view in (cfg.raw, cfg.section(), cfg.section("lvs"), cfg.get("lvs"),
                 cfg.section("missing"), cfg.get("missing", default={}),
                 cfg.get("vendor", "nested")[0]):
        with pytest.raises(TypeError):
            view["option"] = False
    with pytest.raises(AttributeError):
        cfg.get("selection", "nets").append("VSS")
    with pytest.raises(FrozenInstanceError):
        cfg.config_path = tmp_path / "other.toml"
    projection = cfg.to_dict()
    projection["vendor"]["nested"][0]["option"].append(3)
    assert cfg.get("vendor", "nested")[0]["option"] == (1, 2)
    assert cfg.items("selection", "nets") == ["VDD"]
    assert cfg.lvs_case_sensitive() is False
    changed = cfg.replace("lvs", "case_sensitive", value=True)
    assert changed.lvs_case_sensitive() is True and cfg.lvs_case_sensitive() is False
    with pytest.raises(ValueError, match="true or false"):
        cfg.replace("lvs", "case_sensitive", value="typo")


def test_profile_raw_is_detached_and_readonly(tmp_path, monkeypatch):
    raw = profile_values("DRC", tmp_path)
    raw["drc"]["rule_select_groups"] = ["M1"]
    document = read_profile(raw, tmp_path, monkeypatch)
    raw["drc"]["rule_select_groups"].append("M2")
    assert document.raw["drc"]["rule_select_groups"] == ("M1",)
    with pytest.raises(TypeError):
        document.raw["drc"]["rule_select_enable"] = True


@pytest.mark.parametrize("value", ["yes", "nil", "on", 0, 1])
def test_legacy_is_explicit_and_diagnostic(tmp_path, value):
    raw = {"lvs": {"case_sensitive": value}}
    with pytest.raises(ValueError, match="true or false"):
        RceConfig(raw, tmp_path / "run.toml")
    with pytest.warns(LegacyConfigWarning, match="lvs.case_sensitive"):
        converted = adapt_legacy(raw)
    cfg = RceConfig(converted, tmp_path / "run.toml")
    assert type(cfg.lvs_case_sensitive()) is bool
    assert raw["lvs"]["case_sensitive"] == value


@pytest.mark.parametrize("value", ["typo", None, 2, {}, []])
def test_legacy_never_defaults_unknown_booleans(tmp_path, value):
    with pytest.raises(ValueError, match="legacy boolean"):
        adapt_legacy({"lvs": {"case_sensitive": value}})


def test_load_legacy_config_and_enum_conversion(tmp_path):
    source = tmp_path / "run.toml"
    source.write_text('[lvs]\ncase_sensitive = "no"\n[netlist]\ndspf_remove_instances = true\n')
    with pytest.raises(ValueError, match="true or false"):
        load_config(source)
    with pytest.warns(LegacyConfigWarning) as caught:
        cfg = load_config(source, legacy=True)
    assert len(caught) == 2
    assert cfg.lvs_case_sensitive() is False
    assert cfg.text("netlist", "dspf_remove_instances") == "TRUE"
    with warnings.catch_warnings(record=True) as caught:
        adapt_legacy({"lvs": {"case_sensitive": True}})
    assert not caught


def test_request_format_keeps_vendor_tables_and_rejects_profile_metadata(tmp_path):
    cfg = RceConfig({"vendor": {"option": ["custom"]}}, tmp_path / "run.toml")
    assert cfg.get("vendor", "option") == ("custom",)
    with pytest.raises(ValueError, match="identifies a profile"):
        RceConfig(profile_values("DRC", tmp_path), tmp_path / "run.toml")


def test_vendor_scalars_are_inert_and_known_sections_require_tables(tmp_path):
    cfg = RceConfig({"LVS_IGNORE_ERROR": "TRUE"}, tmp_path / "run.toml")
    assert cfg.flag("lvs", "ignore_error") is False
    for adapt in (lambda raw: RceConfig(raw, tmp_path / "run.toml"), adapt_legacy):
        with pytest.raises(ValueError, match="section lvs must be a table"):
            adapt({"lvs": "bad"})
    with pytest.raises(ValueError, match="non-table field"):
        cfg.replace("LVS_IGNORE_ERROR", "invalid", value=True)


def test_vendor_toml_datetime_values_remain_supported(tmp_path):
    source = tmp_path / "run.toml"
    source.write_text("[vendor]\nday = 2026-09-18\nat = 2026-09-18T12:00:00Z\n")
    cfg = load_config(source)
    assert cfg.get("vendor", "day").isoformat() == "2026-09-18"
    assert cfg.to_dict()["vendor"]["at"].year == 2026


@pytest.mark.parametrize("package,commands", [
    ("rcepy", ("run", "generate", "prepare")),
    ("drcpy", ("run", "generate", "report")),
    ("lvspy", ("run", "generate", "report", "stream-gds", "export-cdl")),
])
def test_flow_cli_exposes_explicit_legacy_switch(package, commands):
    from importlib import import_module
    parser = import_module(package + ".cli").build_parser()
    for command in commands:
        assert parser.parse_args([command, "run.toml"]).legacy_config is False
        assert parser.parse_args([command, "run.toml", "--legacy-config"]).legacy_config is True
