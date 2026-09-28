"""Flow-specific profile validation rules."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .schema import _REDUCTION_OUTPUT_TAG
from .values import ProfileError, _require_table, _optional_text

def _decimal_text(value: Any, path: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{path} must be a finite numeric string")
    try:
        number = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ProfileError(f"{path} must be a finite numeric string") from exc
    if not number.is_finite():
        raise ProfileError(f"{path} must be a finite numeric string")
    return number


def _validate_run_mode(table: Mapping[str, Any], path: str) -> None:
    mode = table.get("run_mode", "Hier")
    if mode not in {"Hier", "Flat"}:
        raise ProfileError(f"{path}.run_mode must be 'Hier' or 'Flat'")


def _validate_drc(raw: Mapping[str, Any]) -> None:
    drc = _require_table(raw, "drc")
    _validate_run_mode(drc, "drc")
    for key in ("rule_select_groups", "rule_select_checks"):
        value = drc.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise ProfileError(f"drc.{key} must be an array of strings")


def _validate_lvs(raw: Mapping[str, Any]) -> None:
    lvs = _require_table(raw, "lvs")
    _validate_run_mode(lvs, "lvs")
    query = lvs.get("svdb_query")
    if query is not None:
        if not isinstance(query, list) or not all(
            isinstance(item, str) and item.strip() for item in query
        ):
            raise ProfileError(
                "lvs.svdb_query must be an array of non-empty strings"
            )
        allowed = {
            "CCI",
            "SI",
            "RECON",
            "IXF",
            "NXF",
            "SLPH",
            "IXF+NXF+SLPH",
            "IXF NXF SLPH",
            "PINLOC",
        }
        for item in query:
            normalized = item.strip().upper()
            if normalized not in allowed:
                raise ProfileError(
                    "Unsupported lvs.svdb_query option "
                    f"{item!r}; expected one of the SVDB query choices"
                )


def _validate_rce(raw: Mapping[str, Any]) -> None:
    extract = _require_table(raw, "extract")
    view = extract.get("view", {})
    if view is None:
        view = {}
    if not isinstance(view, Mapping):
        raise ProfileError("extract.view must be a table")
    for key in ("library", "cell", "name"):
        value = view.get(key)
        if value is not None and not isinstance(value, str):
            raise ProfileError(f"extract.view.{key} must be a string")
        if isinstance(value, str) and value and (
            any(character.isspace() for character in value) or "/" in value
        ):
            raise ProfileError(
                f"extract.view.{key} must be a single OA name without spaces or '/'"
            )
    library = str(view.get("library", "")).strip()
    cell = str(view.get("cell", "")).strip()
    if bool(library) != bool(cell):
        raise ProfileError(
            "extract.view.library and extract.view.cell must be specified together"
        )
    corners = extract.get("corners")
    corner = extract.get("corner")
    temperatures = extract.get("corner_temperatures")
    if corners is not None:
        if not isinstance(corners, list) or not all(
            isinstance(item, str) and item.strip() for item in corners
        ):
            raise ProfileError("extract.corners must be an array of non-empty strings")
        if len(set(corners)) != len(corners):
            raise ProfileError("extract.corners must not contain duplicates")
    if temperatures is not None:
        if not isinstance(temperatures, list) or not all(
            isinstance(item, str) for item in temperatures
        ):
            raise ProfileError(
                "extract.corner_temperatures must be an array of strings"
            )
        if isinstance(corners, list) and len(temperatures) != len(corners):
            raise ProfileError(
                "extract.corner_temperatures must contain one value per corner"
            )
    scope = extract.get("corner_scope")
    if scope is None:
        scope = "Single Corner"
    if scope == "Multiple Corners" and (not isinstance(corners, list) or not corners):
        raise ProfileError(
            "extract.corners must contain at least one corner for Multiple Corners"
        )
    if scope == "Single Corner" and isinstance(corners, list) and len(corners) > 1:
        raise ProfileError(
            "extract.corners must contain at most one corner for Single Corner"
        )
    if (
        scope == "Single Corner"
        and isinstance(corners, list)
        and len(corners) == 1
        and isinstance(corner, str)
        and corner.strip()
        and corner.casefold() != corners[0].casefold()
    ):
        raise ProfileError(
            "extract.corner must match the selected extract.corners entry"
        )
    if not isinstance(corners, list) or not corners:
        if not isinstance(corner, str) or not corner.strip():
            raise ProfileError(
                "extract.corners must contain a valid corner, or extract.corner "
                "must name one"
            )

    reduction = raw.get("reduction")
    if not isinstance(reduction, Mapping):
        return

    output_tag = reduction.get("output_tag", "reduced")
    if not isinstance(output_tag, str) or not _REDUCTION_OUTPUT_TAG.fullmatch(
        output_tag
    ):
        raise ProfileError(
            "reduction.output_tag must contain only letters, digits, and '_'"
        )

    bounds = (
        ("control", "0.5", Decimal(0), Decimal(1), True),
        ("delay_rel", "0.05", Decimal(0), Decimal(1), True),
        ("delay_abs", "1e-12", Decimal(0), None, True),
        ("frequency", "20", Decimal(0), None, False),
    )
    for key, default, minimum, maximum, minimum_inclusive in bounds:
        number = _decimal_text(reduction.get(key, default), f"reduction.{key}")
        if number < minimum or (not minimum_inclusive and number == minimum):
            operator = ">=" if minimum_inclusive else ">"
            raise ProfileError(f"reduction.{key} must be {operator} {minimum}")
        if maximum is not None and number > maximum:
            raise ProfileError(f"reduction.{key} must be <= {maximum}")

    temperature = reduction.get("temperature", "")
    if temperature:
        _decimal_text(temperature, "reduction.temperature")

    if not reduction.get("enabled", False):
        return

    if scope == "Multiple Corners" or (
        isinstance(corners, list) and len(corners) > 1
    ):
        raise ProfileError("Reduction is disabled for Multiple Corners")

    output_type = str(extract.get("output_type", "dspf")).strip().casefold()
    tool = str(extract.get("tool", "QRC")).strip().casefold()
    configured_kind = (
        str(view.get("kind", "Smart View")).strip().casefold()
        if isinstance(view, Mapping)
        else "smart view"
    )
    if output_type == "smartview":
        native_kind = "smart"
    elif output_type == "extview":
        native_kind = "extracted"
    else:
        native_kind = configured_kind
    file_output = output_type in {"dspf", "spf", "sp", "spice", "hspice"}
    native_output = (
        output_type in {"view", "smartview", "extview"}
        and tool == "qrc"
        and native_kind in {"smart", "smart view", "extracted", "extracted view"}
    )
    if not (file_output or native_output):
        raise ProfileError(
            "Enabled reduction requires DSPF/SPICE output or a Quantus "
            "Smart/Extracted View"
        )

    smart_view = native_output and native_kind in {"smart", "smart view"}
    if temperature and output_type not in {"dspf", "spf"} and not smart_view:
        raise ProfileError(
            "reduction.temperature is supported only for DSPF and Smart View"
        )
    canonical = reduction.get("canonical_device_file", "")
    if canonical and output_type not in {"sp", "spice", "hspice"}:
        raise ProfileError(
            "reduction.canonical_device_file is supported only for SPICE output"
        )
    if (
        reduction.get("mode", "Default") == "Selection File"
        and not str(reduction.get("selection_file", "")).strip()
    ):
        raise ProfileError(
            "reduction.selection_file is required for Selection File mode"
        )


def _validate_lef(raw: Mapping[str, Any]) -> None:
    run = _require_table(raw, "run")
    inp = _require_table(raw, "input")
    output = _require_table(raw, "output")
    root = _optional_text(run, "root", "run.root")
    if root is None:
        raise ProfileError("LEF profile requires run.root")
    sources = sum(
        inp.get(key) is not None for key in ("cell", "cells", "cell_list_file")
    )
    if sources != 1:
        raise ProfileError(
            "Exactly one of input.cell, input.cells, or input.cell_list_file is required"
        )
    if "cells" in inp:
        cells = inp["cells"]
        if not isinstance(cells, list) or not cells or not all(
            isinstance(item, str) and item.strip() for item in cells
        ):
            raise ProfileError("input.cells must be a non-empty array of strings")
    if not (output.get("geometry", True) or output.get("technology", False)):
        raise ProfileError("At least one LEF output data type must be enabled")
