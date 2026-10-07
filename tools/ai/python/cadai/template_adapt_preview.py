"""Attach revalidated P2 provenance without copying the target electrical graph."""

import copy

from .circuit_spec_schema import digest
from .template_adaptation import verify_use


def attach_adapted(result, use, record):
    from .template_circuit import placement_reference

    out = copy.deepcopy(result)
    plan = out["plan"]
    verified = verify_use(record, plan["spec"], use, plan["bindings"])
    reference = placement_reference(record, verified, plan["spec"])
    evidence = {"template_ref": record["template_ref"], "record_digest": digest(record),
                "capture_sha256": record["capture_sha256"], "rule_version": record["rule_version"],
                "mapping_verified": True, "mapping_scope": "verified_core_and_boundary.v2",
                "device_equivalence_verified": False, "electrical_equivalence_verified": False,
                "source_gaps": record["topology"].get("gaps", []),
                "parameter_policy": "explicit_target_parameters_only; source_values_not_copied",
                "placement_constraints": record["reuse_contract"]["placement_constraints"],
                "adaptation_digest": digest(verified),
                "residual_instances": verified["residual_instances"]}
    wire_reference = record.get("assets", {}).get("schematic", {}).get("wire_reference")
    if wire_reference is not None:
        evidence["wire_reference_digest"] = digest(wire_reference)
    plan["template_use"], plan["template_evidence"] = verified, evidence
    plan["placement"]["template_reference"] = reference
    plan["placement"]["pg_hints"] = {}
    plan["placement"]["coordinate_status"] = "template_reference_only; target_geometry_deferred"
    positions = {p["id"]: p for p in reference["instances"]}
    for row in plan["placement"]["instances"]:
        row["reference_placement"] = positions.get(row["id"])
    out["preview_digest"] = digest(plan)
    return out
