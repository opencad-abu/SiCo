"""defaults snapshot cases regressions."""

from __future__ import annotations
import json
from pathlib import Path
from mtsnetlistor.defaults import (
    MaeSetup,
    normalize_model_files,
    parse_defaults_report,
    source_defaults_from_report,
)
from defaults_fixtures import (
    _source,
    _report,
    _report_with_snapshot,
)


def test_report_normalizes_disabled_models_and_preserves_process_text(tmp_path: Path) -> None:
    source = _source(tmp_path)
    report = parse_defaults_report(_report(tmp_path), source=source, dialect="spectre")
    result = source_defaults_from_report(report, source)
    assert result.models[0].file == Path("/tmp/models.scs")
    assert result.models[0].enabled is False
    assert result.temp_text == "27.000"
    assert result.scale_text == "1e-6"
    assert result.gmin_text == "1e-12"
    assert result.simulator_options[0].name == "reltol"
    assert all(option.name != "maxwarns" for option in result.simulator_options)


def test_model_files_resolve_relative_entries_and_preserve_raw_marker(tmp_path: Path) -> None:
    model = tmp_path / "model.scs"
    model.write_text("// model\n", encoding="utf-8")
    rows = normalize_model_files([[f"#{model.name}", "ss"]], base_dirs=(tmp_path,))
    assert rows[0]["enabled"] is False
    assert rows[0]["raw_file"] == f"#{model.name}"
    assert rows[0]["resolved_file"] == str(model.resolve())


def test_mae_include_paths_resolve_relative_model_files(tmp_path: Path) -> None:
    include = tmp_path / "pdk" / "models" / "spectre"
    include.mkdir(parents=True)
    model = include / "corner.scs"
    model.write_text("// model\n", encoding="utf-8")
    snapshot = {
        "model_files": [["corner.scs", "tt"]],
        "environment_options": [
            ["includePath", "./pdk/models/spectre"],
            ["allIncludedPaths", [[True, "./pdk/models/spectre"]]],
        ],
        "simulator_options": {},
    }
    report = parse_defaults_report(
        _report_with_snapshot(tmp_path, snapshot), base_dirs=(tmp_path,)
    )
    row = report.after_design["model_files"][0]
    assert row["resolved_file"] == str(model.resolve())


def test_disabled_mae_all_included_path_is_not_used(tmp_path: Path) -> None:
    include = tmp_path / "disabled" / "spectre"
    include.mkdir(parents=True)
    (include / "corner.scs").write_text("// model\n", encoding="utf-8")
    snapshot = {
        "model_files": [["corner.scs", "tt"]],
        "environment_options": [
            ["allIncludedPaths", [[False, "./disabled/spectre"]]],
        ],
        "simulator_options": {},
    }
    report = parse_defaults_report(
        _report_with_snapshot(tmp_path, snapshot), base_dirs=(tmp_path,)
    )
    row = report.after_design["model_files"][0]
    assert "resolved_file" not in row


def test_report_accepts_scope_and_raw_model_audit_fields(tmp_path: Path) -> None:
    source = _source(tmp_path)
    path = _report(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["after_design"]["raw_model_files"] = payload["after_design"]["model_files"]
    payload["after_design"]["model_files_source"] = "modelFiles"
    payload["after_design"]["option_scope"] = "session"
    payload["after_design"]["session_available"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = parse_defaults_report(path, source=source, dialect="spectre")
    assert report.after_design["model_files_source"] == "modelFiles"
    assert report.after_design["session_available"] is True
    assert report.after_design["raw_model_files"][0]["enabled"] is False
    assert report.after_design["raw_model_files"][0]["raw_file"] == "#/tmp/models.scs"


def test_mae_source_defaults_skip_include_empty_values(tmp_path: Path) -> None:
    source = _source(tmp_path)
    payload = json.loads(_report(tmp_path).read_text(encoding="utf-8"))
    payload["provider"] = "mae_test"
    payload["mae_setup"] = {
        "library": "ade",
        "cell": "setup",
        "view": "maestro",
        "test": "nominal",
    }
    payload["baseline"] = {}
    payload["after_design"] = {
        "model_files": [],
        "environment_options": {"temp": "27", "scale": "1.0", "unused": ""},
        "simulator_options": {
            "reltol:": "1e-3",
            "emptyOption:": "",
            "enabled:": False,
        },
    }
    payload["after_startup_simrc"] = {}
    path = tmp_path / "mae-populated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    defaults = source_defaults_from_report(
        parse_defaults_report(
            path,
            source=source,
            provider="mae_test",
            mae_setup=MaeSetup("ade", "setup", test="nominal"),
        ),
        source,
    )
    assert defaults.temp_text == "27"
    assert defaults.scale_text == "1.0"
    assert [option.name for option in defaults.simulator_options] == [
        "reltol",
        "enabled",
    ]
    assert defaults.simulator_options[1].value == "false"
