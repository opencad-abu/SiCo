"""Dimension scaling and complete selector constraints reject plausible bad facts."""

from copy import deepcopy

import pytest
from pdk_standard_fixture import replace, update
from test_pdk_parameter_facts import args, collect, fact, fixture

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.fact_units import convert, scale
from cadai.pdk_standard.workspace import select


@pytest.mark.parametrize(
    "value,source,target,expected",
    [
        (2.5, "um", "m", 2.5e-6),
        (4, "µm2", "m2", 4e-12),
        (2.5, "kOhm", "Ohm", 2500),
        (1, "mA", "A", 0.001),
        (1, "1", "1", 1),
    ],
)
def test_explicit_decimal_scaling(value, source, target, expected):
    assert convert(value, source, target) == expected


@pytest.mark.parametrize(
    "field,value,source,target",
    [
        ("domain", {"kind": "unbounded", "reason": "anything"}, "m", "V"),
        ("domain", {"kind": "set", "values": ["A"]}, "m", "V"),
        ("default", 1, "K", "degC"),
        ("default", "1u", "um", "m"),
        ("default", 1e-320, "pm2", "m2"),
    ],
)
def test_wrong_dimension_and_unsafe_conversion(field, value, source, target):
    with pytest.raises(PdkUnavailable):
        scale(field, value, source, target)


def set_rules(tmp_path, ops, file="cell0/cdf.json"):
    current = select(tmp_path, "synthetic", {})
    service, draft = update(tmp_path, current, ops, file)
    service.apply({"update_ref": draft["update_ref"]})


def test_denied_cell_and_dependency_facts_without_policy_expansion(tmp_path):
    session, _, _ = fixture(tmp_path)
    set_rules(tmp_path, [replace("/items/cell0/use/circuit", "deny")], "device.json")
    set_rules(
        tmp_path,
        [
            {"op": "add", "path": "/parameters/w/depends_on", "value": ["p1"]},
            replace("/parameters/p1/unit", {"state": "unknown", "reason": "Unavailable"}),
        ],
    )
    before = select(tmp_path, "synthetic", {})
    result = collect(session, [fact("unit", parameter="p1", value="1")])
    assert result["status"] == "ok", result
    after = select(tmp_path, "synthetic", {})
    assert before.document("device.json") == after.document("device.json")
    assert after.document("cell0/cdf.json")["parameters"]["p1"]["write"] == "unknown"
    assert collect(session, [fact(parameter="p1")], "extra-detail")["status"] == "conflict"


def test_unit_and_domain_one_batch_and_grid_default_conflict(tmp_path):
    session, _, _ = fixture(tmp_path)
    set_rules(
        tmp_path, [replace("/parameters/w/unit", {"state": "unknown", "reason": "Unavailable"})]
    )
    result = collect(session, [fact(), fact("unit", value="m")])
    assert result["status"] == "ok" and result["collected_fields"] == 2, result
    revision = result["revision"]
    result = collect(
        session,
        [fact("grid", value={"origin": 0, "step": 0.2}, input_unit="m", complete=True)],
        "bad-grid",
    )
    assert result["status"] == "conflict"
    assert select(tmp_path, "synthetic", {}).revision == revision


def conditional():
    return {
        "kind": "conditional",
        "cases": [
            {
                "when": {"op": "eq", "left": {"param": "p1"}, "right": value},
                "domain": {"kind": "interval", "min": 100000, "max": 500000},
            }
            for value in [0.3, 0.4]
        ],
    }


@pytest.mark.parametrize("problem", ["none", "overlap", "uncovered", "default", "self"])
def test_all_selector_combinations_and_units(tmp_path, problem):
    session, _, _ = fixture(tmp_path)
    set_rules(tmp_path, [replace("/parameters/p1/domain", {"kind": "set", "values": [0.3, 0.4]})])
    domain = conditional()
    if problem == "overlap":
        domain["cases"].append(deepcopy(domain["cases"][1]))
    elif problem == "uncovered":
        domain["cases"].pop()
    elif problem == "default":
        domain["cases"][1]["domain"]["min"] = 400000
    elif problem == "self":
        domain["cases"][0]["when"]["left"]["param"] = "w"
    result = collect(session, [fact(value=domain)])
    assert result["status"] == ("ok" if problem == "none" else "conflict"), result
    if problem == "none":
        effective = select(tmp_path, "synthetic", {}).document("cell0/cdf.json")["parameters"]["w"][
            "domain"
        ]
        assert effective["cases"][0]["when"]["right"] == 0.3
        assert effective["cases"][0]["domain"]["min"] == 0.1


def test_explicit_unbounded_side_and_noop(tmp_path):
    session, bridge, _ = fixture(tmp_path)
    record = fact(value={"kind": "interval", "min": 100000}, unbounded_sides=["max"])
    first = collect(session, [record])
    assert first["status"] == "ok", first
    again = collect(session, [record], "no-op")
    assert again["collected_fields"] == 0 and again["revision"] == first["revision"]
    assert (
        collect(session, [fact("unit", value="V", expected="m")], "relabel")["status"] == "conflict"
    )


def test_late_document_change_and_concurrent_update(tmp_path, monkeypatch):
    session, _, manual = fixture(tmp_path)
    from cadai.pdk_standard import fact_collection

    validate = fact_collection.validate_sources

    def changed(*a, **kw):
        result = validate(*a, **kw)
        manual.write_text("Changed during source validation")
        return result

    monkeypatch.setattr(fact_collection, "validate_sources", changed)
    before = args(session)["revision"]
    result = collect(session)
    assert result["status"] == "conflict"
    assert result["error"]["code"] == "pdk_source_changed"
    assert select(tmp_path, "synthetic", {}).revision == before


