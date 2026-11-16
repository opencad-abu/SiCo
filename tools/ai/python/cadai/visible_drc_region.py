"""Validate and render an explicit user-unit export region."""

from __future__ import annotations

import math


def region(value):
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(type(item) not in {int, float} for item in value)
    ):
        raise ValueError("region_user_units must contain four numbers")
    values = [float(item) for item in value]
    if (
        not all(math.isfinite(item) for item in values)
        or values[2] <= values[0]
        or values[3] <= values[1]
    ):
        raise ValueError("region_user_units must be a positive ordered bbox")
    if any(abs(item) > 1e9 for item in values):
        raise ValueError("region_user_units exceeds its coordinate limit")
    return values


def skill_region(values):
    return "list(" + " ".join(repr(item) for item in values) + ")"


__all__ = ["region", "skill_region"]
