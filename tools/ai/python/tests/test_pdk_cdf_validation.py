"""Durable background CDF validation, without deriving domains from sampled values."""

import pytest
from pdk_draft_fixture import approve, create, setup, submit

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.cdf_validation_cases import prepare
from cadai.pdk_standard.cdf_validation_native import evidence
from cadai.pdk_standard.cdf_validation_result import classify, coverage
from cadai.pdk_standard.jsonio import encode
from cadai.pdk_standard.workspace import select


def case(ident="base", purpose="baseline", **extra):
    return dict(
        id=ident,
        purpose=purpose,
        reason="Synthetic CDF verification",
        parameters={"w": "0.3"},
        observe=["w"],
        **extra,
    )


def outcome(value="0.3", **extra):
    known = dict(status="known", value=value)
    return dict(
        ok=True,
        saved=True,
        readback_verified=True,
        all_requested_match=True,
        cdf_values_restored=True,
        cdf_metadata_unchanged=True,
        window_open=False,
        callback_execution_attempted=True,
        stage="probe_saved",
        parameters=[dict(name="w", current=known)],
        trace=[dict(label="builtin:set:w", values={"w": known})],
        **extra,
    )


@pytest.fixture
def suite(tmp_path):
    service, bridge, _ = setup(tmp_path, 1)
    draft = create(service)
    approve(service, submit(service, draft))
    current = select(tmp_path, "synthetic", {})
    args = dict(
        library="synthetic",
        device="cell0",
        revision=current.revision,
        request_id="cdf-test",
        order=["w"],
        cases=[case(), case("other", "variation")],
    )
    validation = service.cdf_validation

    class Native:
        calls = 0

        def inspect(self, target, names):
            return {"cdf_ref": "cdf:test"}

        def probe(self, *args):
            self.calls += 1
            return outcome()

    validation.native = Native()
    return service, validation, args


def test_lifecycle_idempotence_cancel_and_paging(suite):
    service, validation, args = suite
    first = service.call("prepare_pdk_cdf_validation", args)
    assert service.call("prepare_pdk_cdf_validation", args) == first
    ref = dict(validation_ref=first["validation_ref"])
    result = service.call("advance_pdk_cdf_validation", ref)
    assert result["case"]["status"] == "accepted"
    assert result["case"]["target"]["library"] == "SicoTest"
    assert validation.native.calls == 1
    query = service.call("get_pdk_cdf_validation", dict(**ref, page_size=1))
    assert len(encode(query)) < 16384 and query["returned"] == 1
    service.call("cancel_pdk_cdf_validation", ref)
    assert service.call("advance_pdk_cdf_validation", ref)["counts"] == {
        "accepted": 1,
        "cancelled": 1,
    }
    assert validation.native.calls == 1
    with pytest.raises(PdkUnavailable, match="coverage incomplete"):
        service.call("finalize_pdk_cdf_validation", dict(**ref, apply_execution=True))


def test_interrupted_intent_is_never_replayed(suite):
    service, validation, args = suite
    result = validation.prepare(args)
    ref = result["validation_ref"]
    state = validation.store.load(ref)
    state["cases"]["base"]["status"] = "running"
    validation.store.save(state)
    result = validation.advance(dict(validation_ref=ref))
    assert result["status"] == "interrupted" and result["counts"]["uncertain"] == 1
    assert validation.native.calls == 0
    assert validation.advance(dict(validation_ref=ref)) == result


def test_transport_failure_retains_artifact_and_target(suite):
    service, validation, args = suite

    def broken(*args):
        raise TimeoutError("native outcome unknown")

    validation.native.probe = broken
    ref = validation.prepare(args)["validation_ref"]
    result = validation.advance(dict(validation_ref=ref))
    assert result["status"] == "interrupted"
    assert result["case"]["target"]["library"] == "SicoTest"
    assert result["case"]["status"] == "uncertain"
    state = validation.store.load(ref)
    from pathlib import Path

    assert (Path(state["evidence_root"]) / "base/failure.json").exists()


def test_reject_policy_conflicts_and_duplicate_requests(suite):
    service, validation, args = suite
    validation.prepare(args)
    with pytest.raises(PdkUnavailable, match="request ID conflicts"):
        validation.prepare(dict(args, order=["other"]))
    current = select(service.workspace, "synthetic", {})
    cdf = current.document("cell0/cdf.json")
    with pytest.raises(PdkUnavailable, match="every confirmed"):
        prepare(cdf, [], args["cases"])
    cdf["parameters"]["w"]["write"] = "deny"
    with pytest.raises(PdkUnavailable, match="denied"):
        prepare(cdf, [], args["cases"])


