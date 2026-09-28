"""Convert the authoritative provider snapshot to safe editable defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence
from .errors import RequestValidationError
from .model_design import SourceDesign
from .model_entries import ModelEntry
from .model_options import SimulatorOption
from .defaults_values import _normalize_named_options
from .defaults_result import DefaultsReport, SourceDefaults


def _option_value_text(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "" if value is None else str(value)


def _option_type(value: Any, choices: Sequence[Any]) -> str:
    if choices:
        return "enum"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "real"
    return "string"


def _option_entry(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, (list, tuple)) and len(item) >= 2 for item in value
    ):
        return {str(item[0]): item[1] for item in value}
    return {"value": value}


def _effective_option_names(
    baseline: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> set[str]:
    """Return options changed by source initialization or optional setup files."""

    before = _normalize_named_options(baseline.get("simulator_options", {}))
    after = _normalize_named_options(snapshot.get("simulator_options", {}))
    changed: set[str] = set()
    before_folded = {name.casefold(): raw for name, raw in before.items()}
    for name, raw_entry in after.items():
        current = _option_entry(raw_entry).get("value")
        previous_entry = before_folded.get(name.casefold())
        previous = (
            _option_entry(previous_entry).get("value")
            if previous_entry is not None
            else None
        )
        if current != previous:
            changed.add(name.casefold())
    return changed


def source_defaults_from_report(
    report: DefaultsReport,
    source: SourceDesign,
) -> SourceDefaults:
    """Convert the final ASI snapshot into editable GUI values.

    The source worker records both the post-design/libInit snapshot and the
    post-startup/simrc snapshot.  The latter is the effective value presented
    to the user; it is identical to the former when no optional files load.
    """

    value = source.validate()
    # ``normalize_snapshot({})`` intentionally retains an empty, schema-shaped
    # mapping.  Select the post-startup snapshot only when it actually
    # contains values; otherwise retain the post-design/libInit defaults.
    startup = report.after_startup_simrc
    design = report.after_design
    has_startup_values = any(
        startup.get(name)
        for name in ("model_files", "environment_options", "simulator_options")
    )
    snapshot = startup if has_startup_values else design
    changed_option_names = _effective_option_names(report.baseline, snapshot)
    raw_models = snapshot.get("model_files", ())
    models: list[ModelEntry] = []
    for row in raw_models if isinstance(raw_models, (list, tuple)) else ():
        if not isinstance(row, Mapping):
            continue
        file_name = str(row.get("resolved_file") or row.get("file") or "").strip()
        if not file_name:
            continue
        models.append(
            ModelEntry(
                Path(file_name),
                str(row.get("section", "")),
                enabled=bool(row.get("enabled", True)),
            )
        )

    raw_options = _normalize_named_options(snapshot.get("simulator_options", {}))
    options: list[SimulatorOption] = []
    process_values: dict[str, str] = {}
    if isinstance(raw_options, Mapping):
        option_items = raw_options.items()
    elif isinstance(raw_options, (list, tuple)):
        option_items = (
            (entry[0], entry[1])
            for entry in raw_options
            if isinstance(entry, (list, tuple)) and len(entry) >= 2
        )
    else:
        option_items = ()
    for raw_name, raw_entry in option_items:
        name = str(raw_name)
        if report.provider == "mae_test" and name.endswith(":"):
            name = name[:-1]
        entry = _option_entry(raw_entry)
        raw_value = entry.get("value")
        if raw_value is None or (report.provider == "mae_test" and raw_value == ""):
            continue
        choices_value = entry.get("choices", ()) or ()
        choices = tuple(str(item) for item in choices_value) if isinstance(
            choices_value, (list, tuple)
        ) else ()
        text = _option_value_text(raw_value)
        canonical = name.casefold()
        if canonical in {"temp", "temperature", "tempdc"} and "temp" not in process_values:
            process_values["temp"] = text
            continue
        if canonical == "scale" and "scale" not in process_values:
            process_values["scale"] = text
            continue
        if canonical == "gmin" and "gmin" not in process_values:
            process_values["gmin"] = text
            continue
        # A Maestro test is already a user-authored setup.  Include every
        # populated test option, even when it happens to equal the ASI tool's
        # baseline value; filtering by baseline would hide valid test state.
        if report.provider != "mae_test" and canonical not in changed_option_names:
            continue
        # Skip complex values that cannot be represented by the safe typed
        # option editor. They remain available in the raw report for audit.
        if isinstance(raw_value, (list, tuple, dict)):
            continue
        try:
            option = SimulatorOption(
                name,
                text,
                _option_type(raw_value, choices),
                enum_values=choices,
            ).validate(report.dialect)
        except RequestValidationError:
            continue
        options.append(option)

    env_options = _normalize_named_options(snapshot.get("environment_options", {}))
    if isinstance(env_options, Mapping):
        for name, raw_value in env_options.items():
            if raw_value is None or (report.provider == "mae_test" and raw_value == ""):
                continue
            canonical = str(name).rstrip(":").casefold()
            if canonical in {"temp", "temperature", "tempdc"}:
                process_values.setdefault("temp", _option_value_text(raw_value))
            elif canonical == "scale":
                process_values.setdefault("scale", _option_value_text(raw_value))
            elif canonical == "gmin":
                process_values.setdefault("gmin", _option_value_text(raw_value))

    diagnostics = tuple(report.diagnostics) + tuple(
        f"{report.provider.upper()} API: {item}" for item in report.api_errors
    )
    return SourceDefaults(
        provider=report.provider,
        dialect=report.dialect,
        source=value,
        models=tuple(models),
        simulator_options=tuple(options),
        temp_text=process_values.get("temp", ""),
        scale_text=process_values.get("scale", ""),
        gmin_text=process_values.get("gmin", ""),
        diagnostics=diagnostics,
        run_dir=report.run_dir,
        report_path=report.report_path,
        process=report.process,
    )


def defaults_report_to_source_defaults(
    report: DefaultsReport, source: SourceDesign
) -> SourceDefaults:
    """Compatibility spelling for callers migrating from report-level APIs."""

    return source_defaults_from_report(report, source)
