"""Design Symbol export contract and actual generic geometry consumption."""

import copy

import pytest

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.template_symbol import call_symbol
from cadai.template_symbol_binding import binding_result


def native():
    return dict(
        ok=True,
        symbol_ref="template-symbol:test",
        task_ref="task:test",
        template_ref="tpl_" + "a" * 64,
        preview_digest="b" * 64,
        target=["workLib", "arbitrary", "symbol"],
        library_path="/work/lib",
        signature="native saved snapshot",
        occupied_bbox=[[0, -1], [3, 2]],
        ports=[
            ["IN", "input", [0, 0], "left"],
            ["OUT", "output", [3, 0], "right"],
            ["VP", "inputOutput", [1.5, 2], "up"],
            ["VN", "inputOutput", [1.5, -1], "down"],
        ],
    )


def exported():
    return binding_result(native(), dict(master_id="design", instance_id="dut"))


def test_export_exact_geometry_revision_and_design_interface():
    result = exported()
    assert "signature" not in result
    assert result["master"]["kind"] == "design" and result["master"]["parameters"] == []
    assert result["geometry"]["master_revision"] == result["master"]["revision"]
    assert [t["anchors"][0]["escape"] for t in result["geometry"]["terminals"]] == [
        "left",
        "right",
        "up",
        "down",
    ]
    changed = native()
    changed["signature"] += " modified font"
    assert (
        binding_result(changed, dict(master_id="design", instance_id="dut"))["master"]["revision"]
        != result["master"]["revision"]
    )


@pytest.mark.parametrize("orientation", ["R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90"])
def test_export_feeds_generic_geometry_with_rotated_supply_stubs(orientation):
    result = exported()
    bindings = dict(
        schema="cad.circuit.bindings.v1",
        project_ref="project:test",
        snapshot_ref="snapshot:test",
        source_ref=result["source_ref"],
        evidence_kind=result["evidence_kind"],
        masters=[result["master"]],
    )
    spec = dict(
        schema="cad.circuit.spec.v1",
        project_ref="project:test",
        binding_snapshot="snapshot:test",
        kind="testbench",
        target=dict(library="workLib", cell="tb", view="schematic"),
        instances=[
            dict(
                id="dut",
                master="design",
                role="dut",
                parameters={},
                unconnected={},
                connections={p[0]: p[0] for p in native()["ports"]},
            )
        ],
        nets=[dict(name=p[0], scope="local") for p in native()["ports"]],
        ports=[],
        notes=[],
    )
    preview = preview_circuit(spec, bindings)
    geometry = dict(
        schema="cad.circuit.geometry.v1",
        project_ref="project:test",
        binding_snapshot="snapshot:test",
        binding_digest=preview["plan"]["binding_digest"],
        source_ref=result["source_ref"],
        evidence_kind="session_capture",
        coordinate_system="master_local_schematic_uu",
        instances=[result["geometry"]],
    )
    layout = dict(
        instances=dict(dut=dict(xy=[0, 0], orientation=orientation)),
        ports={},
        anchor_choices={},
        grid=0.0625,
        stub_length=0.25,
        clearance=0.125,
    )
    geo = preview_geometry(spec, bindings, preview["preview_digest"], geometry, layout)
    assert geo["plan"]["geometry_checks_passed"]
    assert geo["plan"]["instances"][0]["name"] == "I_arbitrary_0"
    broken = copy.deepcopy(geometry)
    broken["instances"][0]["terminals"][2]["anchors"][0]["escape"] = "down"
    with pytest.raises(CircuitSpecError, match="escape"):
        preview_geometry(spec, bindings, preview["preview_digest"], broken, layout)


@pytest.mark.parametrize("diagnostic", [{}, dict(output="*WARNING* BINDING\n",
                        output_truncated=False, output_capture="skill_ports")])
def test_mcp_binding_adapter_preserves_native_refusal(tmp_path, diagnostic):
    import json

    class Client:
        def call(self, method, args):
            assert method == "eval_skill_native"
            assert args["code"] == 'aiTemplateSymbolBinding("template-symbol:test")'
            return True, {"ok": True, "value": json.dumps({**native(), **diagnostic})}

    result = call_symbol(
        "get_symbol_binding",
        dict(symbol_ref="template-symbol:test", master_id="design", instance_id="dut"),
        workspace=tmp_path,
        client=Client(),
    )
    assert result == {**exported(), **diagnostic}

    class Refused:
        def call(self, method, args):
            return True, {
                "ok": True,
                "value": json.dumps(dict(ok=False, code="symbol_binding_unavailable")),
            }

    assert not call_symbol(
        "get_symbol_binding",
        dict(symbol_ref="template-symbol:test", master_id="design", instance_id="dut"),
        workspace=tmp_path,
        client=Refused(),
    )["ok"]
