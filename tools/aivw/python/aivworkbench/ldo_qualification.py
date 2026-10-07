"""Compose profile preflight and optional evidence qualification.

Compatibility exports remain the same objects. Remove them after external
consumers migrate to the named source/evidence/report owners.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .ldo_qualification_aggregate import aggregate_ldo_qualification
from .ldo_qualification_snapshot import build_source_snapshot
from .ldo_qualification_status import M3_BLOCKED_INPUT, M3_SCHEMA_VERSION


def qualify_ldo_profile(
    profile: Any,
    *,
    authenticated_snapshot: Mapping[str, Any] | None = None,
    topology_evidence: Mapping[str, Sequence[Mapping[str, Any]] | Mapping[str, Any]] | None = None,
    repeat_runs: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    public_plans: Mapping[str, Mapping[str, Any]] | None = None,
    hidden_holdout: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the offline M3 profile preflight and optional evidence aggregate."""

    projects = getattr(profile, "projects", {})
    project = projects.get("ldo") if isinstance(projects, Mapping) else None
    if not isinstance(project, Mapping):
        snapshot = {
            "schema_version": M3_SCHEMA_VERSION,
            "kind": "ldo-source-snapshot",
            "status": M3_BLOCKED_INPUT,
            "authenticated": False,
            "authority": "profile",
            "errors": ["profile has no ldo project mapping"],
        }
        return aggregate_ldo_qualification({}, source_snapshot=snapshot)
    root = project.get("root")
    snapshot = build_source_snapshot(
        str(root),
        library=str(project.get("library", "amsLDO")),
        cds_lib=project.get("cds_lib"),
        source_cds_lib=project.get("source_cds_lib"),
        mapping_root=project.get("mapping_cds_lib") and Path(str(project["mapping_cds_lib"])).parent,
        mapping_allowed_roots=project.get("mapping_allowed_roots"),
        mapping_environment=project.get("mapping_environment"),
        model_root=project.get("model_root"),
        required_library_mappings=tuple(project.get("required_library_mappings", ("amsLDO", "gpdk045", "analogLib", "basic"))),
        required_model_sections=tuple(project.get("required_model_sections", ("tt", "ff", "ss"))),
        authenticated_snapshot=authenticated_snapshot,
    )
    if topology_evidence is None:
        topology_evidence = {}
    if public_plans is None:
        public_plans = {}
    return aggregate_ldo_qualification(
        topology_evidence,
        source_snapshot=snapshot,
        repeat_runs=repeat_runs,
        public_plans=public_plans,
        hidden_holdout=hidden_holdout,
        metadata={"profile": getattr(profile, "name", "unknown"), "project": "ldo", "offline_preflight": True},
    )


from .ldo_qualification_evidence import (
    validate_topology_evidence as validate_topology_evidence,
)
from .ldo_qualification_holdout import (
    validate_holdout_blindness as validate_holdout_blindness,
)
from .ldo_qualification_negative import (
    run_negative_injection_matrix as run_negative_injection_matrix,
)
from .ldo_qualification_repeat import compare_repeat_runs as compare_repeat_runs
from .ldo_qualification_report import (
    write_ldo_qualification_report as write_ldo_qualification_report,
)
from .ldo_qualification_secrets import scan_secret_leaks as scan_secret_leaks
from .ldo_qualification_snapshot import (
    verify_source_snapshot_unchanged as verify_source_snapshot_unchanged,
)
from .ldo_qualification_status import LDO_GATES as LDO_GATES
from .ldo_qualification_status import LDO_TOPOLOGIES as LDO_TOPOLOGIES
from .ldo_qualification_status import M3_ANALOG_ISLAND as M3_ANALOG_ISLAND
from .ldo_qualification_status import M3_BLOCKED_ENVIRONMENT as M3_BLOCKED_ENVIRONMENT
from .ldo_qualification_status import M3_FAIL as M3_FAIL
from .ldo_qualification_status import M3_FAIL_REPEAT as M3_FAIL_REPEAT
from .ldo_qualification_status import M3_NEEDS_MORE_EVIDENCE as M3_NEEDS_MORE_EVIDENCE
from .ldo_qualification_status import M3_QUALIFIED as M3_QUALIFIED
from .ldo_qualification_status import M3_STALE_SOURCE as M3_STALE_SOURCE
from .ldo_snapshot_authentication import (
    validate_authenticated_snapshot as validate_authenticated_snapshot,
)

__all__ = ['LDO_GATES', 'LDO_TOPOLOGIES', 'M3_ANALOG_ISLAND', 'M3_BLOCKED_ENVIRONMENT', 'M3_BLOCKED_INPUT', 'M3_FAIL', 'M3_FAIL_REPEAT', 'M3_NEEDS_MORE_EVIDENCE', 'M3_QUALIFIED', 'M3_SCHEMA_VERSION', 'M3_STALE_SOURCE', 'aggregate_ldo_qualification', 'build_source_snapshot', 'compare_repeat_runs', 'qualify_ldo_profile', 'run_negative_injection_matrix', 'scan_secret_leaks', 'validate_authenticated_snapshot', 'validate_holdout_blindness', 'validate_topology_evidence', 'verify_source_snapshot_unchanged', 'write_ldo_qualification_report']
