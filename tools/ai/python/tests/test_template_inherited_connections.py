"""Inherited-connection expressions qualify only when their defaults are verified."""

from cadai.template_source_inheritance import (
    parse,
    record_discharged,
    resolved,
    view_resolved,
)
from cadai.template_standard.content import inheritance_state

SOURCE = ["demo", "INV", "schematic"]


def view(terminals=(), signals=(), texts=None, signal_texts=None, target=SOURCE):
    return dict(
        target=list(target),
        oa_view_type="schematic",
        source_view="source",
        parameterized=False,
        hierarchy_count=0,
        instance_count=1,
        mosaic_count=0,
        terminal_expressions=list(terminals),
        signal_expressions=list(signals),
        terminal_expression_texts=dict(texts or {}),
        signal_expression_texts=dict(signal_texts or {}),
    )


def test_expression_parser_accepts_only_the_declared_form():
    assert parse("[@VSS:%:VSS!]") == {"parameter": "VSS", "scope": "%", "default": "VSS!"}
    assert parse(" [@VDD:%:VDD!] ") == {"parameter": "VDD", "scope": "%", "default": "VDD!"}
    assert parse("VDD!") is None
    assert parse("[@VSS:%]") is None
    assert parse(None) is None


def test_defaults_that_match_the_captured_nets_resolve():
    observations = view(
        terminals=["VSS", "VDD"],
        signals=["VSS!", "VDD!"],
        texts={"VSS": "[@VSS:%:VSS!]", "VDD": "[@VDD:%:VDD!]"},
        signal_texts={"VSS!": "[@VSS:%:VSS!]", "VDD!": "[@VDD:%:VDD!]"},
    )
    ok, reason = view_resolved(
        observations, {"VSS": "VSS!", "VDD": "VDD!"}, {"VSS!", "VDD!"}
    )
    assert ok and reason is None
    assert inheritance_state([observations], {"rows": [
        {"kind": "terminal", "name": "VSS", "net": "VSS!"},
        {"kind": "terminal", "name": "VDD", "net": "VDD!"},
        {"kind": "net", "name": "VSS!"},
        {"kind": "net", "name": "VDD!"},
    ]}) == "resolved"


def test_uncaptured_missing_or_disagreeing_expressions_stay_unresolved():
    missing = view(terminals=["VSS"], texts={})
    assert view_resolved(missing, {"VSS": "VSS!"}, set())[0] is False
    disagreeing = view(terminals=["VSS"], texts={"VSS": "[@VSS:%:VSS!]"})
    ok, reason = view_resolved(disagreeing, {"VSS": "n2"}, set())
    assert ok is False and "n2" in reason
    renamed = view(signals=["VDD!"], signal_texts={"VDD!": "[@VDD:%:OTHER]"})
    assert view_resolved(renamed, set(), {"VDD!"})[0] is False
    assert inheritance_state([missing], {"rows": []}) == "present"
    assert inheritance_state([view()], {"rows": []}) == "absent"


def test_master_views_only_require_a_parsable_expression():
    master = view(
        terminals=["B"],
        texts={"B": "[@VSS:%:VSS!]"},
        target=["gpdk045", "nmos1v", "symbol"],
    )
    master["source_view"] = "master"
    ok, reason = resolved([view(), master], {"VSS": "VSS!"}, {"VSS!"}, SOURCE)
    assert ok and reason is None
    master["terminal_expression_texts"] = {"B": "not-an-expression"}
    ok, reason = resolved([view(), master], {"VSS": "VSS!"}, {"VSS!"}, SOURCE)
    assert ok is False and "B" in reason


def test_reviewed_proof_discharges_only_verified_defaults():
    record = {
        "source": {
            "lib": "demo", "cell": "INV", "view": "schematic",
            "electrical_observation": {},
        },
        "topology": {
            "ports": [{"name": "VSS", "net": "n0"}],
            "nets": [{"id": "n0", "source_name": "VSS!"}],
        },
    }
    verified = view(terminals=["VSS"], texts={"VSS": "[@VSS:%:VSS!]"})
    assert record_discharged(record, [verified]) is True
    unverified = view(terminals=["VSS"], texts={"VSS": "[@VSS:%:OTHER]"})
    assert record_discharged(record, [unverified]) is False
    empty = view()
    assert record_discharged(record, [empty]) is True
