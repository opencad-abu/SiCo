"""Validate decisions on keyword candidates separately from legacy contracts."""

from __future__ import annotations

if __package__:
    from .a_philosophy_compat import validate_reference
    from .a_philosophy_exports import surface_digest
    from .a_philosophy_sources import read_python
else:
    from a_philosophy_compat import validate_reference
    from a_philosophy_exports import surface_digest
    from a_philosophy_sources import read_python


def review_candidates(root, candidates, registry, payload):
    """Review coverage is advisory; invalid evidence/record links fail the gate.

    A partial legacy registration never closes a whole-file review. Changed
    surfaces and newly detected candidates return to review, not to a blacklist.
    """
    errors, warnings, reports = [], [], []
    if payload.get("schema_version") != 1 or not isinstance(payload.get("reviews"), list):
        return ["unsupported compatibility review schema"], [], []
    contracts = {entry["legacy"]: entry for entry in registry}
    decisions = {}
    paths = {item["path"] for item in candidates}
    for item in payload["reviews"]:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("compatibility review requires an object and source path")
            continue
        path = item.get("path", "")
        if path in decisions:
            errors.append(f"duplicate compatibility review: {path}")
        decisions[path] = item
        if item.get("disposition") not in {"compatibility", "mixed", "canonical", "internal-tool"}:
            errors.append(f"{path}: invalid review disposition")
        for field in ("rationale", "surface_sha256"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{path}: missing review {field}")
        if not isinstance(item.get("records"), list) or not all(isinstance(r, str) for r in item["records"]):
            errors.append(f"{path}: records must be a string list")
            continue
        if item.get("disposition") in {"compatibility", "mixed"} and not item["records"]:
            errors.append(f"{path}: compatibility decision needs records")
        for legacy in item["records"]:
            record = contracts.get(legacy)
            if record is None or record["source"].partition(":")[0] != path:
                errors.append(f"{path}: missing/mismatched review contract {legacy}")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{path}: review requires evidence")
            evidence = []
        for ref in [path, *evidence]:
            try:
                if not isinstance(ref, str):
                    raise ValueError("evidence must be a path reference")
                validate_reference(root, ref)
            except (OSError, ValueError, SyntaxError) as exc:
                errors.append(f"{path}: {exc}")
        if path not in paths:
            warnings.append(f"compatibility review no longer matches a candidate: {path}")
    for candidate in candidates:
        path = candidate["path"]
        item = decisions.get(path)
        state = "unreviewed"
        if item:
            try:
                state = "reviewed" if surface_digest(read_python(root / path)) == item["surface_sha256"] else "stale"
            except (OSError, ValueError, SyntaxError):
                state = "stale"  # The AST/source check reports the hard failure.
        if state != "reviewed":
            warnings.append(f"compatibility candidate requires review ({state}): {path}")
        reports.append({**candidate, "review_state": state,
                        "disposition": item.get("disposition") if item else None})
    return errors, warnings, reports
