"""Display logo resolution shared by standalone CAD applications."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from sicoresources import icon as resource_icon


FALLBACK_LOGO = "SiCo"
_LEGACY_LOGO_VALUES = ("OCAD",)


def logo_text(environ: Mapping[str, str] | None = None) -> str:
    """Return the configured display logo.

    ``LOGO`` wins over ``COMPANY``.  Unset values, empty strings, and the
    legacy ``OCAD`` placeholder are ignored, so an unconfigured environment
    reads ``SiCo``.  This mirrors the SKILL ``SICO_logo`` resolution used by
    the menu and form banners.
    """

    values = os.environ if environ is None else environ
    for name in ("LOGO", "COMPANY"):
        value = values.get(name, "")
        if value is None:
            continue
        value = str(value).strip()
        if value and value.upper() not in _LEGACY_LOGO_VALUES:
            return value
    return FALLBACK_LOGO


def logo_path() -> "Path | None":
    """Site/product brand icon; None when the installation ships no logo."""

    return resource_icon("brand", "logo.png")
