"""Diagnostic value and severity order shared by private RNM checks."""

from __future__ import annotations

from dataclasses import dataclass

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    subject: str
    message: str
