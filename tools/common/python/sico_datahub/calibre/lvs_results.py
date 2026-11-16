"""Conservative adapters for Calibre LVS reports and completion logs."""

from __future__ import annotations

import re

from ..codec import artifact_ref
from .outcomes import LvsFinding, LvsResult
from .result_io import read_result_file

_LVS_STATUS = re.compile(
    r"LVS\s+completed\.\s+(CORRECT|INCORRECT|NOT\s+COMPARED)\b",
    re.IGNORECASE,
)
_LVS_REPORT_STATUS = re.compile(
    r"\b(INCORRECT|NOT\s+COMPARED|CORRECT)\b", re.IGNORECASE
)
_LVS_TABLE = re.compile(
    r"^\s*(Ports|Nets|Instances):\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
    re.IGNORECASE,
)
_LVS_TOTAL_INST = re.compile(
    r"^\s*Total\s+Inst:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
    re.IGNORECASE,
)
_LVS_DETAIL = re.compile(
    r"^\s*(Mismatch|Unmatched|Incorrect)\s*:(.*)", re.IGNORECASE
)
_LVS_ZERO = re.compile(
    r"\b(?:0|no)\s+(?:mismatch(?:es)?|unmatched|errors?|failures?)\b",
    re.IGNORECASE,
)
_FAILURE = re.compile(r"\b(?:FATAL|ERROR:)", re.IGNORECASE)


def _report_status(text):
    direct = _LVS_STATUS.search(text)
    if direct is not None:
        return " ".join(direct.group(1).lower().split())
    section = re.search(r"OVERALL\s+COMPARISON\s+RESULTS", text, re.IGNORECASE)
    if section is None:
        return ""
    match = _LVS_REPORT_STATUS.search(text[section.end():section.end() + 2048])
    return " ".join(match.group(1).lower().split()) if match else ""


def _unmatched(report_text):
    counts = {}
    findings = []
    for line in report_text.splitlines():
        match = _LVS_TABLE.match(line)
        if match is not None:
            category = match.group(1).lower()
            layout = int(match.group(4))
            source = int(match.group(5))
        else:
            match = _LVS_TOTAL_INST.match(line)
            if match is None:
                continue
            category = "instances"
            layout = int(match.group(3))
            source = int(match.group(4))
        if layout:
            counts["unmatched_layout"] = counts.get("unmatched_layout", 0) + layout
            findings.append(LvsFinding(
                "unmatched_layout",
                f"{category}: {line.strip()}"[:4096],
                count=layout,
            ))
        if source:
            counts["unmatched_source"] = counts.get("unmatched_source", 0) + source
            findings.append(LvsFinding(
                "unmatched_source",
                f"{category}: {line.strip()}"[:4096],
                count=source,
            ))
    return counts, findings


def _details(report_text):
    counts = {}
    findings = []
    for line in report_text.splitlines():
        if _LVS_ZERO.search(line):
            continue
        match = _LVS_DETAIL.match(line)
        if match is None:
            continue
        category = match.group(1).lower()
        counts[category] = counts.get(category, 0) + 1
        if len(findings) < 200:
            findings.append(LvsFinding(category, line.strip()[:4096]))
    return counts, findings


def parse_lvs_report(
    report_path, *, log_path=None, svdb_path=None, provenance=None, scope="full"
) -> LvsResult:
    report_path, text = read_result_file(report_path)
    log_ref = None
    status = ""
    if log_path is not None:
        log_path, log_text = read_result_file(log_path)
        log_ref = artifact_ref(log_path)
        match = _LVS_STATUS.search(log_text)
        if match is not None:
            status = " ".join(match.group(1).lower().split())
    if not status:
        status = _report_status(text)
    counts = {}
    findings = []
    if status == "incorrect":
        counts, findings = _unmatched(text)
        detail_counts, detail_findings = _details(text)
        for name, value in detail_counts.items():
            counts[name] = counts.get(name, 0) + value
        findings.extend(detail_findings)
        if not counts:
            counts["incorrect"] = 1
    elif status == "not compared":
        status = "unknown"
        findings = [LvsFinding("not_compared", "Calibre LVS did not compare")]
    elif status not in {"correct", "unknown"}:
        status = "failed" if _FAILURE.search(text) else "unknown"
    return LvsResult(
        status=status,
        counts=counts,
        findings=tuple(findings[:200]),
        report=artifact_ref(report_path),
        svdb=artifact_ref(svdb_path) if svdb_path is not None else None,
        log=log_ref,
        provenance=provenance,
        scope=scope,
    )


__all__ = ["parse_lvs_report"]
