"""Deterministic TOML scalar and corner-bundle encoding."""

from __future__ import annotations

from .model_entries import CornerExport


def _toml_string(value: object) -> str:
    import json

    return json.dumps(str(value), ensure_ascii=True)


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if value is None:
        raise TypeError("TOML has no null scalar")
    return _toml_string(value)


def _append_corner_toml(lines: list[str], value: CornerExport, temperature: str, prefix: str = "") -> None:
    # A scalar is emitted before any child tables so it belongs to its cell/process.
    lines.append(f"temperature_mode = {_toml_string(temperature)}")
    header = prefix + ".corner_export" if prefix else "corner_export"
    lines.extend(("", f"[{header}]", f"mode = {_toml_string(value.mode)}", f"variable = {_toml_string(value.variable)}"))
    for profile in value.profiles:
        lines.extend(("", f"[[{header}.profiles]]", f"name = {_toml_string(profile.name)}"))
        for model in profile.models:
            lines.extend(("", f"[[{header}.profiles.models]]",
                          f"file = {_toml_string(model.file)}", f"section = {_toml_string(model.section)}",
                          f"label = {_toml_string(model.label)}", f"enabled = {_toml_scalar(model.enabled)}"))
