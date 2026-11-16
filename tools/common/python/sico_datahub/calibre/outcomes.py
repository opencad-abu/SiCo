"""Bounded DRC and LVS outcome records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from ..codec import ArtifactRef
from ..errors import HubError
from ._validation import artifact, bbox, count, mapping, text
from .provenance import CalibreProvenance

_DRC_STATES = frozenset({"clean", "violations", "failed", "unknown"})
_LVS_STATES = frozenset({"correct", "incorrect", "failed", "unknown"})
_SCOPES = frozenset({"full", "region", "fragment", "candidate"})


def _provenance(value, field_name):
    if value is not None and not isinstance(value, CalibreProvenance):
        raise HubError("invalid_arguments", "Invalid " + field_name)
    return value


@dataclass(frozen=True)
class DrcViolation:
    check: str
    message: str = ""
    severity: str = "error"
    cell: str = ""
    layer: str = ""
    bbox: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None
    count: int = 1
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        text(self.check, "check", limit=512)
        text(self.message, "message", empty=True)
        text(self.severity, "severity", limit=32)
        text(self.cell, "cell", empty=True, limit=512)
        text(self.layer, "layer", empty=True, limit=512)
        bbox(self.bbox)
        count(self.count, "violation count", allow_zero=False)
        mapping(dict(self.details), "Violation details")

    def to_dict(self):
        return {
            "check": self.check,
            "message": self.message,
            "severity": self.severity,
            "cell": self.cell,
            "layer": self.layer,
            "bbox": (
                [list(self.bbox[0]), list(self.bbox[1])]
                if self.bbox is not None else None
            ),
            "count": self.count,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class DrcResult:
    status: str
    total: int
    checks: Mapping[str, int] = field(default_factory=dict)
    violations: Tuple[DrcViolation, ...] = ()
    results: Optional[ArtifactRef] = None
    summary: Optional[ArtifactRef] = None
    provenance: Optional[CalibreProvenance] = None
    scope: str = "full"

    def __post_init__(self):
        if self.status not in _DRC_STATES:
            raise HubError("invalid_arguments", "Invalid DRC result status")
        count(self.total, "DRC result total")
        for check, value in self.checks.items():
            text(check, "check", limit=512)
            count(value, "check count")
        if len(self.violations) > 200:
            raise HubError("result_too_large", "Too many DRC violations in one result")
        artifact(self.results, "results", optional=True)
        artifact(self.summary, "summary", optional=True)
        _provenance(self.provenance, "DRC provenance")
        if self.scope not in _SCOPES:
            raise HubError("invalid_arguments", "Invalid DRC result scope")

    @property
    def signoff_eligible(self):
        """Only a clean full-scope run can be presented as signoff evidence."""
        return self.status == "clean" and self.scope == "full"

    def to_dict(self):
        return {
            "status": self.status,
            "total": self.total,
            "checks": dict(self.checks),
            "violations": [violation.to_dict() for violation in self.violations],
            "results": self.results.to_dict() if self.results else None,
            "summary": self.summary.to_dict() if self.summary else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "scope": self.scope,
            "signoff_eligible": self.signoff_eligible,
            "violations_truncated": self.total > len(self.violations),
        }


@dataclass(frozen=True)
class LvsFinding:
    category: str
    message: str
    cell: str = ""
    net: str = ""
    count: int = 1

    def __post_init__(self):
        text(self.category, "category", limit=64)
        text(self.message, "message")
        text(self.cell, "cell", empty=True, limit=512)
        text(self.net, "net", empty=True, limit=512)
        count(self.count, "finding count", allow_zero=False)

    def to_dict(self):
        return {
            "category": self.category,
            "message": self.message,
            "cell": self.cell,
            "net": self.net,
            "count": self.count,
        }


@dataclass(frozen=True)
class LvsResult:
    status: str
    counts: Mapping[str, int] = field(default_factory=dict)
    findings: Tuple[LvsFinding, ...] = ()
    report: Optional[ArtifactRef] = None
    svdb: Optional[ArtifactRef] = None
    log: Optional[ArtifactRef] = None
    provenance: Optional[CalibreProvenance] = None
    scope: str = "full"

    def __post_init__(self):
        if self.status not in _LVS_STATES:
            raise HubError("invalid_arguments", "Invalid LVS result status")
        for name, value in self.counts.items():
            text(name, "LVS count", limit=128)
            count(value, "LVS count")
        if len(self.findings) > 200:
            raise HubError("result_too_large", "Too many LVS findings in one result")
        artifact(self.report, "report", optional=True)
        artifact(self.svdb, "svdb", optional=True)
        artifact(self.log, "log", optional=True)
        _provenance(self.provenance, "LVS provenance")
        if self.scope not in _SCOPES:
            raise HubError("invalid_arguments", "Invalid LVS result scope")

    @property
    def signoff_eligible(self):
        return self.status == "correct" and self.scope == "full"

    def to_dict(self):
        return {
            "status": self.status,
            "counts": dict(self.counts),
            "findings": [finding.to_dict() for finding in self.findings],
            "report": self.report.to_dict() if self.report else None,
            "svdb": self.svdb.to_dict() if self.svdb else None,
            "log": self.log.to_dict() if self.log else None,
            "provenance": self.provenance.to_dict() if self.provenance else None,
            "findings_truncated": len(self.findings) >= 200,
            "scope": self.scope,
            "signoff_eligible": self.signoff_eligible,
        }


__all__ = ["DrcResult", "DrcViolation", "LvsFinding", "LvsResult"]
