from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Mapping

from .errors import EnvironmentError
from .profiles import product_root
from .m1ai_correlation_paths import _reject_excluded_path
from .m1ai_correlation_sources import _read_json

POLICY_NAME = "comparator_new_correlation.json"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
def load_correlation_policy(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the checked-in comparison policy."""
    policy_path = path or (product_root() / "contracts" / POLICY_NAME)
    _reject_excluded_path(policy_path, label="correlation policy")
    payload = _read_json(policy_path, label="correlation policy")
    if payload.get("schema_version") != 1:
        raise EnvironmentError("unsupported correlation policy schema")
    target = payload.get("target")
    metrics = payload.get("metrics")
    required_types = payload.get("required_case_types")
    if not isinstance(target, Mapping) or not isinstance(metrics, Mapping):
        raise EnvironmentError("correlation policy requires target and metrics")
    if not isinstance(required_types, list) or not required_types:
        raise EnvironmentError("correlation policy requires required_case_types")
    for name, rule in metrics.items():
        if not isinstance(name, str) or not isinstance(rule, Mapping):
            raise EnvironmentError("correlation metric rules must be objects")
        if rule.get("kind") not in {"categorical", "numeric"}:
            raise EnvironmentError(f"unsupported correlation metric kind: {name}")
        if rule.get("required") is not True:
            raise EnvironmentError(f"correlation metric must be required: {name}")
        if rule["kind"] == "numeric":
            tolerance = rule.get("absolute_tolerance")
            if (
                isinstance(tolerance, bool)
                or not isinstance(tolerance, (int, float))
                or not math.isfinite(float(tolerance))
                or tolerance < 0
            ):
                raise EnvironmentError(f"invalid absolute tolerance for metric: {name}")
    return payload

__all__=["load_correlation_policy"]
