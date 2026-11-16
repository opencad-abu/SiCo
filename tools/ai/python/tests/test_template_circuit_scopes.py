"""Global references can become explicit ports only through a recorded mapping."""

import copy

import pytest
from template_circuit_fixture import geometry_args, persisted_case

from cadai.circuit_create import build_create_call
from cadai.circuit_generic import checked_preview
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_circuit import prepare_template


def localized(tmp_path, monkeypatch, change=None):
    args, record = persisted_case(tmp_path, monkeypatch, change)
    ids = {n["source_name"]: n["id"] for n in record["topology"]["nets"]}
    args["net_scope_map"] = {ids["vdd!"]: "local", ids["gnd!"]: "local"}
    args["net_map"].update({ids["vdd!"]: "VDD", ids["gnd!"]: "VSS"})
    args["port_map"] = {"VDD": "VDD", "VSS": "VSS"}
    return args, record, ids


def test_localized_supply_preserves_endpoints_and_creation_provenance(tmp_path, monkeypatch):
    args, record, ids = localized(tmp_path, monkeypatch)
    original = copy.deepcopy(record)
    result = prepare_template(args, workspace=tmp_path)
    assert all(n["scope"] == "local" for n in result["spec"]["nets"])
    assert result["spec"]["instances"][0]["connections"] == dict(G="A", D="Y", S="VSS", B="VSS")
    evidence = result["plan"]["template_evidence"]["net_scope_changes"]
    assert {r["source_name"]: r["target_name"] for r in evidence} == {"gnd!": "VSS", "vdd!": "VDD"}
    assert not result["plan"]["template_evidence"]["electrical_equivalence_verified"]
    request = geometry_args(result)
    request["layout"]["ports"]["VDD"] = request["layout"]["ports"].pop("vdd!")
    request["layout"]["ports"]["VSS"] = request["layout"]["ports"].pop("gnd!")
    request["layout"]["grid"] = 0.0625
    geometry = preview_geometry(**request, workspace=tmp_path)
    request.update(
        geometry_plan_digest=geometry["geometry_plan_digest"], task_ref="task:t", request_id="t"
    )
    assert "gnd!" not in build_create_call("prepare_circuit_creation", request, workspace=tmp_path)
    assert record == original
    # A later caller cannot omit the conversion or change its target while keeping the mapping.
    for field in ("net_scope_map", "net_map"):
        bad = copy.deepcopy(result["template_use"])
        if field == "net_scope_map":
            bad.pop(field)
        else:
            bad[field][ids["vdd!"]] = "OTHER"
        with pytest.raises(CircuitSpecError):
            checked_preview(
                dict(spec=result["spec"], bindings=args["bindings"], template_use=bad),
                workspace=tmp_path,
            )


@pytest.mark.parametrize(
    "case", ["local", "unknown", "ground", "global_name", "merge", "without_port"]
)
def test_scope_conversion_refuses_unrepresentable_semantics(tmp_path, monkeypatch, case):
    def change(rows):
        if case == "without_port":
            rows[:] = [r for r in rows if not (r["kind"] == "terminal" and r.get("name") == "VDD")]

    args, _, ids = localized(tmp_path, monkeypatch, change)
    if case == "local":
        args["net_scope_map"][ids["in"]] = "local"
    elif case == "unknown":
        args["net_scope_map"]["n999"] = "local"
    elif case == "ground":
        args["net_map"][ids["vdd!"]] = "0"
    elif case == "global_name":
        args["net_map"][ids["vdd!"]] = "VDD!"
    elif case == "merge":
        args["net_map"][ids["vdd!"]] = "VSS"
    else:
        args["port_map"].pop("VDD")
    with pytest.raises(CircuitSpecError):
        prepare_template(args, workspace=tmp_path)


def test_symbol_uses_same_local_boundary_and_records_conversion(tmp_path, monkeypatch):
    from cadai.template_symbol_plan import make_plan

    args, record, _ = localized(tmp_path, monkeypatch)
    plan = make_plan(
        record,
        dict(
            library="work",
            cell="dut",
            port_map=args["port_map"],
            net_scope_map=args["net_scope_map"],
        ),
    )
    assert all(p["global_net"] is None for p in plan["ports"])
    assert all(row[3] is None for row in plan["rows"])
    assert len(plan["net_scope_changes"]) == 2
    legacy = make_plan(record, dict(library="work", cell="dut", port_map={}))
    assert sum(p["global_net"] is not None for p in legacy["ports"]) == 2
    assert legacy["preview_digest"] != plan["preview_digest"]
