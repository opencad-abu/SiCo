"""Private acceptance manifests: split integrity and auditable, non-inferred signoff."""

from cadai.circuit_spec_schema import BOOL, TEXT, array, enum, obj, validate
from cadai.template_reuse_schema import HEX
from cadai.template_schema import digest

CASE = obj({
    "id": TEXT, "family": TEXT, "source_digest": HEX, "input_digest": HEX,
    "split": enum("development", "held_out"),
    "label_source": enum("synthetic_structural_invariant", "human_reviewed"),
    "expected_status": enum("needs_mapping", "no_match", "inconclusive"),
    "scenario": TEXT,
})
MANIFEST = obj({
    "schema": enum("cad.template.acceptance-manifest.v1"),
    "cases": array(CASE, 10000, 1),
    "review": obj({"status": enum("pending", "signed"),
                   "reviewer": TEXT, "evidence_ref": TEXT, "scope": TEXT,
                   "independent_of_development": BOOL}, ("status",)),
})


def validate_manifest(manifest):
    validate(manifest, MANIFEST)
    ids, families, sources = set(), {}, {}
    for case in manifest["cases"]:
        if case["id"] in ids:
            raise ValueError("duplicate acceptance case ID")
        ids.add(case["id"])
        for owners, key in ((families, case["family"]), (sources, case["source_digest"])):
            if key in owners and owners[key] != case["split"]:
                raise ValueError("development/held_out family or source leakage")
            owners[key] = case["split"]
    review = manifest["review"]
    if review["status"] == "signed":
        required = {"reviewer", "evidence_ref", "scope", "independent_of_development"}
        if not required <= review.keys() or review["independent_of_development"] is not True:
            raise ValueError("signoff requires a reviewer, evidence, scope and independence")
        if any(c["label_source"] != "human_reviewed" for c in manifest["cases"]
               if c["split"] == "held_out"):
            raise ValueError("synthetic labels cannot be signed as expert acceptance")
    return manifest


def manifest_for(cases):
    result = {
        "schema": "cad.template.acceptance-manifest.v1",
        "cases": [{
            "id": c["id"], "family": c["family"],
            "source_digest": digest(c["candidates"]),
            "input_digest": digest(c), "scenario": c["id"].split("/")[-1],
            "split": "held_out" if c["split"] == "acceptance" else "development",
            "label_source": c["label_source"], "expected_status": c["expected_status"],
        } for c in cases],
        "review": {"status": "pending"},
    }
    return validate_manifest(result)


def acceptance_report(manifest, observations):
    """Missing results stay pending; hashes/status/recall violations stay failures."""
    validate_manifest(manifest)
    ids = {c["id"] for c in manifest["cases"]}
    if len({o["id"] for o in observations}) != len(observations):
        raise ValueError("duplicate observation")
    if any(o["id"] not in ids for o in observations):
        raise ValueError("observation is outside frozen manifest")
    observed = {o["id"]: o for o in observations}
    rows = []
    for case in manifest["cases"]:
        actual = observed.get(case["id"])
        passed = None if actual is None else (
            actual["input_digest"] == case["input_digest"]
            and actual["status"] == case["expected_status"]
            and actual["mapping_matches"] is True
            and actual["no_new_recall_loss"] is True
        )
        rows.append(dict(id=case["id"], split=case["split"], scenario=case["scenario"],
                         expected=case["expected_status"], passed=passed))

    def metric(selected):
        return dict(cases=len(selected), passed=sum(r["passed"] is True for r in selected),
                    pending=sum(r["passed"] is None for r in selected))

    held = [r for r in rows if r["split"] == "held_out"]
    return {
        "schema": "cad.template.acceptance-report.v1", "manifest_digest": digest(manifest),
        "human_expert_pending": manifest["review"]["status"] != "signed",
        "machine_passed": bool(held) and all(r["passed"] is True for r in held),
        "cases": rows, "held_out": metric(held),
        "held_out_families": len({c["family"] for c in manifest["cases"]
                                  if c["split"] == "held_out"}),
        "wrong_connection_rejection": metric([r for r in held
                                               if r["scenario"] == "wrong_connection"]),
        "unknown_preservation": metric([r for r in held if r["expected"] == "inconclusive"]),
        "failed": sum(r["passed"] is False for r in rows),
    }
