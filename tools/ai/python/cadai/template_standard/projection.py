"""Publish independently qualified standard L1/L2/L3 packages from retained raw facts."""

import copy
from pathlib import Path

from ..template_schema import TemplateUnavailable
from .codec import encode, fail, sha
from .collect import private_bytes
from .identity import projection as identity
from .identity import reference
from .package import publish, verify
from .projection_content import LEVELS, documents
from .projection_sources import VALIDATOR, masters, metadata
from .qualification import PROFILES, evaluate


def checks_for(record, docs, level, use):
    checks = {
        p: dict(result="unknown", reason="Controlled source evidence unavailable") for p in PROFILES
    }
    if record is not None:
        # Projected documents carry sanitized projection provenance, not the raw
        # independent-symbol evidence row. Recheck interface/geometry, not that row.
        checks = evaluate(record, docs, current=True, evidence_required=False)
    for part in ("layout", "placement", "routing", "symbol"):
        if (
            part == "layout"
            or (level == "L1")
            or (part == "symbol" and part not in docs["collection"]["scope"]["requested_assets"])
        ):
            checks[part + "-reference-v1"] = dict(
                result="not_applicable", reason="Asset excluded by projection scope"
            )
    if "reuse" not in docs:
        for mode in ("whole", "core"):
            checks[mode + "-reuse-v1"] = dict(
                result="not_applicable", reason="No validated reuse contract"
            )
    elif use["reuse"] != "allow":
        for mode in ("whole", "core"):
            checks[mode + "-reuse-v1"] = dict(
                result="unknown", reason="Source use decision does not allow reuse"
            )
    else:
        other = "core" if docs["reuse"]["mode"] == "whole" else "whole"
        checks[other + "-reuse-v1"] = dict(result="not_applicable", reason="Different reuse mode")
    return checks


def compile_projection(item, level, policy, record):
    if item["entry"]["detail_level"] != "raw" or level not in LEVELS:
        fail("standard projection requires a raw ctpl_ reference and L1/L2/L3")
    docs, maps = documents(item["documents"], level)
    dependencies = masters(item, maps, level, policy)
    # Private immutable proof preserves the entire source closure, including fields
    # deliberately removed from the public level. Only its digest leaves this root.
    proof = dict(
        source_ref=item["entry"]["template_ref"],
        source=identity(item["package"], item["tables"], item["documents"], item["template_id"]),
        level=level,
        masters=dependencies,
        validator=VALIDATOR,
        source_replayed=record is not None,
    )
    proof_raw = encode(proof)
    at, use = item["package"]["created_at"], item["entry"]["use"]
    package, tables = metadata(
        item["entry"]["template_ref"], level, use, at, sha(proof_raw), docs, dependencies
    )
    if "reuse" in docs:
        from ..template_rules import rule_ref

        names = {}
        for key in docs["reuse"]["allowed_rules"]:
            row = item["package"]["rules"][key]
            installed = rule_ref(row["name"])
            expected = dict(
                name=installed["id"],
                version=installed["version"],
                implementation_digest="sha256:" + installed["implementation_digest"],
            )
            if row != expected:
                fail("projection requires installed rule identity")
            names[key] = row["name"]
            package["rules"][row["name"]] = copy.deepcopy(row)
        docs["reuse"]["allowed_rules"] = [names[k] for k in docs["reuse"]["allowed_rules"]]
    entry = tables["template"]["items"]["main"]
    entry["template_ref"] = reference(package, tables, docs, "main")
    checks = checks_for(record, docs, level, use)
    report = encode(
        dict(
            template_ref=entry["template_ref"],
            validator=VALIDATOR,
            evidence_digest=sha(proof_raw),
            checks=checks,
        )
    )
    receipt_ref = "validation:" + sha(report)[7:]
    tables["sources"]["items"]["validation"] = dict(
        kind="validation",
        at=at,
        summary="Independent sanitized projection capability checks",
        validator=VALIDATOR,
        evidence_ref=receipt_ref,
    )
    docs["qualification"] = dict(
        source="validation",
        depends_on=["capture"],
        template_ref=entry["template_ref"],
        checks={
            p: row
            | (
                dict(at=at, validator=VALIDATOR, evidence_ref=receipt_ref)
                if row["result"] in {"passed", "failed"}
                else {}
            )
            for p, row in checks.items()
        },
    )
    return package, tables, docs, proof_raw, report


def project(catalog, template_ref, level, *, output=None, master_policy=None):
    from .reader import locate
    from .replay import replay

    item, _ = locate(catalog, template_ref)
    if item["entry"]["detail_level"] != "raw":
        fail("projection input must be raw; use derived_from to select the original")
    record = None
    try:
        record = replay(item)[0]
    except (TemplateUnavailable, FileNotFoundError):
        pass  # No private proof: retain explicit unknown capability, never reuse a raw receipt.
    package, tables, docs, proof, report = compile_projection(
        item, level, dict(masters=[]) if master_policy is None else master_policy, record
    )
    private = catalog.ensure_private_destination()
    destination = Path(output).resolve() if output is not None else private
    if output is not None and (
        destination == private.resolve()
        or private.resolve() in destination.parents
        or destination in private.resolve().parents
    ):
        fail("public projection output must be separate from the private evidence tree")
    if output is not None:
        from .projection_publish import check_destination

        check_destination(destination, catalog)
    private_bytes(private, "evidence", ".projection-input.json", proof)
    private_bytes(private, "evidence", ".validation.json", report)
    verify(item["path"], item["observed"])
    path = publish(
        destination, package, tables, {"main": docs}, roots={}, public_only=output is not None
    )
    return dict(
        ok=True,
        template_ref=tables["template"]["items"]["main"]["template_ref"],
        base_template_ref=template_ref,
        detail_level=level,
        package=str(path),
        capabilities={k: v["result"] for k, v in docs["qualification"]["checks"].items()},
    )
