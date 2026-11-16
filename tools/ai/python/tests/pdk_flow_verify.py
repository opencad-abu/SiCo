"""Independent native/readback assertions for the formal PDK flow acceptance."""

import json

import pytest


def verify_flow(bridge, call, project, results):
    rows = call(
        "query_maestro_results",
        dict(result_ref=results["result_ref"], entity="outputs", limit=100),
        "comparison-values",
    )
    numeric = compare_outputs(rows["items"])
    history = (
        project
        / "simulation"
        / "smicFlowTest"
        / "formal_pdk_comparison"
        / "maestro"
        / "results"
        / "maestro"
        / results["history"]
    )
    netlists = [history / "1" / name / "netlist" / "input.scs" for name in ("direct", "configured")]
    assert len(netlists) == 2, netlists
    assert all(path.is_file() for path in netlists), netlists
    designs = []
    expected = [
        "I_archived_inv_0 (IN OUT VDD 0) archived_inv",
        "MN (OUT IN VSS VSS) n09_ckt w=1u l=60n nf=2",
        "MP (OUT IN VDD VDD) p09_ckt w=2u l=60n nf=2",
        "VSUP (VDD 0) vsource dc=0.9",
        "CL (OUT 0) capacitor c=5f",
    ]
    for path in netlists:
        wrapper = path.read_text()
        assert 'include "netlist"' in wrapper, path
        text = (path.parent / "netlist").read_text()
        assert all(line in text for line in expected), path
        assert all(word not in text for word in ("gpdk045", "VDD!", "VSS!")), path
        designs.append(
            "\n".join(
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.strip().startswith("//")
            )
        )
    assert designs[0] == designs[1]
    proof = json.loads(
        bridge.skill(
            "let((pfCv pfGrounds pfLabels pfOutputs pfTermExpr pfSigExpr) "
            'pfCv=dbOpenCellViewByType("smicFlowTest" "archived_inv_tb" "schematic" "" "r") '
            "pfGrounds=length(setof(pfI pfCv~>instances "
            'and(pfI~>libName=="basic" pfI~>cellName=="gnd"))) '
            "pfLabels=length(setof(pfS pfCv~>shapes "
            'and(pfS~>objType=="label" pfS~>theLabel=="gnd!"))) '
            "pfTermExpr=length(setof(pfT pfCv~>terminals dbGetTermNetExpr(pfT))) "
            "pfSigExpr=length(setof(pfN pfCv~>signals dbGetSigNetExpr(pfN))) dbClose(pfCv) "
            'pfCv=dbOpenCellViewByType("smicFlowTest" "archived_inv" "schematic" "" "r") '
            "pfOutputs=foreach(mapcar pfI setof(pfX pfCv~>instances "
            'and(pfX~>libName=="basic" pfX~>cellName=="opin")) pfI~>orient) dbClose(pfCv) '
            "aiCircuitJson(list(pfGrounds pfLabels pfTermExpr pfSigExpr pfOutputs)))"
        )
    )
    assert proof == [4, 0, 0, 0, ["R0"]], proof
    return dict(
        ground_and_pin_proof=proof,
        matching_outputs=numeric_values(numeric),
        netlists=[str(p) for p in netlists],
        numeric_tolerance_v=1e-9,
    )


def numeric_values(values):
    return [dict(test=t, output=n, value=v) for (t, n), v in sorted(values.items())]


def compare_outputs(rows):
    names = ("high", "low", "input_high", "input_low")
    expected = {(test, name) for test in ("direct", "configured") for name in names}
    assert len(rows) == len(expected), rows
    assert all(row["status"] == "scalar" for row in rows), rows
    numeric = {(row["test"], row["output"]): row["value"] for row in rows}
    assert set(numeric) == expected, rows
    for name in names:
        assert numeric["direct", name] == pytest.approx(
            numeric["configured", name], rel=0, abs=1e-9
        )
    return numeric
