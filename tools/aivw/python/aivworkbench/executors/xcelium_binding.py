"""Xcelium evidence binding and artifact locator owner."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..artifact_paths import PathContractError, path_has_symlink_component, validate_relative_path
from ..executor import ExternalArtifactLocator

def _evidence_locators(
    evidence: Mapping[str, Any],
    payload_root: Path,
    evidence_binding: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[ExternalArtifactLocator]:
    """Expose validated coverage/waveform paths as bounded manifest locators."""
    locators: list[ExternalArtifactLocator] = []
    seen: set[str] = set()
    for event_type, key, producer in (
        ("coverage", "observed_coverage_ids", "xcelium.coverage"),
        ("waveform", "observed_waveform_ids", "xcelium.waveform"),
    ):
        ids = evidence.get(key, [])
        if not isinstance(ids, list):
            continue
        events = {
            str(event.get("id")): event
            for event in evidence.get("events", [])
            if isinstance(event, Mapping) and event.get("event") == event_type
        }
        for identifier in ids:
            event = events.get(str(identifier))
            path_value = event.get("path") if isinstance(event, Mapping) else None
            if (
                not isinstance(event, Mapping)
                or event.get("status") != "PASS"
                or not isinstance(path_value, str)
                or not path_value
            ):
                continue
            if event_type == "waveform":
                waveform = evidence.get("waveform")
                retention = waveform.get("retention") if isinstance(waveform, Mapping) else None
                if retention == "never":
                    continue
                if retention == "on_failure" and evidence.get("status") == "PASS":
                    continue
            elif evidence.get("status") != "PASS":
                continue
            expected = evidence_binding.get(event_type) if evidence_binding else None
            if isinstance(expected, Mapping) and expected.get("path") != path_value:
                continue
            candidate = (payload_root / Path(path_value)).resolve()
            if not candidate.exists() or path_has_symlink_component(payload_root, payload_root / Path(path_value)) or not candidate.is_relative_to(payload_root.resolve()):
                continue
            relative = candidate.relative_to(payload_root.resolve()).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            locators.append(
                ExternalArtifactLocator(
                    candidate,
                    producer,
                    {"evidence_id": str(identifier), "evidence_type": event_type},
                )
            )
    return locators

def _validate_evidence_binding(
    plan: Mapping[str, Any], raw_binding: object, payload_root: Path
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Validate model-class declarations that bind evidence to Xcelium output.

    The generic evaluator can inspect an event path, but it cannot know whether
    a model-class testbench actually requested that path from Xcelium.  This
    binding is the narrow hand-off between a registered renderer/adapter and
    the generic executor.  It contains no arbitrary command line; tool flags
    remain owned by the registered adapter implementation.
    """
    required = {
        kind: plan.get(kind) for kind in ("coverage", "waveform") if plan.get(kind) is not None
    }
    if not required:
        if raw_binding is None:
            return {}, []
        if not isinstance(raw_binding, Mapping):
            return {}, [{"code": "invalid_evidence_binding", "detail": "binding must be an object"}]
    elif not isinstance(raw_binding, Mapping):
        return {}, [{"code": "missing_evidence_binding", "expected": sorted(required)}]

    assert isinstance(raw_binding, Mapping)
    findings: list[dict[str, Any]] = []
    normalized: dict[str, dict[str, Any]] = {}
    allowed = set(required)
    extras = set(raw_binding) - allowed
    for kind in sorted(extras):
        findings.append({"code": "extra_evidence_binding", "type": kind})
    for kind, contract in required.items():
        item = raw_binding.get(kind)
        if not isinstance(item, Mapping):
            findings.append({"code": "missing_evidence_binding", "type": kind})
            continue
        extra_fields = set(item) - {"id", "path", "producer"}
        if extra_fields:
            findings.append(
                {
                    "code": "evidence_binding_fields_invalid",
                    "type": kind,
                    "fields": sorted(str(value) for value in extra_fields),
                }
            )
        identifier = contract.get("id") if isinstance(contract, Mapping) else None
        if item.get("id") != identifier:
            findings.append(
                {
                    "code": "evidence_binding_id_mismatch",
                    "type": kind,
                    "expected": identifier,
                    "actual": item.get("id"),
                }
            )
        path_value = item.get("path")
        try:
            path_value = validate_relative_path(path_value, "evidence binding")
        except PathContractError:
            findings.append(
                {"code": "evidence_binding_path_invalid", "type": kind, "actual": path_value}
            )
            continue
        producer = item.get("producer")
        if producer != "xcelium":
            findings.append(
                {
                    "code": "evidence_binding_producer_invalid",
                    "type": kind,
                    "expected": "xcelium",
                    "actual": producer,
                }
            )
        normalized[kind] = {
            "id": identifier,
            "path": path_value,
            "producer": "xcelium",
        }
        candidate = payload_root / Path(path_value)
        if path_has_symlink_component(payload_root, candidate):
            findings.append(
                {"code": "evidence_binding_path_symlink", "type": kind, "path": path_value}
            )
    return normalized, findings

def _compare_evidence_binding(
    binding: Mapping[str, Mapping[str, Any]], events: object
) -> list[dict[str, Any]]:
    if not binding or not isinstance(events, list):
        return []
    by_type = {
        str(event.get("event")): event
        for event in events
        if isinstance(event, Mapping) and isinstance(event.get("event"), str)
    }
    findings: list[dict[str, Any]] = []
    for kind, expected in binding.items():
        event = by_type.get(kind)
        if not isinstance(event, Mapping):
            continue
        if event.get("id") == expected.get("id") and event.get("path") != expected.get("path"):
            findings.append(
                {
                    "code": "evidence_binding_path_mismatch",
                    "type": kind,
                    "expected": expected.get("path"),
                    "actual": event.get("path"),
                }
            )
    return findings
