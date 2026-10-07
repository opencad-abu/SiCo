"""Report holdout blindness without exposing hidden values."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .ldo_qualification_status import M3_BLOCKED_INPUT
from .ldo_qualification_values import (
    qualification_canonical,
    qualification_copy_json,
    qualification_digest,
)


def validate_holdout_blindness(
    public_plan: Mapping[str, Any],
    *,
    hidden_holdout: Mapping[str, Any] | None = None,
    observable_payloads: Sequence[object] = (),
) -> dict[str, Any]:
    """Validate lock metadata and return only a digest/count summary."""

    findings: list[str] = []
    try:
        public = qualification_copy_json(public_plan)
    except ValueError as exc:
        return {"status": M3_BLOCKED_INPUT, "findings": [str(exc)]}
    if not isinstance(public, Mapping):
        return {"status": M3_BLOCKED_INPUT, "findings": ["public experiment plan must be an object"]}
    holdout = public.get("holdout")
    if not isinstance(holdout, Mapping) or holdout.get("locked") is not True or holdout.get("public_values_included") is not False:
        findings.append("public plan does not keep holdout locked")
    ids: tuple[str, ...] = ()
    values: tuple[Any, ...] = ()
    if hidden_holdout is not None:
        if not isinstance(hidden_holdout, Mapping):
            findings.append("hidden holdout must be an object")
        else:
            raw_ids = hidden_holdout.get("case_ids", ())
            raw_values = hidden_holdout.get("values", ())
            if not isinstance(raw_ids, (list, tuple)) or any(not isinstance(item, str) or not item for item in raw_ids):
                findings.append("hidden holdout case_ids are malformed")
            else:
                ids = tuple(str(item) for item in raw_ids)
            if not isinstance(raw_values, (list, tuple)):
                findings.append("hidden holdout values are malformed")
            else:
                try:
                    values = tuple(qualification_copy_json(item) for item in raw_values)
                except ValueError as exc:
                    findings.append(str(exc))
            if not findings:
                hidden_digest = qualification_digest({"case_ids": list(ids), "values": list(values)})
                # Search identifiers and scalar values without serializing the
                # hidden payload into the returned report.
                for payload_index, payload in enumerate((public, *observable_payloads)):
                    encoded = qualification_canonical(payload)
                    for identifier in ids:
                        if identifier in encoded:
                            findings.append("hidden holdout identifier leaked at payload[%d]" % payload_index)
                    for value in values:
                        if qualification_canonical(value) in encoded:
                            findings.append("hidden holdout value leaked at payload[%d]" % payload_index)
            else:
                hidden_digest = None
    else:
        hidden_digest = None
    summary = {
        "locked": not findings or not any("holdout" in item for item in findings),
        "public_values_included": False,
        "case_count": len(ids),
        "identifier_count": len(ids),
        "holdout_sha256": hidden_digest,
    }
    return {
        "status": "PASS" if not findings else M3_BLOCKED_INPUT,
        "summary": summary,
        "findings": sorted(set(findings)),
    }
