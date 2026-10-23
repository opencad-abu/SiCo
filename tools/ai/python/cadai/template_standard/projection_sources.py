"""Public provenance profile and explicit master publication decisions."""

import copy

from .codec import VERSION, fail, jcs, sha
from .projection_content import ENV, label
from .validate import DIGEST

METHOD = "sico-standard-projection-v1"
VALIDATOR = "sico-standard-projection-checks-v1"


def masters(item, maps, level, policy):
    """An exact target+fingerprint allowlist is required; existence grants nothing."""
    if (
        not isinstance(policy, dict)
        or set(policy) != {"masters"}
        or not isinstance(policy["masters"], list)
    ):
        fail("master publication policy requires a masters list")
    approved = []
    for row in policy["masters"]:
        if not isinstance(row, dict) or set(row) != {"target", "fingerprint"}:
            fail("master publication decision requires target and fingerprint")
        target = row["target"]
        if not isinstance(target, dict) or set(target) != {"library", "cell", "view"}:
            fail("invalid master publication target")
        for value in target.values():
            label(value)
        if not isinstance(row["fingerprint"], str) or not DIGEST.fullmatch(row["fingerprint"]):
            fail("master publication requires a fixed fingerprint")
        approved.append(row)
    if level == "L1":
        return {}
    result = {}
    for key, ident in maps.get(("topology", "masters"), {}).items():
        row = item["documents"]["topology"]["masters"][key]
        dep = item["package"]["dependencies"][row["dependency"]]
        candidate = {k: dep[k] for k in ("target", "fingerprint")}
        if dep["kind"] != "master" or candidate not in approved:
            fail("master fingerprint is unknown or not approved for publication")
        from ..template_native_content import METHOD as NATIVE_METHOD

        if dep["method"] != NATIVE_METHOD:
            fail("unsupported publishable master fingerprint method")
        result[ident] = copy.deepcopy(dep)
    return result


def metadata(base_ref, level, use, at, evidence_digest, docs, master_dependencies):
    key = evidence_digest[7:]
    dependencies = dict(
        capture=dict(
            kind="file",
            target=dict(file_ref="capture"),
            fingerprint=evidence_digest,
            method="sha256-bytes-v1",
        ),
        **master_dependencies,
    )
    sources = dict(
        evidence=dict(
            kind="document",
            at=at,
            summary="Opaque controlled projection evidence",
            file_ref="capture",
            locator="projection-input-v1",
        ),
        capture=dict(
            kind="capture",
            at=at,
            summary="Abstract electrical graph digest",
            collector=METHOD,
            scope_digest=sha(jcs(docs.get("topology", {}))),
        ),
        projection=dict(
            kind="derived",
            at=at,
            summary="Sanitized standard semantic facts",
            inputs=["capture", "evidence"],
            method=METHOD,
        ),
        intent=dict(
            kind="user",
            at=at,
            summary="Preserved use and exact master publication decisions",
            confirmed_by="requesting-user",
            scope_digest=sha(jcs(dict(use=use, masters=master_dependencies))),
            evidence_ref="projection-input:" + key,
        ),
    )
    entry = dict(
        name="Circuit " + level,
        description="Sanitized circuit reference",
        categories=["reference"],
        granularity="unknown",
        detail_level=level,
        dir="template",
        use=copy.deepcopy(use),
        derived_from=[base_ref],
    )
    entry["reason"] = {k: "Source use decision retained" for k, v in use.items() if v != "allow"}
    tables = dict(
        sources=dict(items=sources),
        template=dict(**ENV, evidence={"/items/main/use": "intent"}, items=dict(main=entry)),
        category=dict(**ENV, items=dict(reference=dict(name="reference"))),
        file=dict(
            **ENV,
            items=dict(
                capture=dict(
                    kind="private_evidence",
                    root="private",
                    path="evidence/" + key + ".projection-input.json",
                    purpose="Controlled projection evidence",
                    dependency="capture",
                )
            ),
        ),
    )
    package = dict(
        format="sico.circuit-template.package",
        schema_version=VERSION,
        package_id="projection-" + key[:24] + "-" + level,
        revision="pending",
        created_at=at,
        dependencies=dependencies,
        pdks={},
        rules={},
        files={},
        required_features=[],
    )
    return package, tables
