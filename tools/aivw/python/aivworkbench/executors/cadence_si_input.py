"""Cadence SI input environment rendering and strict comparison."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

_SI_ASSIGNMENT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_SI_IDENTITY_KEYS = ("simLibName", "simCellName", "simViewName", "simSimulator", "simNetlistHier")
_SI_RUNTIME_KEY = re.compile(r"^(?:sim|hnl|verilogSim|vlogif|vtools|nl)[A-Za-z0-9_]*$")

def _render_si_env(library: str, cell: str, view: str) -> str:
    return (
        f'simLibName = "{library}"\n'
        f'simCellName = "{cell}"\n'
        f'simViewName = "{view}"\n'
        'simSimulator = "verilog"\n'
        "simNetlistHier = t\n"
    )

def _compare_si_env(
    input_path: Path,
    active_path: Path,
    *,
    library: str,
    cell: str,
    view: str,
) -> tuple[bool, dict[str, Any]]:
    """Compare SI's post-run file semantically, allowing Cadence defaults.

    IC23.1 documents that ``si.env`` may be augmented or normalized as a run is
    initialized.  The active file is therefore not required to remain byte
    identical.  The design identity assignments are always strict; additions,
    removals, and value changes are accepted only for Cadence's documented
    runtime namespaces and are retained in evidence for audit.
    """
    input_values, input_errors, input_duplicates = _parse_si_env(input_path)
    active_values, active_errors, active_duplicates = _parse_si_env(active_path)
    expected = {
        "simLibName": f'"{library}"',
        "simCellName": f'"{cell}"',
        "simViewName": f'"{view}"',
        "simSimulator": '"verilog"',
        "simNetlistHier": "t",
    }
    identity = {
        key: {
            "expected": expected[key],
            "input": input_values.get(key),
            "active": active_values.get(key),
            "match": input_values.get(key) == expected[key]
            and active_values.get(key) == expected[key],
        }
        for key in _SI_IDENTITY_KEYS
    }
    identity_unchanged = all(item["match"] for item in identity.values())
    input_keys = set(input_values)
    active_keys = set(active_values)
    changed_keys = sorted(
        key
        for key in input_keys & active_keys
        if key not in _SI_IDENTITY_KEYS and input_values[key] != active_values[key]
    )
    added_keys = sorted(active_keys - input_keys)
    removed_keys = sorted(input_keys - active_keys)
    unexpected_keys = sorted(
        key
        for key in (*added_keys, *removed_keys, *changed_keys)
        if not _SI_RUNTIME_KEY.fullmatch(key)
    )
    return identity_unchanged, {
        "valid": not input_errors and not active_errors,
        "input_parse_errors": input_errors,
        "active_parse_errors": active_errors,
        "input_duplicate_keys": input_duplicates,
        "active_duplicate_keys": active_duplicates,
        "identity": identity,
        "added_keys": added_keys,
        "removed_keys": removed_keys,
        "changed_keys": changed_keys,
        "unexpected_keys": unexpected_keys,
    }

def _parse_si_env(path: Path) -> tuple[dict[str, str], list[str], list[str]]:
    values: dict[str, str] = {}
    errors: list[str] = []
    duplicate_keys: list[str] = []
    if not path.is_file() or path.is_symlink():
        return values, [f"not a regular file: {path}"], duplicate_keys
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        return values, [f"cannot read {path}: {exc}"], duplicate_keys
    for line_number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _SI_ASSIGNMENT.fullmatch(line)
        if match is None:
            errors.append(f"line {line_number}: unsupported assignment")
            continue
        key, value = match.groups()
        if key in values:
            if values[key] == value:
                duplicate_keys.append(key)
                continue
            errors.append(f"line {line_number}: conflicting duplicate key {key}")
            continue
        values[key] = value
    return values, errors, sorted(set(duplicate_keys))
