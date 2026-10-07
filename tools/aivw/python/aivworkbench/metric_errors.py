"""Classified metric boundary failures."""

from __future__ import annotations



class LDOMetricError(ValueError):
    """Raised for malformed metric policies/evidence."""


class MetricBoundaryError(LDOMetricError):
    """An evaluation error already classified at an input boundary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)

