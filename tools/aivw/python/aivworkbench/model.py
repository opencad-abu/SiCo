"""Generic model-class request contract used by generation executors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelGenerationRequest:
    """Version-neutral context supplied to a registered model-class plugin."""

    contract: Mapping[str, Any]
    spec: Mapping[str, Any]
    contract_sha256: str
    spec_sha256: str
    generated_at: str
    verification: Mapping[str, Any] = field(default_factory=dict)
    # Recipe-owned model options are passed to the registered model class.
    # They are configuration data only; executable Xcelium argv remains owned
    # by the registered evidence adapter.
    model_options: Mapping[str, Any] = field(default_factory=dict)


__all__ = ["ModelGenerationRequest"]
