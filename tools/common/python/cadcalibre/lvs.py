"""Pure helpers shared by Calibre LVS and xRC control-file generation."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def _quote(value: str) -> str:
    return str(value).replace('"', '\\"')


def _unquote_name(name: str) -> str:
    value = name.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def virtual_connect_lines(mode: str, names: Iterable[str] = ()) -> list[str]:
    """Render Calibre virtual-connect statements from normalized settings."""
    # Legacy SKILL list output may contain extra spaces or line breaks.
    mode = mode.strip()
    if mode.startswith("(") and mode.endswith(")"):
        mode = "(" + " ".join(mode[1:-1].split()) + ")"
    if mode not in {"", "(nil nil)", "(t nil)", "(nil t)", "(t t)"}:
        raise ValueError(f"Unsupported Calibre virtual_connect setting: {mode!r}")
    lines: list[str] = []
    if mode in {"(t nil)", "(t t)"}:
        lines.append("VIRTUAL CONNECT COLON YES")
    if mode in {"(nil t)", "(t t)"}:
        normalized = [_unquote_name(name) for name in names]
        if not normalized or any(not name for name in normalized):
            raise ValueError(
                "Calibre Virtual Connect by Name is enabled but no net-name "
                "pattern was provided"
            )
        rendered = " ".join(f'"{_quote(name)}"' for name in normalized)
        lines.append(f"VIRTUAL CONNECT NAME {rendered}")
    return lines


def append_custom_svrf(
    text: str, *, enabled: bool, command: str
) -> str:
    """Append enabled user SVRF verbatim to a generated control file."""
    if not enabled or not command:
        return text
    return text.rstrip("\n") + "\n\n" + command + (
        "" if command.endswith("\n") else "\n"
    )


def hcell_arguments(path: Path | None) -> list[str]:
    """Render the optional Calibre ``-hcell`` command arguments."""
    return ["-hcell", str(path)] if path is not None else []
