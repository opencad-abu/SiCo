"""Apply dated, individual debt allowances and forbid new or growing debt."""

from __future__ import annotations

from datetime import date


def _entries(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported engineering debt schema")
    entries = payload.get("entries")
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise ValueError("engineering debt entries must be objects")
    if any(not isinstance(row.get("id"), str) or not row["id"] for row in entries):
        raise ValueError("engineering debt requires an exact finding id")
    by_id = {row["id"]: row for row in entries}
    if len(by_id) != len(entries):
        raise ValueError("duplicate engineering debt id")
    return by_id


def assess(findings, payload, *, previous, base_commit, today=None):
    today = today or date.today()
    entries = _entries(payload)
    old = _entries(previous) if previous is not None else None
    errors, accepted = [], []
    if old is None and payload.get("established_base") != base_commit:
        errors.append("initial debt registration requires its exact audited base commit")
    if previous is not None and payload.get("established_base") != previous.get("established_base"):
        errors.append("engineering debt establishment anchor cannot change")
    actual = {row["id"]: row for row in findings}
    if len(actual) != len(findings):
        errors.append("duplicate finding id")
    for key, entry in entries.items():
        before_errors = len(errors)
        for field in ("owner", "reason", "remediation", "exit_condition", "expires"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                errors.append(f"{key}: debt requires {field}")
        try:
            deadline = date.fromisoformat(entry["expires"])
            if deadline <= today:
                errors.append(f"{key}: debt expired on {deadline}")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{key}: invalid debt expiration date")
        before = old.get(key) if old is not None else None
        if old is not None:
            if before is None:
                errors.append(f"{key}: new debt allowance is forbidden")
            else:
                if entry.get("expires", "") > before.get("expires", ""):
                    errors.append(f"{key}: extending debt expiration is forbidden")
                if entry.get("fingerprint") != before.get("fingerprint"):
                    errors.append(f"{key}: replacing debt evidence is forbidden; complete the review")
                if ("ceiling" in before and (type(entry.get("ceiling")) is not int
                        or entry["ceiling"] > before["ceiling"])):
                    errors.append(f"{key}: increasing debt ceiling is forbidden")
        finding = actual.get(key)
        if finding is None:
            errors.append(f"{key}: remove resolved debt allowance")
            continue
        if "value" in finding:
            if type(entry.get("ceiling")) is not int:
                errors.append(f"{key}: numeric debt requires a ceiling")
            elif finding["value"] != entry["ceiling"]:
                direction = "exceeds" if finding["value"] > entry["ceiling"] else "lower"
                errors.append(f"{key}: {direction} debt ceiling {entry['ceiling']} to measured {finding['value']}")
        elif finding.get("fingerprint") != entry.get("fingerprint"):
            errors.append(f"{key}: debt evidence changed; review or fix the finding")
        if len(errors) == before_errors:
            accepted.append({**finding, "allowance": entry})
    unexpected = [row for key, row in actual.items() if key not in entries]
    errors.extend(f"unregistered finding: {row['message']}" for row in unexpected)
    return dict(errors=errors, accepted_debt=accepted, unregistered=unexpected,
                resolved=sorted(set(old or {}) - set(entries)))