@pytest.mark.parametrize(
    "status,changes",
    [
        ("accepted", {}),
        ("unsafe", dict(window_open=True)),
        ("unsafe", dict(cdf_metadata_unchanged=False)),
        ("unsafe", dict(readback_verified=False)),
        ("rejected", dict(ok=False, saved=False, parameters=[])),
        (
            "adjusted",
            dict(
                ok=False,
                saved=False,
                parameters=[],
                trace=[
                    dict(label="builtin:set:w", values={"w": dict(status="known", value="0.4")})
                ],
            ),
        ),
        ("unsupported", dict(ok=False, saved=False, callback_execution_attempted=False, trace=[])),
    ],
)
def test_observed_classification(status, changes):
    result = outcome()
    result.update(changes)
    assert classify(result, case(), {})["status"] == status


def test_unexecuted_inputs_are_not_reported_as_adjusted():
    result = outcome()
    result.update(
        ok=False,
        saved=False,
        parameters=[],
        trace=[dict(label="builtin:set:first", values={"w": dict(status="known", value="0.2")})],
    )
    assert classify(result, case(), {})["adjusted_inputs"] == []


def test_transition_failure_uses_only_second_phase_checkpoints():
    result = outcome()
    result.update(
        ok=False,
        saved=False,
        stage="transition",
        parameters=[],
        trace=result["trace"] + [dict(label="transition:before", values={})],
    )
    current = case(transition={"w": "0.5"})
    assert classify(result, current, {})["status"] == "rejected"


def test_atomic_evidence_cannot_overwrite(tmp_path):
    artifact = evidence(tmp_path, "probe", {"ok": True})
    assert artifact["bytes"] > 0
    with pytest.raises(FileExistsError):
        evidence(tmp_path, "probe", {"ok": False})


def test_unknown_domain_never_inferred_from_accepted_samples(suite):
    service, validation, args = suite
    cdf = select(service.workspace, "synthetic", {}).document("cell0/cdf.json")
    cases = prepare(cdf, args["order"], args["cases"])
    for c in cases.values():
        c.update(classify(outcome(), c, {}))
    result = coverage(cdf, cases)
    assert not result["execution_eligible"] and result["domain_inferred"] is False
    assert "input_constraints_still_unknown" in result["missing"]


def test_finish_recovers_committed_publication(suite, monkeypatch):
    _, validation, args = suite
    ref = validation.prepare(args)["validation_ref"]
    state = validation.store.load(ref)
    state["finalizing"] = True
    validation.store.save(state)
    from cadai.pdk_standard import cdf_validation_publish

    monkeypatch.setattr(cdf_validation_publish, "recovered", lambda *a: "new-revision")
    result = validation.finish(dict(validation_ref=ref, apply_execution=True))
    assert result["execution_applied"] and result["revision"] == "new-revision"
    assert validation.finish(dict(validation_ref=ref)) == result


def test_declared_case_order_survives_canonical_storage(suite):
    _, validation, args = suite
    args["cases"] = [case("z_first"), case("a_second", "variation")]
    ref = validation.prepare(args)["validation_ref"]
    result = validation.advance(dict(validation_ref=ref))
    assert result["case"]["id"] == "z_first"


def test_native_rows_use_observed_oa_type(monkeypatch, tmp_path):
    from cadai.pdk_standard import cdf_validation_native
    from cadai.pdk_standard.cdf_validation_native import Native

    native = Native(None, tmp_path)
    captured = []
    monkeypatch.setattr(native, "exchange", lambda *args: captured.append(args) or {})
    monkeypatch.setattr(cdf_validation_native, "normalize", lambda raw, name: raw)
    c = case()
    c["parameters"] = {"w": 0.3}
    native.probe(
        "cdf:test",
        c,
        ["w"],
        dict(library="SicoTest", cell="test", view="schematic"),
        "request",
        {"parameters": [{"name": "w", "cdf_type": "string"}]},
    )
    assert captured[0][1][3] == [["w", "string", "0.3"]]
    with pytest.raises(PdkUnavailable, match="type is unsupported"):
        native.probe(
            "cdf:test",
            c,
            ["w"],
            dict(library="SicoTest", cell="test", view="schematic"),
            "request",
            {"parameters": []},
        )
