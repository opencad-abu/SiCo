"""Parse authoritative units captured from a Virtuoso technology database.

The ASCII techfile is not authoritative. Call SICO_tech_units.il in Virtuoso
and parse its output with :func:`parse_skill_probe`.
"""

from __future__ import annotations

import re

from .jsonio import fail

UNIT_METERS = {"micron": 1e-6, "inch": 0.0254}


def _number(text, name):
    try:
        return float(text)
    except (TypeError, ValueError):
        fail("tech.db " + name + " is not numeric")


def parse_skill_probe(text):
    """Parse the key/value dump produced by SICO_tech_units.il."""
    if not isinstance(text, str):
        fail("tech.db probe must be text")
    view = re.search(
        r'viewTypeUnits=\(\(\s*"maskLayout"\s+"([^"]+)"\s+(\d+)\s*\)',
        text,
    )
    dbu = re.search(r"dbuPerUU=([0-9.]+)", text)
    user = re.search(r'userUnit="([^"]+)"', text)
    grid = re.search(r"mfgGrid=([0-9.]+)", text)
    if not (view and dbu and user and grid):
        fail("tech.db probe is incomplete")
    user_unit = user.group(1)
    if user_unit not in UNIT_METERS:
        fail("unsupported tech.db user unit: " + user_unit)
    dbu_per_uu = int(dbu.group(1).split(".")[0])
    if dbu_per_uu <= 0:
        fail("tech.db dbuPerUU must be positive")
    stream_unit_m = UNIT_METERS[user_unit]
    database_unit = float(f"{stream_unit_m / dbu_per_uu:.15g}")
    mfg_grid_um = _number(grid.group(1), "mfgGrid")
    return {
        "source": "tech_db_skill",
        "oa_view_type": "maskLayout",
        "user_unit": user_unit,
        "dbu_per_uu": dbu_per_uu,
        "mfg_grid_um": mfg_grid_um,
        "grid_um": mfg_grid_um,
        "stream_unit_m": stream_unit_m,
        "stream_precision_m": database_unit,
        "database_unit": database_unit,
        "dbu_per_um": dbu_per_uu,
    }


def validate_tech_units(units):
    """Validate and normalize a tech.db units mapping."""
    if not isinstance(units, dict):
        fail("tech.db units must be an object")
    if "grid_um" not in units and "mfg_grid_um" in units:
        units = dict(units)
        units["grid_um"] = units["mfg_grid_um"]
    for key in ("stream_unit_m", "stream_precision_m", "database_unit",
                "dbu_per_um", "grid_um"):
        value = units.get(key)
        if (type(value) not in (int, float) or isinstance(value, bool)
                or value <= 0):
            fail("tech.db units." + key + " must be positive")
    if abs(float(units["stream_precision_m"]) - float(units["database_unit"])) > 1e-18:
        fail("tech.db stream_precision_m must equal database_unit")
    expected = float(units["stream_unit_m"]) / float(units["database_unit"])
    if abs(expected - float(units["dbu_per_um"])) > 1e-9:
        fail("tech.db dbu_per_um disagrees with stream_unit_m/database_unit")
    return dict(units)


__all__ = ["UNIT_METERS", "parse_skill_probe", "validate_tech_units"]
