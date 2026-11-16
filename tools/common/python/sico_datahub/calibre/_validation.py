"""Shared bounded-value validation for Calibre DataHub records."""

from __future__ import annotations

import math

from ..codec import ArtifactRef
from ..errors import HubError
from ..limits import check_text, check_value


def text(value, field_name, *, empty=False, limit=4096):
    return check_text(value, field_name, empty=empty, limit=limit)


def count(value, field_name, *, allow_zero=True):
    if type(value) is not int or value < 0 or (not allow_zero and value == 0):
        raise HubError("invalid_arguments", "Invalid " + field_name)
    return value


def digest(value, field_name="dependency digest"):
    if (
        not isinstance(value, str) or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise HubError("invalid_arguments", "Invalid " + field_name)
    return value


def artifact(value, field_name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, ArtifactRef):
        raise HubError("invalid_arguments", "Invalid " + field_name)
    return value


def bbox(value):
    if value is None:
        return None
    if (
        not isinstance(value, tuple) or len(value) != 2
        or any(not isinstance(point, tuple) or len(point) != 2 for point in value)
        or any(type(number) not in {int, float} for point in value for number in point)
    ):
        raise HubError("invalid_arguments", "Invalid DRC violation bbox")
    try:
        if not all(math.isfinite(float(number)) for point in value for number in point):
            raise HubError("invalid_arguments", "Invalid DRC violation bbox")
    except OverflowError as exc:
        raise HubError("invalid_arguments", "Invalid DRC violation bbox") from exc
    return value


def mapping(value, field_name):
    if not isinstance(value, dict):
        raise HubError("invalid_arguments", field_name + " must be an object")
    check_value(value)
    return value


__all__ = ["artifact", "bbox", "count", "digest", "mapping", "text"]