def test_unsupported_domain_demoted_with_reason_and_exact_prior_value(tmp_path):
    session, _, _ = fixture(tmp_path)
    assert collect(session)["status"] == "ok"
    old = {"kind": "interval", "min": 0.1, "max": 0.5}
    reason = "Reviewed source provides only a model characterization range"
    record = fact(
        disposition="unresolved",
        basis="incomplete",
        reason=reason,
        value={"kind": "unknown", "reason": reason},
        expected=old,
    )
    result = collect(session, [record], "correct-range")
    assert result["status"] == "incomplete" and result["unresolved_records"] == 1
    p = select(tmp_path, "synthetic", {}).document("cell0/cdf.json")["parameters"]["w"]
    assert p["domain"] == record["value"] and p["write"] == "allow"
    assert collect(session, [record], "stale-review")["status"] == "conflict"


def test_concurrent_revision_and_index_write_failure(tmp_path, monkeypatch):
    session, _, _ = fixture(tmp_path)
    before = args(session)["revision"]
    from cadai.pdk_standard import fact_collection, overlay

    real, reads = fact_collection.select, []

    def changed(*args):
        package = real(*args)
        reads.append(1)
        if len(reads) == 2:
            package.revision = "concurrent"
        return package

    with monkeypatch.context() as patch:
        patch.setattr(fact_collection, "select", changed)
        result = collect(session)
    assert result["status"] == "conflict" and "Concurrent" in result["error"]["message"]
    with monkeypatch.context() as patch:
        patch.setattr(overlay, "atomic", lambda *a: (_ for _ in ()).throw(OSError("index failed")))
        with pytest.raises(OSError, match="index failed"):
            collect(session, request="index-write")
    assert select(tmp_path, "synthetic", {}).revision == before


def test_duplicate_and_unknown_definition_rejected(tmp_path):
    session, _, _ = fixture(tmp_path)
    pending = fact(
        disposition="unresolved",
        basis="incomplete",
        reason="unavailable",
        value={"kind": "unknown", "reason": "unavailable"},
    )
    pending["id"] = "different"
    assert collect(session, [fact(), pending])["status"] == "conflict"
    unknown = fact(
        "default", value={"state": "unknown", "reason": "not established"}, input_unit="m"
    )
    assert collect(session, [unknown], "unknown-definition")["status"] == "conflict"


def test_definition_ordering_and_unresolved_type(tmp_path):
    session, _, _ = fixture(tmp_path)
    set_rules(
        tmp_path, [replace("/parameters/w/type", {"state": "unknown", "reason": "Unavailable"})]
    )
    assert collect(session)["status"] == "conflict"
    result = collect(session, [fact(), fact("type", value="number")], "type-first")
    assert result["status"] == "ok" and result["collected_fields"] == 2


def test_invalid_unit_object_and_public_host_contract(tmp_path):
    from sico.adapters.circuit import NAMES

    from cadai.mcp_pdk_handler import dispatch_pdk
    from cadai.pdk_errors import PdkArgumentError
    from cadai.pdk_standard.tools import arguments

    assert {"collect_pdk_parameter_facts", "get_pdk_fact_report"} <= set(NAMES)
    session, _, _ = fixture(tmp_path)
    result = collect(session, [fact("unit", value={"unit": "m"})])
    assert result["status"] == "conflict"
    request = args(session, request="mcp")
    ok, result = dispatch_pdk(
        "collect_pdk_parameter_facts", request, session=session, bindings=None, client=None
    )
    assert ok and result["status"] == "ok"
    with pytest.raises(PdkArgumentError):
        arguments(
            "collect_pdk_parameter_facts", {**request, "facts": [fact("write", value="allow")]}
        )
    with pytest.raises(PdkArgumentError):
        arguments("collect_pdk_parameter_facts", {**request, "facts": [fact()] * 33})
    with pytest.raises(PdkArgumentError):
        arguments("collect_pdk_parameter_facts", {**request, "facts": [fact(value="x" * 16384)]})


def test_selector_enlargement_cannot_leave_existing_domain_uncovered(tmp_path):
    session, _, _ = fixture(tmp_path)
    set_rules(
        tmp_path,
        [
            replace("/parameters/p1/domain", {"kind": "set", "values": [0.3, 0.4]}),
            replace("/parameters/p1/write", "allow"),
            replace("/parameters/p1/requirement", "explicit"),
        ],
    )
    assert collect(session, [fact(value=conditional())])["status"] == "ok"
    before = args(session)["revision"]
    record = fact(
        parameter="p1",
        input_unit="m",
        expected={"kind": "set", "values": [0.3, 0.4]},
        value={"kind": "set", "values": [0.3, 0.4, 0.5]},
    )
    result = collect(session, [record], "selector-change")
    assert result["status"] == "conflict" and "uncovered" in result["error"]["message"]
    assert select(tmp_path, "synthetic", {}).revision == before


def test_unknown_interval_does_not_hide_grid_default_conflict(tmp_path):
    session, _, _ = fixture(tmp_path)
    record = fact("grid", value={"origin": 0, "step": 0.2}, input_unit="m", complete=True)
    result = collect(session, [record])
    assert result["status"] == "conflict" and "grid" in result["error"]["message"]
