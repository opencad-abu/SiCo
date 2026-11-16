"""Rewrite a foundry fill deck for ASCII geometry and a selected region."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .profile import FillProfile, ProfileError


class DeckError(ValueError):
    """The fill deck cannot be rewritten safely."""


@dataclass(frozen=True)
class DeckRequest:
    """Inputs for one generated Calibre fill run."""

    layout_path: Path
    top_cell: str
    output_rdb: Path
    summary_path: Path
    selected_layers: tuple[str, ...]
    region: tuple[float, float, float, float] | None = None
    dm_on_corner: bool | None = None
    with_sealring: bool | None = None


@dataclass(frozen=True)
class DeckResult:
    """Generated deck text and the checks used before execution."""

    text: str
    encrypted_sha256: str
    removed_check_maps: int


_DEFINE = re.compile(r"^(\s*)(//\s*)?#DEFINE\s+([A-Za-z0-9_]+)(.*)$")
_THICKNESS = re.compile(r"^\s*(?://\s*)?#DEFINE\s+([0-9A-Z_]+_THICK_M([0-9]+))\b")
_VARIABLE = re.compile(r"^(\s*VARIABLE\s+(xLB|yLB|xRT|yRT)\s+)(\S+)(.*)$")
_CHECK_MAP = re.compile(r"^\s*DRC\s+CHECK\s+MAP\s+(\S+)\b")
_LAYER_NUMBER = re.compile(r"^M([0-9]+)$")


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _define(line: str, name: str, enabled: bool) -> str | None:
    if line.endswith("\r\n"):
        body, ending = line[:-2], "\r\n"
    elif line.endswith("\n"):
        body, ending = line[:-1], "\n"
    else:
        body, ending = line, ""
    match = _DEFINE.match(body)
    if match is None or match.group(3) != name:
        return None
    return (
        match.group(1)
        + ("" if enabled else "//")
        + "#DEFINE "
        + name
        + match.group(4)
        + ending
    )


def _replace_pattern(line: str, pattern: str, replacement: str) -> str | None:
    match = re.match(pattern, line, flags=re.IGNORECASE)
    if match is None:
        return None
    return match.group(1) + replacement + match.group(2)


def _encrypted_text(lines: list[str]) -> str:
    selected: list[str] = []
    inside = False
    for line in lines:
        if line.strip().startswith("#DECRYPT"):
            inside = True
        if inside:
            selected.append(line)
        if line.strip().startswith("#ENDCRYPT"):
            inside = False
    return "".join(selected)


def _layer_number(name: str) -> int | None:
    match = _LAYER_NUMBER.fullmatch(name)
    return int(match.group(1)) if match else None


def rewrite_deck(profile: FillProfile, source: str, request: DeckRequest) -> DeckResult:
    """Return a deck with ASCII output, selected layers, and an optional region."""
    if not request.selected_layers:
        raise DeckError("at least one metal layer must be selected")
    selected = tuple(dict.fromkeys(request.selected_layers))
    try:
        rules = profile.require_layers(selected)
    except ProfileError as exc:
        raise DeckError(str(exc)) from exc
    if not request.top_cell.strip():
        raise DeckError("top cell must be non-empty")
    if request.region is not None:
        x1, y1, x2, y2 = request.region
        if not (x1 < x2 and y1 < y2):
            raise DeckError("fill region must have x1 < x2 and y1 < y2")
    dm_on_corner = (
        profile.dm_on_corner
        if request.dm_on_corner is None
        else request.dm_on_corner
    )
    with_sealring = (
        profile.with_sealring
        if request.with_sealring is None
        else request.with_sealring
    )

    original = source.splitlines(keepends=True)
    encrypted_before = _encrypted_text(original)
    lines: list[str] = []
    inside_encrypted = False
    found_fill_defines: set[str] = set()
    found_thickness: set[str] = set()
    removed_maps = 0
    maximum_results_seen = False
    for line in original:
        stripped = line.strip()
        if stripped.startswith("#DECRYPT"):
            inside_encrypted = True
        if not inside_encrypted:
            if stripped.startswith("DRC MAXIMUM RESULTS"):
                maximum_results_seen = True
            replaced = _replace_pattern(
                line,
                r'^(\s*LAYOUT\s+PATH\s+).*?(\s*(?://.*)?\r?\n?)$',
                _quote(str(request.layout_path)),
            )
            if replaced is not None:
                line = replaced
            replaced = _replace_pattern(
                line,
                r'^(\s*LAYOUT\s+PRIMARY\s+).*?(\s*(?://.*)?\r?\n?)$',
                _quote(request.top_cell),
            )
            if replaced is not None:
                line = replaced
            replaced = _replace_pattern(
                line,
                r'^(\s*DRC\s+RESULTS\s+DATABASE\s+).*?(\s*(?://.*)?\r?\n?)$',
                _quote(str(request.output_rdb)) + " ASCII",
            )
            if replaced is not None:
                line = replaced
            replaced = _replace_pattern(
                line,
                r'^(\s*DRC\s+SUMMARY\s+REPORT\s+).*?(\s*(?://.*)?\r?\n?)$',
                _quote(str(request.summary_path)),
            )
            if replaced is not None:
                line = replaced
            map_match = _CHECK_MAP.match(line)
            if map_match is not None and map_match.group(1) in profile.rules:
                removed_maps += 1
                continue
            for layer in profile.layers.values():
                changed = _define(line, layer.fill_define, layer.name in selected)
                if changed is not None:
                    found_fill_defines.add(layer.name)
                    line = changed
                    break
            for layer in profile.layers.values():
                if layer.thickness_define is None:
                    continue
                number = _layer_number(layer.name)
                thick = _THICKNESS.match(line)
                if number is None or thick is None or int(thick.group(2)) != number:
                    continue
                enabled = layer.name in selected and thick.group(1) == layer.thickness_define
                line = _define(line, thick.group(1), enabled) or line
                found_thickness.add(thick.group(1))
            if request.region is not None:
                changed = _define(line, "ChipWindowUsed", True)
                if changed is not None:
                    line = changed
                changed = _define(line, "UseprBoundary", False)
                if changed is not None:
                    line = changed
                variable_line = line.rstrip("\r\n")
                ending = line[len(variable_line):]
                variable = _VARIABLE.match(variable_line)
                if variable is not None:
                    value = {
                        "xLB": request.region[0],
                        "yLB": request.region[1],
                        "xRT": request.region[2],
                        "yRT": request.region[3],
                    }[variable.group(2)]
                    line = variable.group(1) + f"{value:g}" + variable.group(4) + ending
            else:
                changed = _define(line, "ChipWindowUsed", False)
                if changed is not None:
                    line = changed
            if dm_on_corner is not None:
                changed = _define(line, "dmOnCorner", dm_on_corner)
                if changed is not None:
                    line = changed
            if with_sealring is not None:
                changed = _define(line, "WithSealring", with_sealring)
                if changed is not None:
                    line = changed
        lines.append(line)
        if stripped.startswith("#ENDCRYPT"):
            inside_encrypted = False

    missing = [
        layer.fill_define
        for layer in rules
        if layer.name not in found_fill_defines
    ]
    if missing:
        raise DeckError("fill deck is missing layer switch: " + ", ".join(missing))
    for layer in rules:
        if layer.thickness_define and layer.thickness_define not in found_thickness:
            raise DeckError(
                f"fill deck is missing thickness switch {layer.thickness_define}"
            )
    if not maximum_results_seen:
        lines.insert(
            1,
            "DRC MAXIMUM RESULTS ALL\n",
        )
    encrypted_after = _encrypted_text(lines)
    if encrypted_after != encrypted_before:
        raise DeckError("encrypted deck body changed during rewrite")
    digest = hashlib.sha256(encrypted_after.encode("utf-8")).hexdigest()
    return DeckResult("".join(lines), digest, removed_maps)
