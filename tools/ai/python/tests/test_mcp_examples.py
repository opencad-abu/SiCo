"""Curated help examples must stay valid under the real workflow validators.

The token convention: any string starting with ``REPLACE:`` is session data the
caller must supply.  This module's materializer shows the intended substitution
and then runs the same preview chain the host runs, so a drifted example fails
here instead of failing a live creation task.
"""

from __future__ import annotations

import copy
import json

import pytest

from cadai.mcp import mcp_examples
from cadai.template_standard.pipeline.circuit_geometry import preview_geometry
from cadai.template_standard.pipeline.circuit_layout_intent import resolve_layout_intent
from cadai.shared.circuit_spec_plan import preview_circuit
from cadai.workflows.simulation_run.simulation_recipe import preview_recipe
from cadai.shared.schema_validation import digest, validate
from cadai.mcp.mcp_definitions import TOOLS_BY_NAME
from cadai.mcp.mcp_help import mcp_help
from cadai.platform.io.socket_server import RequestFailure

_TOKENS = {
    "REPLACE: project_ref from get_project_context": "project:example",
    "REPLACE: binding snapshot_ref": "snapshot:example",
    "REPLACE: selection source_ref": "example-selection",
    "REPLACE: geometry source_ref": "example-geometry",
    "REPLACE: cdf_ref from a fresh inspect_cdf of the same instance":
        "cdf:" + "0123456789abcdef" * 2,
    "REPLACE: task_ref from begin_circuit_task": "task:example",
    "REPLACE: request_id passed to execute_circuit_operation": "tb_myinv-cdf-1",
    "REPLACE: absolute path of the spectre model file": "/pdk/models/spectre/models.scs",
    "REPLACE: setup_ref from create_simulation_setup": "simulation:setup",
    "REPLACE: run_ref from run_simulation_setup": "simulation-run:run",
}
_ENVELOPE = {"ok": True, "action": "example", "complete": True, "truncated": False}
_INLINE_BYTES = 8192


def _substitute(value):
    if isinstance(value, str):
        if value.startswith("REPLACE: absolute path of the "):
            return "/example/" + value.split(" the ")[1].rsplit(" library", 1)[0]
        return _TOKENS.get(value, value)
    if isinstance(value, list):
        return [_substitute(item) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item) for key, item in value.items()}
    return value


def _materialize(view):
    """Resolve the documented tokens the way a live session must."""
    arguments = _substitute(copy.deepcopy(view["arguments"]))
    spec, bindings = arguments["spec"], arguments["bindings"]
    for master in bindings["masters"]:
        master["revision"] = "rev-" + master["id"]
    if "geometry" not in arguments:
        return arguments
    geometry = arguments["geometry"]
    masters = {row["id"]: row["master"] for row in spec["instances"]}
    parameters = {row["id"]: row["parameters"] for row in spec["instances"]}
    for instance in geometry["instances"]:
        instance["master_revision"] = "rev-" + masters[instance["instance"]]
        instance["parameters_digest"] = digest(parameters[instance["instance"]])
    preview = preview_circuit(spec, bindings)
    arguments["preview_digest"] = preview["preview_digest"]
    geometry["binding_digest"] = preview["plan"]["binding_digest"]
    return arguments


def test_examples_reference_advertised_tools_and_known_parts():
    for name, row in mcp_examples.EXAMPLES.items():
        assert name in TOOLS_BY_NAME, name
        assert row["summary"]
        for part in row.get("parts", []):
            assert part in mcp_examples.PARTS, (name, part)


def test_served_examples_stay_inside_the_inline_result_budget():
    for name in mcp_examples.EXAMPLES:
        view = mcp_examples.tool_view(name)
        assert view is not None
        payload = {**_ENVELOPE, **view}
        size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                              sort_keys=True).encode("utf-8"))
        # The host replaces results above 8192 B with an artifact pointer, which
        # would force the shell round-trip this help exists to remove.
        assert size <= _INLINE_BYTES, (name, size)


def test_session_values_are_marked_with_the_documented_token():
    chain = mcp_examples.tool_view("preview_circuit_geometry")
    assert "REPLACE:" in json.dumps(chain["arguments"])
    assert any(path.startswith("geometry.") for path in chain["replace"])
    assert all(path != "" for path in chain["replace"])


def test_chain_notes_state_the_parameters_digest_rule():
    """digest 规则必须随链样例可见：空参数常量与实现一致，模型无需反推序列化。"""
    for name in ("preview_circuit_spec", "preview_circuit_geometry"):
        notes = "\n".join(mcp_examples.tool_view(name)["notes"])
        assert "parameters_digest 规则" in notes
        assert 'sha256(json.dumps(parameters,sort_keys=True,separators=(",",":")' in notes
        assert 'ensure_ascii=False' in notes and 'encode("utf-8")' in notes
        assert digest({}) in notes


def test_chain_notes_state_the_payload_shape_and_resubmission_rules():
    """载荷一致性规则必须随样例可见：spec 级字段形状与"原样重发"要求。"""
    spec_notes = "\n".join(mcp_examples.tool_view("preview_circuit_spec")["notes"])
    assert "connections 只在 spec.instances[] 实例级出现" in spec_notes
    prepare_notes = "\n".join(
        mcp_examples.tool_view("prepare_circuit_creation")["notes"])
    assert "逐字节一致" in prepare_notes and "数组顺序" in prepare_notes
    assert "preview_digest differs" in prepare_notes


