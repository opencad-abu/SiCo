"""Validate report schema and bind provider/source identity to its request."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence
from .errors import RequestValidationError
from .model_design import SourceDesign
from .model_validation import SIMULATORS
from .defaults_request import MaeSetup
from .defaults_result import DefaultsReport
from .defaults_values import DEFAULTS_PROVIDERS, DEFAULTS_SCHEMA_VERSION, _normalise, _safe_text
from .defaults_snapshot import normalize_snapshot


def parse_defaults_report(
    path: str | Path,
    *,
    source: SourceDesign | None = None,
    dialect: str | None = None,
    provider: str | None = None,
    mae_setup: MaeSetup | None = None,
    base_dirs: Sequence[str | Path] = (),
) -> DefaultsReport:
    """Read and validate one private defaults report.

    ``provider`` and ``mae_setup`` bind a report to the request that spawned
    its worker.  They are optional for backward-compatible offline report
    inspection, but the workflow always supplies both expected values.
    """

    expected_provider = None if provider is None else str(provider)
    if expected_provider is not None and expected_provider not in DEFAULTS_PROVIDERS:
        raise RequestValidationError(
            f"unsupported expected defaults provider: {expected_provider!r}"
        )
    expected_mae_setup = None if mae_setup is None else mae_setup.validate()
    if expected_mae_setup is not None:
        if expected_provider is None:
            expected_provider = "mae_test"
        elif expected_provider != "mae_test":
            raise RequestValidationError(
                "expected Maestro setup requires provider='mae_test'"
            )
    elif expected_provider == "mae_test":
        raise RequestValidationError(
            "expected MAE provider requires an explicit Maestro setup/test"
        )

    report_path = Path(path).expanduser().resolve()
    if not report_path.is_file() or report_path.stat().st_size == 0:
        raise RequestValidationError(f"defaults report is missing or empty: {report_path}")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RequestValidationError(f"invalid defaults report: {report_path}") from exc
    if not isinstance(payload, Mapping):
        raise RequestValidationError("defaults report root must be an object")
    allowed_root = {
        "schema_version",
        "status",
        "dialect",
        "tool_name",
        "provider",
        "mae_setup",
        "source",
        "baseline",
        "after_design",
        "after_startup_simrc",
        "diagnostics",
        "api_errors",
    }
    unknown = sorted(str(key) for key in payload if str(key) not in allowed_root)
    if unknown:
        raise RequestValidationError(
            "unknown defaults report field(s): " + ", ".join(unknown)
        )
    try:
        version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise RequestValidationError("defaults report schema_version must be an integer") from exc
    if version != DEFAULTS_SCHEMA_VERSION:
        raise RequestValidationError(f"unsupported defaults report schema_version: {version}")
    status = str(payload.get("status", ""))
    if status not in {"succeeded", "failed"}:
        raise RequestValidationError(f"invalid defaults report status: {status!r}")
    report_dialect = str(payload.get("dialect", ""))
    if report_dialect not in SIMULATORS:
        raise RequestValidationError(f"invalid defaults report dialect: {report_dialect!r}")
    if dialect is not None and report_dialect != str(dialect):
        raise RequestValidationError("defaults report dialect does not match request")
    report_provider = str(payload.get("provider", "asi_initialization"))
    if report_provider not in DEFAULTS_PROVIDERS:
        raise RequestValidationError(
            f"invalid defaults report provider: {report_provider!r}"
        )
    if expected_provider is not None and report_provider != expected_provider:
        raise RequestValidationError("defaults report provider does not match request")
    raw_mae_setup = payload.get("mae_setup")
    if report_provider == "mae_test":
        if not isinstance(raw_mae_setup, Mapping):
            raise RequestValidationError("MAE defaults report requires mae_setup")
        allowed_mae_setup = {
            "library",
            "cell",
            "view",
            "test",
            "history",
            "application",
        }
        unknown_mae_setup = sorted(
            str(key)
            for key in raw_mae_setup
            if str(key) not in allowed_mae_setup
        )
        if unknown_mae_setup:
            raise RequestValidationError(
                "unknown defaults report mae_setup field(s): "
                + ", ".join(unknown_mae_setup)
            )
        try:
            report_mae_setup = MaeSetup(
                str(raw_mae_setup.get("library", "")),
                str(raw_mae_setup.get("cell", "")),
                str(raw_mae_setup.get("view", "maestro")),
                str(raw_mae_setup.get("test", "")),
                raw_mae_setup.get("history"),
                raw_mae_setup.get("application"),
            ).validate()
        except RequestValidationError:
            raise
        raw_mae_setup = report_mae_setup.to_dict()
        if (
            expected_mae_setup is not None
            and raw_mae_setup != expected_mae_setup.to_dict()
        ):
            raise RequestValidationError(
                "defaults report Maestro setup does not match request"
            )
    elif raw_mae_setup is not None:
        raise RequestValidationError("mae_setup is only valid for provider='mae_test'")
    raw_source = payload.get("source", {})
    if not isinstance(raw_source, Mapping):
        raise RequestValidationError("defaults report source must be an object")
    source_keys = {"cds_lib", "library", "cell", "view"}
    unknown_source = sorted(str(key) for key in raw_source if str(key) not in source_keys)
    if unknown_source:
        raise RequestValidationError(
            "unknown defaults source field(s): " + ", ".join(unknown_source)
        )
    for key in ("library", "cell", "view"):
        raw_value = raw_source.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise RequestValidationError(f"defaults report source.{key} must be set")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_value):
            raise RequestValidationError(
                f"defaults report source.{key} contains a control character"
            )
    if "cds_lib" in raw_source:
        raw_cds = raw_source["cds_lib"]
        if not isinstance(raw_cds, str) or not Path(raw_cds).expanduser().is_absolute():
            raise RequestValidationError("defaults report source.cds_lib must be absolute")
    if source is not None:
        expected = source.validate()
        for key, expected_value in {
            "cds_lib": str(expected.cds_lib),
            "library": expected.library,
            "cell": expected.cell,
            "view": expected.view,
        }.items():
            if key not in raw_source:
                raise RequestValidationError(f"defaults report source missing: {key}")
            if str(raw_source[key]) != expected_value:
                raise RequestValidationError(f"defaults report source mismatch: {key}")
    diagnostics = payload.get("diagnostics", ()) or ()
    api_errors = payload.get("api_errors", ()) or ()
    if not isinstance(diagnostics, (list, tuple)) or not isinstance(api_errors, (list, tuple)):
        raise RequestValidationError("defaults diagnostics/api_errors must be arrays")
    if not all(isinstance(item, str) for item in (*diagnostics, *api_errors)):
        raise RequestValidationError("defaults diagnostics/api_errors must contain strings")
    if status == "succeeded" and api_errors:
        raise RequestValidationError(
            "succeeded defaults report must not contain API errors"
        )
    for label in ("baseline", "after_design", "after_startup_simrc"):
        snapshot = payload.get(label, {})
        if not isinstance(snapshot, Mapping):
            raise RequestValidationError(f"defaults report {label} must be an object")
        unknown_snapshot = sorted(
            str(key)
            for key in snapshot
            if str(key)
            not in {
                "model_files",
                "raw_model_files",
                "model_files_source",
                "option_scope",
                "session_available",
                "environment_options",
                "simulator_options",
            }
        )
        if unknown_snapshot:
            raise RequestValidationError(
                f"unknown defaults {label} field(s): " + ", ".join(unknown_snapshot)
            )
    return DefaultsReport(
        status=status,
        dialect=report_dialect,
        tool_name=str(payload.get("tool_name", report_dialect)),
        source={str(key): _normalise(value) for key, value in raw_source.items()},
        baseline=normalize_snapshot(payload.get("baseline", {}), base_dirs=base_dirs),
        after_design=normalize_snapshot(payload.get("after_design", {}), base_dirs=base_dirs),
        after_startup_simrc=normalize_snapshot(
            payload.get("after_startup_simrc", {}), base_dirs=base_dirs
        ),
        provider=report_provider,
        mae_setup=(None if raw_mae_setup is None else dict(raw_mae_setup)),
        diagnostics=tuple(_safe_text(item, "defaults diagnostic") for item in diagnostics),
        api_errors=tuple(_safe_text(item, "defaults API error") for item in api_errors),
        schema_version=version,
        report_path=report_path,
    )


read_defaults_report = parse_defaults_report


def defaults_report_to_json(report: DefaultsReport) -> str:
    """Serialize a parsed report without embedding process objects."""

    return json.dumps(report.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
