"""MCP handler for measurement, AC, and waveform domain tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ac_response import call_response
from .ac_response_schema import RESPONSE_NAMES
from .ac_schema import AC_NAMES
from .ac_tools import call_ac
from .measurement_catalog import CATALOG_NAMES, call_catalog
from .result_contract import ResultError
from .result_tools import RESULT_NAMES, call_result_tool
from .waveform_schema import WAVEFORM_NAMES
from .waveform_spec_schema import SPEC_NAMES
from .waveform_specs import call_waveform_specs
from .waveform_tools import call_waveform


MEASUREMENT_NAMES = frozenset(
    CATALOG_NAMES | RESPONSE_NAMES | AC_NAMES | SPEC_NAMES | WAVEFORM_NAMES | RESULT_NAMES
)


def dispatch_measurement(
    name: str,
    arguments: dict[str, Any],
    *,
    workspace: Path | None,
    client: Any,
) -> tuple[bool, dict[str, Any]]:
    """Dispatch one measurement-family tool with its stable error mapping."""
    if name in CATALOG_NAMES:
        try:
            detail = call_catalog(name, arguments)
        except ResultError as exc:
            return False, {
                "ok": False, "code": "measurement_catalog_error", "message": str(exc)
            }
        return True, detail

    if name in RESPONSE_NAMES:
        try:
            detail = call_response(name, arguments, workspace=workspace)
        except (ResultError, OSError) as exc:
            return False, {
                "ok": False, "code": "ac_response_error", "message": str(exc)
            }
        return True, detail

    if name in AC_NAMES:
        try:
            detail = call_ac(name, arguments, workspace=workspace, client=client)
        except (ResultError, OSError) as exc:
            return False, {
                "ok": False, "code": "ac_waveform_error", "message": str(exc)
            }
        return True, detail

    if name in SPEC_NAMES:
        try:
            detail = call_waveform_specs(name, arguments, workspace=workspace)
        except (ResultError, OSError) as exc:
            return False, {
                "ok": False, "code": "waveform_spec_error", "message": str(exc)
            }
        return True, detail

    if name in WAVEFORM_NAMES:
        try:
            detail = call_waveform(name, arguments, workspace=workspace, client=client)
        except (ResultError, OSError) as exc:
            return False, {
                "ok": False, "code": "waveform_error", "message": str(exc)
            }
        return True, detail

    if name in RESULT_NAMES:
        try:
            detail = call_result_tool(name, arguments, workspace=workspace, client=client)
        except (ResultError, OSError) as exc:
            return False, {
                "ok": False, "code": "measurement_results_error", "message": str(exc)
            }
        return True, detail

    raise ValueError(f"unknown measurement tool: {name}")


__all__ = ["MEASUREMENT_NAMES", "dispatch_measurement"]
