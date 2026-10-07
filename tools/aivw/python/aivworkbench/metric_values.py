"""Normalize and freeze metric evidence values."""

from __future__ import annotations
import math
from typing import Any, Mapping

from .metric_errors import LDOMetricError


class _FrozenDict(dict):
    """JSON-compatible mapping that rejects mutation at every result boundary."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("LDO metric mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def freeze_value(value: Any, _seen: set[int] | None = None) -> Any:
    """Copy JSON-shaped metric data into immutable containers."""
    seen = set() if _seen is None else _seen
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("metric values must be finite")
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise TypeError("metric value contains a cyclic mapping")
        seen.add(identity)
        result = _FrozenDict()
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError("metric mappings require text keys")
                dict.__setitem__(result, key, freeze_value(child, seen))
        finally:
            seen.remove(identity)
        return result
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise TypeError("metric value contains a cyclic sequence")
        seen.add(identity)
        try:
            return tuple(freeze_value(child, seen) for child in value)
        finally:
            seen.remove(identity)
    raise TypeError("metric value is not JSON-compatible")


def copy_json_value(value: Any, _seen: set[int] | None = None) -> Any:
    """Make a plain JSON-shaped copy while retaining list/tuple semantics.

    Validation alone is not enough for a gate boundary: a hostile ``Mapping``
    or ``list`` subclass can override equality, hashing, or iteration after it
    has been inspected.  Converting containers and scalar subclasses to their
    builtin counterparts gives the comparator a stable value to work with and
    prevents caller aliases from reaching observations.
    """
    seen = set() if _seen is None else _seen
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("metric values must be finite")
        return float(value)
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise TypeError("metric value contains a cyclic mapping")
        seen.add(identity)
        result: dict[str, Any] = {}
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise TypeError("metric mappings require text keys")
                result[str(key)] = copy_json_value(child, seen)
        finally:
            seen.remove(identity)
        return result
    if isinstance(value, list):
        identity = id(value)
        if identity in seen:
            raise TypeError("metric value contains a cyclic sequence")
        seen.add(identity)
        try:
            return [copy_json_value(child, seen) for child in value]
        finally:
            seen.remove(identity)
    if isinstance(value, tuple):
        identity = id(value)
        if identity in seen:
            raise TypeError("metric value contains a cyclic sequence")
        seen.add(identity)
        try:
            return tuple(copy_json_value(child, seen) for child in value)
        finally:
            seen.remove(identity)
    raise TypeError("metric value is not JSON-compatible")


def thaw_value(value: Any) -> Any:
    """Return an independent mutable copy for callers/serializers."""
    if isinstance(value, Mapping):
        return {str(key): thaw_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_value(child) for child in value]
    return value


def finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LDOMetricError("%s must be numeric" % label)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LDOMetricError("%s must be numeric" % label) from exc
    if not math.isfinite(result):
        raise LDOMetricError("%s must be finite" % label)
    return result


def walk_finite(value: object, path: str = "evidence", _seen: set[int] | None = None) -> None:
    """Validate the complete JSON-shaped metric payload.

    ``json.dumps`` would reject unsupported values eventually, but allowing
    one to reach ``MetricObservation`` first used to leak a ``TypeError`` out
    of ``evaluate_metrics``.  Metric evaluation is a gate boundary, so every
    malformed value must become a structured blocked result.  The explicit
    cycle guard also prevents a hostile mapping/list from recursing forever.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LDOMetricError("%s must be finite" % path)
        return
    seen = set() if _seen is None else _seen
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise LDOMetricError("%s contains a cyclic object" % path)
        seen.add(identity)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise LDOMetricError("%s contains a non-text key" % path)
                walk_finite(child, "%s.%s" % (path, key), seen)
        finally:
            seen.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise LDOMetricError("%s contains a cyclic object" % path)
        seen.add(identity)
        try:
            for index, child in enumerate(value):
                walk_finite(child, "%s[%d]" % (path, index), seen)
        finally:
            seen.remove(identity)
        return
    raise LDOMetricError("%s contains a non-JSON value" % path)

