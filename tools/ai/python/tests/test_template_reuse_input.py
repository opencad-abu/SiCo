"""Public source-name intent compiles to the sole v3 contract and remains repairable."""

import copy
from pathlib import Path

import pytest
from template_adapt_fixture import geometry_request, target_case
from template_extract_fixture import request
from test_template_circuit_transport import rpc, unpack
from test_template_extract_reuse import extract

from cadai.mcp import McpServer
from cadai.template_catalog import TemplateCatalog
from cadai.template_circuit import prepare_template


def core_intent():
    return dict(mode="core", allow_rename=False, allow_omit_optional_groups=True,
                allow_add_boundary_group=False, core_instances=["M0"],
                optional_groups=[dict(id="load", instances=["M1"])],
                boundary_terminals=[dict(endpoint=dict(instance="M0", terminal=p),
                    allowed_device_kinds=["pmos"], min_additional_connections=0,
                    max_additional_connections=1) for p in ["D", "G"]])


def test_public_optional_contract_to_integrated_prepare(tmp_path):
    args = request() | dict(reuse=core_intent())
    before = copy.deepcopy(args)
    _, server, result = extract(tmp_path, args=args)
    assert result["ok"], result
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    contract = record["reuse_contract"]
    assert contract["core_devices"] == ["d0"]
    assert contract["optional_groups"] == [dict(id="load", devices=["d1"],
        boundary_connections=[dict(device="d1", terminal=p) for p in ["D", "G"]],
        omit=dict(remove_nets=[], remove_ports=[]))]
    assert [r["id"] for r in contract["allowed_rule_refs"]] == ["omit_optional_group"]
    target = target_case(record, omit=True)
    prepared = prepare_template(target, workspace=tmp_path)
    inputs = {k: target[k] for k in ("spec", "bindings", "template_ref", "mapping")}
    inputs.update(geometry=geometry_request(prepared)["geometry"],
                  placement=dict(clearance=0, routing=dict(mode="stub")),
                  options=dict(match_mode="core", allowed_tiers=["private"],
                               return_alternatives=0))
    blocked = unpack(rpc(server, "prepare_circuit_template_plan", inputs))
    assert blocked["status"] == "needs_adaptation", blocked
    assert any("allowed_rules" in row for row in blocked["next_action"]["input_alternatives"])
    ready = unpack(rpc(server, "prepare_circuit_template_plan", inputs | dict(
        allowed_rules=["omit_optional_group"])))
    assert ready["status"] == "ready_for_creation_prepare", ready
    use = ready["plan"]["template_use"]
    assert use["adaptations"][0]["diff"]["removed_devices"] == ["d1"]
    legacy = unpack(rpc(server, "prepare_circuit_template_plan", inputs | dict(
        allowed_rule_refs=target["allowed_rule_refs"])))
    assert legacy["plan"] == ready["plan"]
    assert args == before


@pytest.mark.parametrize("fault,code,object_ref", [
    ("absent", "source_instance_missing", "absent"),
    ("overlap", "group_device_overlap", "M0"),
    ("uncovered", "device_partition_incomplete", "M1"),
    ("boundary", "group_boundary_undeclared", "load"),
    ("pin", "source_terminal_missing", "M0"),
    ("duplicate_net", "boundary_net_duplicate", "M0"),
    ("range", "boundary_source_count", "M0"),
    ("kind", "boundary_source_kind", "M0"),
    ("group_id", "group_id_duplicate", "load"),
])
def test_contract_failure_retains_reference_and_offline_repair(tmp_path, fault, code, object_ref):
    intent = core_intent()
    if fault == "absent":
        intent["core_instances"] = ["absent"]
    elif fault == "overlap":
        intent["optional_groups"][0]["instances"].append("M0")
    elif fault == "uncovered":
        intent["optional_groups"] = []
    elif fault == "boundary":
        intent["boundary_terminals"] = []
    elif fault == "pin":
        intent["boundary_terminals"][0]["endpoint"]["terminal"] = "absent"
    elif fault == "duplicate_net":
        intent["boundary_terminals"].append(copy.deepcopy(intent["boundary_terminals"][0]))
    elif fault == "range":
        intent["boundary_terminals"][0]["max_additional_connections"] = 0
    elif fault == "kind":
        intent["boundary_terminals"][0]["allowed_device_kinds"] = ["capacitor"]
    else:
        intent["optional_groups"].append(copy.deepcopy(intent["optional_groups"][0]))
    client, _, result = extract(tmp_path, args=request() | dict(reuse=intent))
    assert not result["ok"] and result["stage"] == "stored_reference", result
    assert result["reuse"]["status"] == "needs_contract"
    issue = next(i for i in result["reuse"]["issues"] if i["code"] == code)
    assert issue["object_ref"] == object_ref and issue["required_fields"]
    assert result["reuse"]["template_ref"] is None
    files = [Path(result["package"]) / "package.json", Path(result["capture"])]
    original = [p.read_bytes() for p in files]
    args = result["continuation"]["arguments"] | dict(reuse=core_intent())
    fixed = unpack(rpc(McpServer(None, workspace=tmp_path), "extract_circuit_templates", args))
    assert fixed["ok"] and fixed["live"] is False
    assert len(client.calls) == 1 and original == [p.read_bytes() for p in files]
