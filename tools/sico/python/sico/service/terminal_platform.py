"""Host platform discovery for the SiCo terminal runtime selection.

Lives outside the UI package so the desktop widgets keep their no-file-IO
boundary; the Qt terminal tool calls these helpers with plain values.
"""

from __future__ import annotations

from sicoenv import read as environment_setting

import os
from pathlib import Path
from typing import Mapping

PLATFORMS = ("rhel7", "rhel8")
RHEL_FAMILIES = (
    "rhel",
    "redhat",
    "centos",
    "rocky",
    "almalinux",
    "oracle",
    "cloudlinux",
    "anolis",
)


def platform_name(
    environment: Mapping[str, str] | None = None,
    os_release: Path | str = Path("/etc/os-release"),
) -> str | None:
    """Return ``rhel7`` or ``rhel8`` the way the terminal dispatcher selects it."""

    source = os.environ if environment is None else environment
    requested = environment_setting(source, "SICO_AI_PLATFORM", "").strip()
    if requested:
        return requested if requested in PLATFORMS else None
    try:
        text = Path(os_release).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            values[name.strip()] = value.strip().strip("\"'")
    family = f"{values.get('ID', '')} {values.get('ID_LIKE', '')}".lower()
    if not any(token in family for token in RHEL_FAMILIES):
        return None
    major = values.get("VERSION_ID", "").split(".")[0]
    if major == "7":
        return "rhel7"
    if major in ("8", "9"):
        return "rhel8"
    return None
