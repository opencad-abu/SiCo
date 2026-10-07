from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .errors import EnvironmentError

def _reject_excluded_path(path: Path, *, label: str) -> None:
    """Keep the excluded legacy workspace out of evidence provenance."""
    candidates = {str(path).lower()}
    try:
        candidates.add(str(path.resolve(strict=False)).lower())
    except OSError:
        pass
    if any("smic28" in candidate for candidate in candidates):
        raise EnvironmentError(f"{label} must not use the excluded smic28 workspace: {path}")
def _reject_excluded_payload(value: object, *, label: str) -> None:
    """Reject excluded workspace references carried inside JSON provenance."""
    if isinstance(value, str) and "smic28" in value.lower():
        raise EnvironmentError(f"{label} contains the excluded smic28 workspace")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_excluded_payload(key, label=label)
            _reject_excluded_payload(item, label=label)
    elif isinstance(value, list):
        for item in value:
            _reject_excluded_payload(item, label=label)

__all__=["_reject_excluded_path","_reject_excluded_payload"]
