"""Render a read-only defaults worker call from an explicit request."""

from __future__ import annotations

import json
from pathlib import Path
from .errors import RequestValidationError
from .model_design import SourceDesign
from .model_validation import SIMULATORS
from .defaults_request import MaeSetup
from .defaults_values import DEFAULTS_OUTPUT_ENV, DEFAULTS_PROVIDERS


def _skill_string(value: str) -> str:
    text = str(value)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise RequestValidationError("probe string contains a control character")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _skill_symbol(value: str) -> str:
    text = str(value)
    if not text or not (text[0].isalpha() or text[0] == "_") or not all(
        ch.isalnum() or ch == "_" for ch in text
    ):
        raise RequestValidationError(f"invalid simulator name: {value!r}")
    return "'" + text


def render_defaults_probe_script(
    source: SourceDesign,
    dialect: str,
    *,
    project_dir: str | Path,
    result_dir: str | Path,
    output_path: str | Path | None = None,
    provider: str = "asi_initialization",
    mae_setup: MaeSetup | None = None,
) -> str:
    """Render a read-only OCEAN defaults probe.

    A baseline snapshot is taken after the simulator tool is initialized and
    before ``design`` opens the source view.  The next snapshot captures PDK
    callbacks; optional startup/simrc files are then loaded before the final
    snapshot.  No ``modelFile``, ``temp``, ``option``, ``createNetlist`` or
    ``run`` call is emitted by this probe.
    """

    value = source.validate()
    sim = str(dialect)
    if sim not in SIMULATORS:
        raise RequestValidationError(f"unsupported simulator dialect: {sim!r}")
    selected_provider = str(provider)
    if selected_provider not in DEFAULTS_PROVIDERS:
        raise RequestValidationError(f"unsupported defaults provider: {selected_provider!r}")
    selected_mae = None
    if selected_provider == "mae_test":
        if mae_setup is None:
            raise RequestValidationError(
                "MAE provider requires an explicit Maestro setup/test"
            )
        selected_mae = mae_setup.validate()
    elif mae_setup is not None:
        raise RequestValidationError(
            "Maestro setup is only valid with provider='mae_test'"
        )
    project = Path(project_dir).expanduser().resolve()
    result = Path(result_dir).expanduser().resolve()
    report_path = (  # noqa: F841 - preserve existing output-path validation
        _skill_string(str(Path(output_path).expanduser().resolve()))
        if output_path is not None
        else f'getShellEnvVar("{DEFAULTS_OUTPUT_ENV}")'
    )
    # Setup identity is request data, not a runtime value.
    report_suffix = ""
    if selected_mae is not None:
        mae_json = json.dumps(
            selected_mae.to_dict(), ensure_ascii=True, separators=(",", ":")
        )
        report_suffix += ',"mae_setup":' + mae_json
    report_suffix += "}"

    # This is deliberately plain SKILL rather than a dependency on json.cxt:
    # OCEAN replay may not load that context until netlisting, and probing must
    # remain read-only.  Association lists are represented as JSON objects;
    # all other lists become JSON arrays.
    from cadcontext import worker_call
    setup = selected_mae
    return worker_call(
        "mtsRuntimeDefaultsMae" if setup else "mtsRuntimeDefaultsAsi",
        sim, project, result, value.cds_lib, value.library, value.cell, value.view,
        value.startup_file, value.simrc, output_path,
        setup.library if setup else None, setup.cell if setup else None,
        setup.view if setup else None, setup.test if setup else None,
        setup.application if setup else None, setup.history if setup else None,
        report_suffix,
    ) + "exit()\n"
