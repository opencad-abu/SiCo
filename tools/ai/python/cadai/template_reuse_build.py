"""Construct a new private v3 reference from explicit reviewed classification evidence."""

import copy
from collections import Counter

from .circuit_spec_schema import MASTER, validate
from .template_build import _publish, summary
from .template_classification import EXPLICIT_SEMANTICS
from .template_graph import fingerprint
from .template_reuse_schema import validate_record
from .template_schema import SCHEMA_V3, TemplateError, digest
from .template_source_qualification import qualify_source

RULE_VERSION = "20260919.reuse-contract.v1"


def content_ref(record):
    return "tpl_" + digest(
        {k: v for k, v in record.items() if k not in {"template_ref", "summary"}}
    )


def make_reusable(base, contract, classifications, granularity="block"):
    """No OA reads, inferred device names, global policy or source-parameter copying."""
    result = copy.deepcopy(base)
    devices = result["topology"]["devices"]
    if set(classifications) != {d["id"] for d in devices}:
        raise TemplateError("classification evidence must cover every source device")
    for device in devices:
        source_role = device["role"]
        if source_role == "hierarchy":
            raise TemplateError("classification cannot promote hierarchy source roles")
        if source_role not in {"device", "unknown"}:
            raise TemplateError("classification cannot resolve unsupported source role")
        evidence = classifications[device["id"]]
        validate(evidence, MASTER["properties"]["classification"], "source classification")
        if device.get("classification_evidence") and device["classification_evidence"] != evidence:
            raise TemplateError("classification conflicts with captured source evidence")
        if (base["source"].get("classification_semantics") in EXPLICIT_SEMANTICS
                and source_role == "device" and evidence["kind"] != device["kind"]):
            raise TemplateError("classification conflicts with observed device kind")
        if evidence["kind"] == "unknown":
            raise TemplateError("classification evidence cannot leave source kind unknown")
        device["source_role"] = source_role
        device["source_kind"] = device["kind"]
        device["source_attributes"] = copy.deepcopy(device.get("attributes", {}))
        # Explicit classification evidence is the reviewed resolution for a
        # legacy unknown role; it is not a library-name based promotion.
        device["role"] = "device"
        device.update(kind=evidence["kind"], attributes=copy.deepcopy(evidence["attributes"]),
                      classification_evidence=copy.deepcopy(evidence))
    # Complete explicit classification resolves only the matching unknown-type
    # gaps. Hierarchy, missing masters/terminals and electrical proof remain gates.
    resolved_names = {d["source_name"] for d in devices if d.get("classification_evidence")}
    result["topology"]["gaps"] = [
        gap for gap in result["topology"].get("gaps", [])
        if not (gap.get("code") == "unknown_device_type" and gap.get("instance") in resolved_names)
    ]
    result = qualify_source(result)
    result["topology"]["counts"]["device_kinds"] = dict(Counter(d["kind"] for d in devices))
    result["topology"]["fingerprint"] = fingerprint(result["topology"])
    result.update(schema_version=SCHEMA_V3, rule_version=RULE_VERSION,
                  base_template_ref=base["template_ref"], granularity=granularity,
                  reuse_contract=copy.deepcopy(contract))
    result["template_ref"] = content_ref(result)
    result["summary"] = summary(result)
    validate_record(result)
    return result


def publish_reusable(records, output):
    """Build one immutable SQLite package member using the existing writer."""
    for record in records:
        validate_record(record)
        # Structural v3 readability is deliberately weaker than reuse
        # eligibility.  Re-run the source proof at the publication boundary so
        # a hand-edited qualification or template_ref cannot be promoted.
        from .template_source_qualification import require_qualified

        require_qualified(record)
        if record["rule_version"] != RULE_VERSION or content_ref(record) != record["template_ref"]:
            raise TemplateError("v3 publication content identity mismatch")
        if summary(record) != record["summary"]:
            raise TemplateError("v3 publication summary mismatch")
    return _publish(records, output, SCHEMA_V3)
