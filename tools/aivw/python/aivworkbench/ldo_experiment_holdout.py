"""Reject hidden holdout identifiers and values in a public plan."""

from __future__ import annotations


import math


from typing import Any, Mapping, Sequence

from .ldo_experiment_errors import LDOExperimentSafetyError, LDOHoldoutLeakError

def holdout_secrets(value: object) -> tuple[tuple[str, ...], tuple[object, ...]]:
    if value is None:
        return (), ()
    if not isinstance(value, Mapping):
        raise LDOHoldoutLeakError("hidden_holdout guard must be an object")
    known_id_fields = ("ids", "case_ids", "vector_ids")
    present_id_fields = [name for name in known_id_fields if name in value]
    identifiers: list[str] = []
    for name in present_id_fields:
        raw = value[name]
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise LDOHoldoutLeakError("hidden_holdout.%s must be an array" % name)
        for item in raw:
            if not isinstance(item, str) or not item:
                raise LDOHoldoutLeakError("hidden holdout identifiers must be text")
            identifiers.append(item)

    values: list[object] = []

    def collect(raw: object) -> None:
        if isinstance(raw, Mapping):
            for child in raw.values():
                collect(child)
        elif isinstance(raw, (list, tuple)):
            for child in raw:
                collect(child)
        elif isinstance(raw, bool) or raw is None:
            return
        elif isinstance(raw, (int, float)):
            if not math.isfinite(float(raw)):
                raise LDOHoldoutLeakError("hidden holdout values must be finite")
            values.append(raw)
        elif isinstance(raw, str):
            if raw:
                values.append(raw)
        else:
            raise LDOHoldoutLeakError("hidden holdout values must be JSON scalars")

    if "values" in value:
        collect(value["values"])
    elif not present_id_fields:
        identifiers.extend(str(key) for key in value)
        collect(value)
    unknown = set(value) - set(known_id_fields) - {"values"}
    if present_id_fields and unknown:
        raise LDOHoldoutLeakError(
            "hidden_holdout has unknown fields: %s" % sorted(str(item) for item in unknown)
        )
    return tuple(identifiers), tuple(values)


def assert_no_holdout_leak(plan: Mapping[str, Any], hidden_holdout: object) -> None:
    identifiers, secret_values = holdout_secrets(hidden_holdout)
    if not identifiers and not secret_values:
        return

    def visit(value: object, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, "%s.%s" % (path, key))
            return
        if isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                visit(child, "%s[%d]" % (path, index))
            return
        if isinstance(value, str):
            for identifier in identifiers:
                if identifier in value:
                    raise LDOHoldoutLeakError(
                        "hidden holdout identifier leaked at %s" % path
                    )
            for secret in secret_values:
                if isinstance(secret, str) and value == secret:
                    raise LDOHoldoutLeakError("hidden holdout value leaked at %s" % path)
            return
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            number = float(value)
            if not math.isfinite(number):
                raise LDOExperimentSafetyError("public plan contains a non-finite number")
            for secret in secret_values:
                if (
                    not isinstance(secret, (bool, str))
                    and isinstance(secret, (int, float))
                    and number == float(secret)
                ):
                    raise LDOHoldoutLeakError("hidden holdout value leaked at %s" % path)

    visit(plan, "plan")
