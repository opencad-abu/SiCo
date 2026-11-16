"""Source-checked execution publication, exact conflicts and retained evidence."""

from copy import deepcopy

import pytest
from pdk_draft_fixture import approve, create, setup, submit
from test_pdk_cdf_validation import case, outcome

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.cdf_validation_cases import prepare
from cadai.pdk_standard.cdf_validation_native import evidence
from cadai.pdk_standard.cdf_validation_publish import publish, recovered
from cadai.pdk_standard.cdf_validation_result import classify, coverage
from cadai.pdk_standard.overlay import publish as overlay
from cadai.pdk_standard.workspace import select


def prepared(tmp_path):
    service, _, _ = setup(tmp_path, 1)
    approve(service, submit(service, create(service)))
    current = select(tmp_path, "synthetic", {})
    cdf = current.document("cell0/cdf.json")
    cdf["parameters"]["w"]["domain"] = dict(kind="interval", min=0.1, max=0.9)
    overlay(current.base.root.parent, current, {"cell0/cdf.json": cdf})
    current = select(tmp_path, "synthetic", {})
    cdf = current.document("cell0/cdf.json")
    specs = [
        case(),
        case("varied", "variation"),
        case("edge", "boundary"),
        case("grid", "off_grid"),
    ]
    specs[1]["parameters"]["w"] = "0.4"
    cases = prepare(cdf, ["w"], specs)
    for key, c in cases.items():
        value = outcome(c["parameters"]["w"])
        c.update(classify(value, c, evidence(tmp_path / "evidence", key, value)))
    state = dict(
        validation_ref="pdk-cdf:" + "1" * 32,
        library="synthetic",
        device="cell0",
        target=dict(library="synthetic", cell="cell0", view="symbol"),
        order=["w"],
        cases=cases,
    )
    assert coverage(cdf, cases)["execution_eligible"]
    return current, cdf, state


def test_execution_publication_is_narrow_and_recoverable(tmp_path):
    current, cdf, state = prepared(tmp_path)
    before = deepcopy(cdf)
    rev = publish(
        tmp_path, {}, current, current.document("device.json")["items"]["cell0"], cdf, state
    )
    assert recovered(tmp_path, {}, state) == rev
    after = select(tmp_path, "synthetic", {}).document("cell0/cdf.json")
    assert after["execution"] == dict(
        mode="cdf", order=["w"], implementation_ref="builtin:cdf-callbacks:v1"
    )
    assert after["parameters"] == before["parameters"]


@pytest.mark.parametrize("failure", ["conflict", "tampered", "stale"])
def test_publication_failure_preserves_effective_revision(tmp_path, failure):
    current, cdf, state = prepared(tmp_path)
    if failure == "conflict":
        cdf["execution"] = dict(mode="literal")
    elif failure == "tampered":
        from pathlib import Path

        Path(state["cases"]["base"]["artifact"]["path"]).write_text("{}")
    else:
        overlay(current.base.root.parent, current, {"cell0/cdf.json": cdf})
    expected = select(tmp_path, "synthetic", {}).revision
    with pytest.raises(PdkUnavailable):
        publish(
            tmp_path, {}, current, current.document("device.json")["items"]["cell0"], cdf, state
        )
    assert select(tmp_path, "synthetic", {}).revision == expected


def test_accepted_sample_conflicting_with_known_domain_blocks_publication(tmp_path):
    _, cdf, state = prepared(tmp_path)
    cdf["parameters"]["w"]["domain"]["max"] = 0.35
    result = coverage(cdf, state["cases"])
    assert not result["execution_eligible"]
    assert "accepted_sample_conflicts_with_known_constraints" in result["missing"]
