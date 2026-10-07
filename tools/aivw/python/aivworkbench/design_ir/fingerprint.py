"""Stable SHA-256 identity for canonical DesignIR."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .normalize import normalize_design_ir
from .schema import DesignIR


def design_ir_digest(value: DesignIR | Mapping[str, Any]) -> str:
    canonical = json.dumps(
        normalize_design_ir(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ["design_ir_digest"]
