"""Render validated profile data as inert Cadence SKILL literals."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Mapping

from cadgui.transfer import atomic_publish_text

from .model import ProfileDocument, ProfileValue, load_profile


_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+\Z")


def skill_string(value: str) -> str:
    if any(
        (ord(character) < 32 and character not in "\n\t")
        or ord(character) == 127
        for character in value
    ):
        raise ValueError("SKILL profile strings cannot contain control characters")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def skill_value(value: ProfileValue) -> str:
    if isinstance(value, bool):
        return "t" if value else "nil"
    if isinstance(value, str):
        return skill_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, tuple):
        return "list(" + " ".join(skill_value(item) for item in value) + ")"
    raise TypeError(f"Unsupported normalized profile type: {type(value).__name__}")


def render_form_data(document: ProfileDocument) -> str:
    entries = [
        f"  list({skill_string(path)} {skill_value(value)})"
        for path, value in document.entries
    ]
    return (
        f"SICO_profileSetLoadData({skill_string(document.flow)} "
        f"{document.version} list(\n"
        + "\n".join(entries)
        + "\n))\n"
    )


def write_form_data(
    document: ProfileDocument, path: str | Path, *, refuse_existing: bool = True
) -> Path:
    output = Path(path).expanduser()
    published = atomic_publish_text(
        output,
        render_form_data(document),
        refuse_existing=refuse_existing,
    )
    os.chmod(published, 0o600)
    return published


def _toml_key(value: str) -> str:
    return value if _BARE_KEY.fullmatch(value) else skill_string(value)


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\b", "\\b")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported profile publish type: {type(value).__name__}")


def render_profile_toml(document: ProfileDocument) -> str:
    lines = [
        "[cad_config]",
        'format = "sico-flow-profile"',
        f"version = {document.version}",
        f"flow = {_toml_string(document.flow)}",
    ]

    def append_table(table: Mapping[str, Any], path: tuple[str, ...]) -> None:
        scalars = []
        children = []
        for key, value in table.items():
            (children if isinstance(value, Mapping) else scalars).append((key, value))
        if path:
            lines.extend(("", "[" + ".".join(_toml_key(key) for key in path) + "]"))
        for key, value in scalars:
            lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
        for key, value in children:
            append_table(value, (*path, str(key)))

    for key, value in document.raw.items():
        if key == "cad_config":
            continue
        if not isinstance(value, Mapping):
            raise TypeError(f"Top-level profile value must be a table: {key}")
        append_table(value, (str(key),))
    return "\n".join(lines) + "\n"


def publish_profile(document: ProfileDocument, path: str | Path) -> Path:
    output = Path(path).expanduser()
    published = atomic_publish_text(
        output,
        render_profile_toml(document),
        refuse_existing=False,
    )
    os.chmod(published, 0o600)
    return published


def publish_profile_source(
    source: str | Path, path: str | Path, *, flow: str
) -> Path:
    """Validate, normalize, and atomically publish a profile source file."""

    return publish_profile(load_profile(source, flow), path)
