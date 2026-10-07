"""Normalize policy and assertion inputs at their gate boundary."""

from __future__ import annotations
from typing import Any, Mapping, Sequence

from .metric_errors import MetricBoundaryError
from .metric_values import copy_json_value


def policy_definitions(policy: object) -> tuple[object, ...] | None:
    """Accept a metric array or a complete policy containing ``metrics``.

    The complete correlation-policy object is intentionally accepted without
    discarding its other fields; only the metric definitions participate in
    this evaluator.  Invalid entries remain in the returned tuple so they are
    rejected below instead of being silently filtered out.
    """
    if isinstance(policy, Mapping):
        raw = policy.get("metrics")
        if isinstance(raw, Mapping):
            return tuple(
                dict(item, name=str(name)) if isinstance(item, Mapping) else {"name": str(name), "kind": item}
                for name, item in raw.items()
            )
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            return tuple(raw)
        return None
    if isinstance(policy, Sequence) and not isinstance(policy, (str, bytes)):
        return tuple(policy)
    return None


def normalize_assertions(value: object, *, code: str, label: str) -> tuple[str, ...]:
    """Normalize assertion identifiers at their respective gate boundary."""
    if value is None:
        return ()
    try:
        value = copy_json_value(value)
    except Exception as exc:
        raise MetricBoundaryError(code, "%s is malformed: %s" % (label, exc)) from exc
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise MetricBoundaryError(code, "%s must be an array of text" % label)
    result: list[str] = []
    for index, item in enumerate(value):
        if type(item) is not str or not item:
            raise MetricBoundaryError(code, "%s[%d] must be non-empty text" % (label, index))
        result.append(item)
    return tuple(result)


def normalize_input(value: object, *, code: str, label: str) -> Any:
    """Copy a caller payload and classify malformed values consistently."""
    try:
        return copy_json_value(value)
    except MetricBoundaryError:
        raise
    except Exception as exc:
        raise MetricBoundaryError(code, "%s is malformed: %s" % (label, exc)) from exc

