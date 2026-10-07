"""Validate controller-owned saved-state and double-read evidence."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .ldo_qualification_status import LDO_TOPOLOGIES, M3_BLOCKED_INPUT
from .ldo_qualification_values import (
    READ_ONLY_AUTHORITIES,
    qualification_reject_verdict_fields,
    qualification_valid_digest,
)


def validate_authenticated_snapshot(
    evidence: Mapping[str, Any],
    *,
    source_generation: str,
    library: str,
    cells: Sequence[str] = LDO_TOPOLOGIES,
) -> dict[str, Any]:
    """Validate controller-owned read-only snapshot evidence.

    A filesystem preflight can supply hashes. Saved-state and double-read
    facts require authenticated IPC or the controller-owned Virtuoso worker.
    """

    findings: list[str] = []
    try:
        qualification_reject_verdict_fields(evidence)
    except ValueError as exc:
        findings.append(str(exc))
    if not isinstance(evidence, Mapping):
        return {"status": M3_BLOCKED_INPUT, "authenticated": False, "findings": ["snapshot evidence must be an object"]}
    if evidence.get("schema_version") != 1:
        findings.append("snapshot evidence schema_version must be 1")
    if evidence.get("status") != "PASS":
        findings.append("snapshot evidence status is not PASS")
    if evidence.get("authority") not in (READ_ONLY_AUTHORITIES | {"controller-owned-virtuoso-read-only"}):
        findings.append("snapshot authority is not authenticated cdns-ipc or controller-owned readback")
    if evidence.get("formal_saved_state") is not True:
        findings.append("formal saved-state evidence is missing")
    if evidence.get("double_read_equal") is not True:
        findings.append("double-read equality evidence is missing")
    if evidence.get("source_generation") != source_generation:
        findings.append("authenticated source_generation does not match preflight")
    target = evidence.get("target")
    if not isinstance(target, Mapping) or target.get("library") != library:
        findings.append("snapshot target library does not match profile")
    target_cells = target.get("cells") if isinstance(target, Mapping) else None
    if target_cells is not None and set(str(item) for item in target_cells) != set(cells):
        findings.append("snapshot target cells do not match both LDO topologies")
    snapshot_digest = evidence.get("snapshot_digest")
    if snapshot_digest is not None and not qualification_valid_digest(snapshot_digest):
        findings.append("snapshot_digest is malformed")
    return {
        "status": "PASS" if not findings else M3_BLOCKED_INPUT,
        "authenticated": not findings,
        "authority": evidence.get("authority"),
        "formal_saved_state": evidence.get("formal_saved_state") is True,
        "double_read_equal": evidence.get("double_read_equal") is True,
        "source_generation": evidence.get("source_generation"),
        "findings": sorted(set(findings)),
    }