def test_cdf_examples_validate_against_their_declared_schemas():
    """CDF 编辑链样例按真实 schema 校验；callback_order 与 parameters 同名同集合。"""
    for name in ("inspect_cdf", "prepare_cdf_update", "get_circuit_operation"):
        view = mcp_examples.tool_view(name)
        arguments = _substitute(copy.deepcopy(view["arguments"]))
        schema = TOOLS_BY_NAME[name]["inputSchema"]
        validate(arguments, schema, "arguments")
    update = _substitute(copy.deepcopy(
        mcp_examples.tool_view("prepare_cdf_update")["arguments"]))
    assert set(update["callback_order"]) == set(update["parameters"])
    assert len(update["callback_order"]) == len(set(update["callback_order"]))


def test_example_action_lists_the_cdf_edit_group():
    listed = mcp_help({"action": "example", "group": "cdf_edit"})
    tools = [row["tool"] for row in listed["examples"]]
    assert "inspect_cdf" in tools and "prepare_cdf_update" in tools


def test_simulation_examples_validate_and_preview():
    """仿真链样例：recipe 过真实 preview_recipe，其余按真实 schema 校验。"""
    recipe_arguments = _substitute(copy.deepcopy(
        mcp_examples.tool_view("preview_simulation_recipe")["arguments"]))
    preview = preview_recipe(recipe_arguments["recipe"])
    assert preview["ok"] is True
    digest = preview["recipe_digest"]
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    for name in ("simulation_job", "inspect_simulation_setup",
                 "get_simulation_recipe_status", "stop_simulation_recipe",
                 "release_simulation_setup"):
        view = mcp_examples.tool_view(name)
        arguments = _substitute(copy.deepcopy(view["arguments"]))
        validate(arguments, TOOLS_BY_NAME[name]["inputSchema"], "arguments")


def test_simulation_notes_state_the_model_inheritance_and_identifier_rules():
    notes = "\n".join(mcp_examples.tool_view("preview_simulation_recipe")["notes"])
    assert "models=[]" in notes and "沿用 ADE" in notes
    assert "request_id" in notes and "冒号" in notes
    assert "ns" in notes
    assert "VT(" in notes and "undefined function V" in notes


def test_example_action_lists_the_simulation_run_group():
    listed = mcp_help({"action": "example", "group": "simulation_run"})
    tools = [row["tool"] for row in listed["examples"]]
    assert "preview_simulation_recipe" in tools
    assert "inspect_simulation_setup" in tools
    assert "simulation_job" not in tools


def test_creation_chain_example_passes_the_real_preview_validators():
    chain = _materialize(mcp_examples.tool_view("preview_circuit_geometry"))
    preview = preview_circuit(chain["spec"], chain["bindings"])
    layout = resolve_layout_intent(
        preview["plan"], chain["geometry"], chain["layout_intent"]
    )
    geometry = preview_geometry(
        spec=chain["spec"], bindings=chain["bindings"],
        preview_digest=chain["preview_digest"],
        geometry=chain["geometry"], layout=layout)
    assert geometry["plan"]["geometry_checks_passed"], geometry


def test_spec_example_validates_without_geometry():
    arguments = _materialize(mcp_examples.tool_view("preview_circuit_spec"))
    preview = preview_circuit(arguments["spec"], arguments["bindings"])
    assert preview["preview_digest"]


def test_example_action_serves_tool_and_group_views():
    served = mcp_help({"action": "example", "tool": "preview_circuit_geometry"})
    assert served["ok"] is True and served["tool"] == "preview_circuit_geometry"
    assert served["arguments"]["spec"]["kind"] == "testbench"
    assert "REPLACE:" in json.dumps(served["arguments"])
    listed = mcp_help({"action": "example", "group": "circuit_create"})
    tools = [row["tool"] for row in listed["examples"]]
    assert "preview_circuit_geometry" in tools and "prepare_circuit_creation" in tools
    assert listed["note"]


def test_example_action_explains_misses_without_hiding_capability():
    with pytest.raises(RequestFailure) as unknown:
        mcp_help({"action": "example", "tool": "ghost"})
    assert unknown.value.code == "unknown_tool"
    miss = mcp_help({"action": "example", "tool": "inspect_layout"})
    assert miss["example"] is None and miss["hint"]
    with pytest.raises(RequestFailure) as both:
        mcp_help({"action": "example", "group": "circuit_create",
                  "tool": "preview_circuit_spec"})
    assert both.value.code == "invalid_params"


def test_describe_and_workflow_point_at_curated_examples():
    described = mcp_help({"action": "describe", "tool": "preview_circuit_spec"})
    assert described["tool"]["example"] == {"action": "example",
                                            "tool": "preview_circuit_spec"}
    without = mcp_help({"action": "describe", "tool": "inspect_layout"})
    assert "example" not in without["tool"]
    flow = mcp_help({"action": "workflow", "group": "circuit_create"})
    assert flow["examples"]["action"] == "example"
    assert "preview_circuit_geometry" in flow["examples"]["tools"]
