"""Mixed design/PDK composition and per-master validation remain generic."""

from copy import deepcopy

import pytest
from pdk_binding_fixture import fixture, prepare_args

from cadai.circuit_spec_schema import CircuitSpecError, digest
from cadai.pdk_binding import guards


def test_repeated_master_and_design_symbol_preserve_sources_without_redundant_revalidation():
    bridge, adapter, args = fixture()
    record = adapter.bind(args)
    prepared = prepare_args(record)
    repeated = dict(prepared["spec"]["instances"][0], id="M2", parameters={"w": "2u"})
    prepared["spec"]["instances"].append(repeated)
    prepared["geometry"]["instances"].append(
        dict(record["geometry"], instance="M2", parameters_digest=digest(repeated["parameters"]))
    )
    design = dict(deepcopy(record["master"]), id="module", kind="design")
    design.pop("pdk_binding")
    design["target"] = dict(library="projectWork", cell="block", view="symbol")
    design["revision"] = "owned-symbol"
    prepared["bindings"]["masters"].append(design)
    prepared["spec"]["instances"].append(dict(id="dut", master="module", parameters={}))
    bridge.calls.clear()
    adapter.validate_prepare(prepared)
    assert len([c for c in bridge.calls if c[0] == "device"]) == 1
    assert guards(prepared) == [record["source_ref"]]
    prepared["geometry"]["instances"][1]["master_revision"] = "other"
    with pytest.raises(CircuitSpecError, match="geometry differs"):
        adapter.validate_prepare(prepared)


def test_same_device_multiple_roles_validate_in_one_request():
    _, adapter, args = fixture()
    first = adapter.bind(args)
    requests = []
    original = adapter.session.call

    def traced(name, values):
        if name == "revalidate_pdk_bindings":
            requests.append(values)
        return original(name, values)

    adapter.session.call = traced
    second = adapter.bind(dict(args, master_id="alternate_role"))
    requests.clear()
    adapter.validate_execute([first["source_ref"], second["source_ref"]])
    assert requests == [dict(snapshot_ref=args["snapshot_ref"], device_refs=[args["device_ref"]])]


def test_flow_comparison_uses_actual_scalar_contract_and_absolute_tolerance():
    from pdk_flow_verify import compare_outputs

    rows = [
        dict(test=test, output=name, value=value, status="scalar")
        for test in ("direct", "configured")
        for name, value in (("high", 0.9), ("low", 0.0), ("input_high", 0.9), ("input_low", 0.0))
    ]
    assert len(compare_outputs(rows)) == 8
    rows[-2]["value"] += 1e-8
    with pytest.raises(AssertionError):
        compare_outputs(rows)
    rows[-2]["value"] = 0.9
    rows[-1]["status"] = "missing"
    with pytest.raises(AssertionError):
        compare_outputs(rows)
    with pytest.raises(AssertionError):
        compare_outputs(rows[:-1])


def test_flow_verifier_uses_exact_history_point_and_included_design(tmp_path):
    from pdk_flow_verify import verify_flow

    root = tmp_path / "simulation/smicFlowTest/formal_pdk_comparison/maestro/results/maestro"
    text = "\n".join(
        [
            "I_archived_inv_0 (IN OUT VDD 0) archived_inv",
            "MN (OUT IN VSS VSS) n09_ckt w=1u l=60n nf=2",
            "MP (OUT IN VDD VDD) p09_ckt w=2u l=60n nf=2",
            "VSUP (VDD 0) vsource dc=0.9",
            "CL (OUT 0) capacitor c=5f",
        ]
    )
    rows = [
        dict(test=t, output=n, value=v, status="scalar")
        for t in ("direct", "configured")
        for n, v in (("high", 0.9), ("low", 0), ("input_high", 0.9), ("input_low", 0))
    ]
    for path in (
        "history-2/1/direct",
        "history-2/1/configured",
        "history-2/psf/direct",
        ".tmpADEDir/direct",
        "history-1/1/direct",
    ):
        directory = root / path / "netlist"
        directory.mkdir(parents=True)
        (directory / "input.scs").write_text('include "netlist"\n')
        (directory / "netlist").write_text("// " + path + "\n" + text)

    class ProofFixture:
        def skill(self, code):
            return '[4,0,0,0,["R0"]]'

    def call(name, args, label):
        assert name == "query_maestro_results" and args["result_ref"] == "frozen-results"
        return dict(items=rows)

    result = verify_flow(
        ProofFixture(), call, tmp_path, dict(result_ref="frozen-results", history="history-2")
    )
    assert len(result["netlists"]) == 2
    changed = root / "history-2/1/configured/netlist/netlist"
    changed.write_text(text + "\nextra_device (OUT 0) resistor r=1K")
    with pytest.raises(AssertionError):
        verify_flow(
            ProofFixture(), call, tmp_path, dict(result_ref="frozen-results", history="history-2")
        )
