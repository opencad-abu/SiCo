"""defaults report cases regressions."""

from __future__ import annotations
import json
from pathlib import Path
import pytest
from mtsnetlistor.defaults import (
    DefaultsProbeRequest,
    MaeSetup,
    parse_defaults_report,
    source_defaults_from_report,
)
from mtsnetlistor.errors import RequestValidationError
from defaults_fixtures import (
    _source,
    _report,
)


def test_report_rejects_unknown_fields_and_source_mismatch(tmp_path: Path) -> None:
    path = _report(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["unexpected"] = True
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RequestValidationError, match="unknown defaults report"):
        parse_defaults_report(path)

    path = _report(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["source"]["cell"] = "other"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RequestValidationError, match="source mismatch"):
        parse_defaults_report(path, source=_source(tmp_path))


def test_probe_request_validates_dialect(tmp_path: Path) -> None:
    with pytest.raises(RequestValidationError, match="unsupported simulator"):
        DefaultsProbeRequest(_source(tmp_path), "unknown").validate()


def test_mae_setup_requires_explicit_test_and_validates_identity() -> None:
    with pytest.raises(RequestValidationError, match="Maestro test must be set"):
        MaeSetup("ade", "setup", test="").validate()
    value = MaeSetup("ade", "setup", "maestro", "tests:inv:1", history="Interactive.1")
    assert value.validate().to_dict()["test"] == "tests:inv:1"


def test_mae_report_round_trip_preserves_provider_and_setup(tmp_path: Path) -> None:
    source = _source(tmp_path)
    payload = json.loads(_report(tmp_path).read_text(encoding="utf-8"))
    payload["provider"] = "mae_test"
    payload["mae_setup"] = {
        "library": "ade",
        "cell": "setup",
        "view": "maestro",
        "test": "setup:inv:1",
    }
    path = tmp_path / "mae-defaults.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = parse_defaults_report(path, source=source, dialect="spectre")
    assert report.provider == "mae_test"
    assert report.mae_setup["test"] == "setup:inv:1"
    assert source_defaults_from_report(report, source).provider == "mae_test"


def test_report_rejects_provider_or_mae_setup_mismatch(tmp_path: Path) -> None:
    source = _source(tmp_path)
    payload = json.loads(_report(tmp_path).read_text(encoding="utf-8"))
    payload["provider"] = "mae_test"
    payload["mae_setup"] = {
        "library": "ade",
        "cell": "setup",
        "view": "maestro",
        "test": "nominal",
    }
    path = tmp_path / "mae-mismatch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RequestValidationError, match="provider does not match"):
        parse_defaults_report(
            path,
            source=source,
            dialect="spectre",
            provider="asi_initialization",
        )
    with pytest.raises(RequestValidationError, match="Maestro setup does not match"):
        parse_defaults_report(
            path,
            source=source,
            dialect="spectre",
            provider="mae_test",
            mae_setup=MaeSetup("ade", "setup", "maestro", "different"),
        )


def test_report_rejects_unknown_mae_setup_fields(tmp_path: Path) -> None:
    payload = json.loads(_report(tmp_path).read_text(encoding="utf-8"))
    payload["provider"] = "mae_test"
    payload["mae_setup"] = {
        "library": "ade",
        "cell": "setup",
        "view": "maestro",
        "test": "nominal",
        "unexpected": "value",
    }
    path = tmp_path / "mae-unknown.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RequestValidationError, match="unknown defaults report mae_setup"):
        parse_defaults_report(path)


def test_report_rejects_success_status_with_api_errors(tmp_path: Path) -> None:
    path = _report(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["api_errors"] = ["maeOpenSetup returned nil"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RequestValidationError, match="must not contain API errors"):
        parse_defaults_report(path)
