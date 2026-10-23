"""Independent capability receipts, derived only from checks actually executed."""

from ..template_schema import TemplateError
from ..template_source_qualification import topology_qualification_issues
from ..template_topology import electrical_issues
from .topology import graph

# ``resolved`` is only meaningful for inherited connections: the expressions
# were captured verbatim and their declared defaults agree with the captured nets.
ACCEPTED = {
    "hierarchy": {"absent"},
    "arrays": {"absent"},
    "buses": {"absent"},
    "inherited_connections": {"absent", "resolved"},
}

PROFILES = (
    "topology-v1",
    "whole-reuse-v1",
    "core-reuse-v1",
    "placement-reference-v1",
    "routing-reference-v1",
    "symbol-reference-v1",
    "layout-reference-v1",
)


def evaluate(record, docs, *, current=False, sources=None, evidence_required=None):
    checks = {
        name: dict(result="unknown", reason="Capability validation has not run")
        for name in PROFILES
    }
    requested = docs["collection"]["scope"]["requested_assets"]
    for asset in ("symbol", "layout"):
        if asset not in requested:
            checks[asset + "-reference-v1"] = dict(
                result="not_applicable", reason="Asset not requested"
            )
    if current and "symbol" in docs:
        from .symbol_checks import issues as symbol_issues

        if evidence_required is None:
            evidence_required = True
        reasons = symbol_issues(
            docs,
            record["assets"]["symbol"]["ports"],
            sources=sources,
            evidence_required=evidence_required,
        )
        checks["symbol-reference-v1"] = (
            dict(result="unknown", reason="Symbol interface/geometry has gaps")
            if reasons
            else dict(result="passed")
        )
    if "topology" not in docs:
        return checks
    issues = topology_qualification_issues(record)
    try:
        issues += electrical_issues(graph(docs["topology"]))
    except TemplateError as exc:
        issues.append(dict(code="unsupported_feature", message=str(exc)))
    observations = docs["collection"]["assets"].get("schematic", {}).get("observations", {})
    if any(
        observations.get(k) not in ACCEPTED[k]
        for k in ("hierarchy", "arrays", "buses", "inherited_connections")
    ):
        issues.append(dict(code="unknown_or_unsupported_electrical_observation"))
    unsupported = [
        g
        for g in docs["collection"]["gaps"].values()
        if g["part"] == "topology.json" and g["code"] == "unsupported_feature"
    ]
    checks["topology-v1"] = (
        dict(result="unknown", reason="Electrical evidence is incomplete or unsupported")
        if issues or unsupported
        else dict(result="passed")
    )
    if "reuse" in docs and not issues and not unsupported:
        mode = docs["reuse"]["mode"]
        checks[mode + "-reuse-v1"] = dict(result="passed")
        other = "core" if mode == "whole" else "whole"
        checks[other + "-reuse-v1"] = dict(result="not_applicable", reason="Different reuse mode")
    elif "reuse" not in docs:
        for mode in ("core", "whole"):
            checks[mode + "-reuse-v1"] = dict(
                result="not_applicable", reason="No validated reuse contract"
            )
    place = docs.get("placement", {})
    if checks["topology-v1"]["result"] == "passed" and place and "rounding" not in place:
        top = docs["topology"]
        expected = {
            (r["device"], r["terminal"]) if "device" in r else ("port", r["port"])
            for r in top["connections"].values()
        }
        observed = {
            (r["device"], r["terminal"]) if "device" in r else ("port", r["port"])
            for r in place["anchors"].values()
        }
        gaps = [g for g in docs["collection"]["gaps"].values() if g["part"] == "placement.json"]
        if set(place["instances"]) == set(top["devices"]) and expected == observed and not gaps:
            checks["placement-reference-v1"] = dict(result="passed")
    if current:
        from .route_checks import issues as routing_issues

        gaps = [g for g in docs["collection"]["gaps"].values() if g["part"] == "routing.json"]
        if (
            checks["placement-reference-v1"]["result"] == "passed"
            and not gaps
            and not routing_issues(docs)
        ):
            checks["routing-reference-v1"] = dict(result="passed")
    return checks


def receipt(
    record,
    docs,
    dependencies,
    sources,
    version,
    authorized,
    template_ref,
    at,
    request_key,
    capture_sha256,
):
    from .codec import encode, sha
    from .versions import CURRENT, LEGACY, symbol_interface_validator, validator

    checks = evaluate(record, docs, current=version != LEGACY, sources=sources)
    validator_name = validator(version)
    if version == CURRENT and docs.get("symbol", {}).get("source") == "symbol_interface":
        validator_name = symbol_interface_validator(version)
    if version == CURRENT and "layout" in docs:
        from .native import layout_issues

        if not layout_issues(docs, dependencies):
            checks["layout-reference-v1"] = dict(result="passed")
    if not authorized:
        for profile in ("whole-reuse-v1", "core-reuse-v1"):
            checks[profile] = dict(
                result="unknown", reason="Imported usage permission is unconfirmed"
            )
    # The immutable report is a revalidation recipe, not a second editable template.
    report = dict(
        template_ref=template_ref,
        request_key=request_key,
        capture_sha256=capture_sha256,
        validator=validator_name,
        checks=checks,
    )
    report_raw = encode(report)
    report_key = sha(report_raw)[7:]
    sources["validation"] = dict(
        kind="validation",
        at=at,
        summary="Flat collection capability checks",
        validator=validator_name,
        evidence_ref="validation:" + report_key,
    )
    docs["qualification"] = dict(
        source="validation",
        depends_on=["capture"],
        template_ref=template_ref,
        checks={
            name: row
            | (
                dict(at=at, validator=validator_name, evidence_ref="validation:" + report_key)
                if row["result"] in {"passed", "failed"}
                else {}
            )
            for name, row in checks.items()
        },
    )
    return report_raw
