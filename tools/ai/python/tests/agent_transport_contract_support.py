# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""Shared agent transport contract fixtures and helpers."""

from __future__ import annotations

from sico.core.contracts import BoundContext


def context(instance="i", generation="g", valid=True):
    return BoundContext(instance, generation, "bound", {"source": "simulated", "valid": valid})
