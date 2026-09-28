"""StarRC common-option loading and override validation."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Iterator


_CASE_SENSITIVE_SETTINGS = {
    "BLOCK",
    "OA_CDLOUT_RUNDIR",
    "OA_CELL_NAME",
    "OA_DEVICE_MAPPING_FILE",
    "OA_LAYER_MAPPING_FILE",
    "OA_LIB_DEF",
    "OA_LIB_NAME",
    "OA_PORT_ANNOTATION_VIEW",
    "OA_PROPERTY_ANNOTATION_VIEW",
    "OA_VIEW_NAME",
}


def load_common_options(path: Path) -> tuple[list[str], Path | None]:
    if not path.is_file():
        return [], None
    return (
        path.read_text(encoding="utf-8", errors="replace").splitlines(),
        path,
    )


def _normalized_setting(command: str, value: str, *, base_dir: Path) -> str:
    if command == "SPICE_SUBCKT_FILE":
        # File names are case sensitive and relative to the StarRC run directory.
        # Compare the full resolved path, including spaces, rather than the first
        # uppercased token used for enum-valued commands.
        filename = value.strip()
        if filename.startswith(('"', "'")):
            tokens = shlex.split(filename, comments=False, posix=True)
            if len(tokens) != 1:
                raise ValueError("SPICE_SUBCKT_FILE requires one pin-order file")
            filename = tokens[0]
        if not filename:
            return ""
        path = Path(os.path.expandvars(filename)).expanduser()
        return str((path if path.is_absolute() else base_dir / path).resolve())
    if command in _CASE_SENSITIVE_SETTINGS:
        return " ".join(value.strip().split())
    tokens = value.strip().upper().split()
    if not tokens:
        return ""
    if command == "EXTRA_GEOMETRY_INFO":
        return " ".join(sorted(tokens))
    return tokens[0]


def _include_path(raw_value: str, base_dir: Path) -> Path:
    try:
        values = shlex.split(raw_value, comments=False, posix=True)
    except ValueError as exc:
        raise ValueError(f"Invalid StarRC INCLUDE_FILE value: {raw_value!r}") from exc
    if len(values) != 1:
        raise ValueError(
            "StarRC INCLUDE_FILE requires exactly one file name, got: "
            f"{raw_value!r}"
        )
    path = Path(os.path.expandvars(values[0])).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _option_records(
    option_lines: list[str],
    *,
    include_base: Path,
    source: Path,
    stack: tuple[Path, ...],
) -> Iterator[tuple[str, Path]]:
    for raw_line in option_lines:
        line = raw_line.strip()
        if not line or line.startswith("*"):
            continue
        raw_command, separator, raw_value = line.partition(":")
        command = raw_command.strip().upper()
        if separator and command == "INCLUDE_FILE":
            include_file = _include_path(raw_value, include_base)
            if include_file in stack:
                chain = " -> ".join(str(path) for path in (*stack, include_file))
                raise ValueError(f"Cyclic StarRC INCLUDE_FILE chain: {chain}")
            if not include_file.is_file():
                raise FileNotFoundError(
                    f"Cannot access StarRC INCLUDE_FILE referenced by {source}: "
                    f"{include_file}"
                )
            included_lines = include_file.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            yield from _option_records(
                included_lines,
                include_base=include_base,
                source=include_file,
                stack=(*stack, include_file),
            )
            continue
        yield raw_line, source


def validate_parasitic_overrides(
    common_options: list[str],
    requirements: dict[str, str],
    *,
    include_base: Path,
    common_opt: Path | None,
    purpose: str = "StarRC parasitic netlist options",
) -> None:
    if not requirements or common_opt is None:
        return

    # common.opt is appended last, so its final scalar setting wins.
    common_settings: dict[str, tuple[str, Path]] = {}
    root = common_opt.resolve()
    records = _option_records(
        common_options,
        include_base=include_base.resolve(),
        source=root,
        stack=(root,),
    )
    for raw_line, source in records:
        line = raw_line.strip()
        raw_command, separator, raw_value = line.partition(":")
        if not separator:
            continue
        command = raw_command.strip().upper()
        if command in requirements:
            common_settings[command] = (
                _normalized_setting(command, raw_value, base_dir=include_base),
                source,
            )

    for command, required_value in requirements.items():
        actual = common_settings.get(command)
        actual_value = actual[0] if actual else None
        normalized_required = _normalized_setting(
            command, required_value, base_dir=include_base
        )
        if actual_value is not None and actual_value != normalized_required:
            source = actual[1]
            raise ValueError(
                f"{purpose} require "
                f"{command}: {required_value}, but the corner common.opt "
                f"overrides it via {source} with {command}: {actual_value}"
            )


def reject_cumulative_common_commands(
    common_options: list[str],
    commands: set[str],
    *,
    include_base: Path,
    common_opt: Path | None,
    purpose: str,
) -> None:
    """Reject cumulative commands that would silently alter a user selection."""
    if not commands or common_opt is None:
        return

    forbidden = {command.upper() for command in commands}
    root = common_opt.resolve()
    records = _option_records(
        common_options,
        include_base=include_base.resolve(),
        source=root,
        stack=(root,),
    )
    for raw_line, source in records:
        raw_command, separator, _ = raw_line.strip().partition(":")
        command = raw_command.strip().upper()
        if separator and command in forbidden:
            raise ValueError(
                f"{purpose} cannot be combined with cumulative {command} "
                f"from the corner common.opt via {source}"
            )
