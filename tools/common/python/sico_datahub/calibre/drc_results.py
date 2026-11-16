"""Conservative adapters for Calibre DRC summary and ASCII results."""

from __future__ import annotations

import math
import re

from ..codec import artifact_ref
from ..errors import HubError
from .outcomes import DrcResult, DrcViolation
from .result_io import read_result_file

_MAX_SAMPLES = 16
_TOTAL = re.compile(
    r"(?:TOTAL\s+(?:RESULT\s+COUNT|RESULTS?)"
    r"|TOTAL\s+DRC\s+RESULTS?(?:\s+GENERATED)?)"
    r"\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)
_CHECK_COUNT = re.compile(
    r"^\s*(?:RULECHECK|RULE|CHECK)\s+"
    r"(?:\"([^\"]+)\"|([A-Za-z0-9_.:+@-]+))"
    r".*?(?:RESULT\s+COUNT|COUNT|RESULTS?)"
    r"\s*[:=]?\s*(\d+)",
    re.IGNORECASE | re.MULTILINE,
)
_ASCII_NAME = re.compile(r"[A-Za-z0-9_.:+@-]+\Z")
_ASCII_HEAD = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)(?:\s+|$)")
_ASCII_POLY = re.compile(r"^\s*p\s+(\d+)\s+(\d+)\s*$")
_POINT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*$")
_FAILURE = re.compile(r"\b(?:FATAL|ERROR:)", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _check_name(match):
    return match.group(1) or match.group(2)


def _message(text):
    """Keep Calibre headers as one bounded line for the JSON control plane."""
    return " ".join(_CONTROL.sub(" ", text).split())


def _summary(text):
    total_match = _TOTAL.search(text)
    total = int(total_match.group(1)) if total_match else 0
    counts = {}
    for match in _CHECK_COUNT.finditer(text):
        name = _check_name(match)
        value = int(match.group(3))
        counts[name] = max(counts.get(name, 0), value)
    recognized = total_match is not None or bool(counts)
    if not total:
        total = sum(counts.values())
    return total, counts, recognized


def parse_drc_summary(text):
    """Return summary totals; absence of evidence is not a clean result."""
    total, counts, _recognized = _summary(text)
    return total, counts


def _bbox(points):
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return ((min(xs), min(ys)), (max(xs), max(ys)))


def _ascii_names(lines):
    names = set()
    for index in range(len(lines) - 2):
        name = lines[index].strip()
        if _ASCII_NAME.fullmatch(name) is None:
            continue
        head = _ASCII_HEAD.match(lines[index + 1])
        if head is None:
            continue
        text_count = int(head.group(3))
        header = lines[index + 2:index + 2 + text_count]
        if any("Rule File Pathname:" in line for line in header):
            names.add(name)
    return names


def _ascii_blocks(lines, names):
    blocks = {}
    index = 0
    while index < len(lines) - 1:
        name = lines[index].strip()
        head = _ASCII_HEAD.match(lines[index + 1])
        if name not in names or head is None:
            index += 1
            continue
        text_count = int(head.group(3))
        header = lines[index + 2:index + 2 + text_count]
        description = " ".join(
            line.strip()
            for line in header
            if line.strip() and not line.strip().startswith("Rule File Pathname:")
        )[:4096]
        description = _message(description)
        index += 2 + text_count
        samples = []
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                candidate in names
                and index + 1 < len(lines)
                and _ASCII_HEAD.match(lines[index + 1])
            ):
                break
            poly = _ASCII_POLY.match(lines[index])
            if poly is None:
                index += 1
                continue
            count = int(poly.group(2))
            index += 1
            points = []
            for _ in range(min(count, 4096)):
                if index >= len(lines):
                    break
                point = _POINT.match(lines[index])
                if point is None:
                    break
                x, y = (float(point.group(1)), float(point.group(2)))
                if math.isfinite(x) and math.isfinite(y):
                    points.append((x, y))
                index += 1
            if len(samples) < _MAX_SAMPLES:
                box = _bbox(points)
                if box is not None:
                    samples.append(box)
        blocks[name] = (description, tuple(samples))
    return blocks


def _drc_status(total, recognized, results_text, summary_text):
    if total > 0:
        return "violations"
    if recognized:
        return "clean"
    if _FAILURE.search(results_text + "\n" + summary_text):
        return "failed"
    return "unknown"


def parse_drc_results(
    results_path,
    *,
    summary_path=None,
    provenance=None,
    max_violations=200,
    scope="full",
) -> DrcResult:
    results_path, results_text = read_result_file(results_path)
    summary_ref = None
    summary_text = ""
    if summary_path is not None:
        summary_path, summary_text = read_result_file(summary_path)
        summary_ref = artifact_ref(summary_path)
    if type(max_violations) is not int or not 1 <= max_violations <= 200:
        raise HubError("invalid_arguments", "Invalid DRC violation limit")
    total, counts, recognized = _summary(summary_text)
    lines = results_text.splitlines()
    names = set(counts) | _ascii_names(lines)
    blocks = _ascii_blocks(lines, names)
    for name, (_description, samples) in blocks.items():
        if samples and counts.get(name, 0) == 0:
            counts[name] = len(samples)
    if not total:
        total = sum(counts.values())
    nonzero = sorted(
        ((name, value) for name, value in counts.items() if value > 0),
        key=lambda item: (-item[1], item[0]),
    )
    detailed = []
    for name, value in nonzero[:max_violations]:
        description, samples = blocks.get(name, ("", ()))
        details = {
            "source": "calibre-ascii",
            "result_count": value,
            "sample_bboxes": [
                [list(box[0]), list(box[1])] for box in samples
            ],
            "samples_truncated": value > len(samples),
        }
        detailed.append(DrcViolation(
            name,
            message=description or name,
            bbox=samples[0] if samples else None,
            count=value,
            details=details,
        ))
    status = _drc_status(total, recognized, results_text, summary_text)
    return DrcResult(
        status=status,
        total=total,
        checks=counts,
        violations=tuple(detailed),
        results=artifact_ref(results_path),
        summary=summary_ref,
        provenance=provenance,
        scope=scope,
    )


__all__ = ["parse_drc_results", "parse_drc_summary"]
