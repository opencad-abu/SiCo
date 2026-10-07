"""Normalize a recipe contract and its optional canonical point table."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .pvt_contract import PVTContractError, normalize_physical_pvt_matrix


def normalize_contract_with_optional_points(
    value: object,
    cases: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Return one canonical contract, rejecting a mismatched point projection."""

    if not isinstance(value, Mapping):
        raise PVTContractError("physical PVT contract must be an object")
    raw = dict(value)
    supplied_points = raw.pop("points", None)
    normalized = normalize_physical_pvt_matrix(raw, cases)
    if supplied_points is not None:
        if not isinstance(supplied_points, list) or supplied_points != normalized["points"]:
            raise PVTContractError("physical PVT canonical points do not match dimensions")
    return normalized


__all__ = ["normalize_contract_with_optional_points"]
