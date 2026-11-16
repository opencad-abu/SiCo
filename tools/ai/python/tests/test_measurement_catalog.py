"""Catalog/executor agreement, bounded queries and offline MCP discovery."""

import json
from copy import deepcopy

import pytest
from measurement_catalog_fixture import task
from test_circuit_rc_gui import mcp_call

from cadai.circuit_spec_schema import CircuitSpecError, validate
from cadai.mcp import TOOLS, McpServer
from cadai.measurement_catalog import CATALOG_NAMES, call_catalog
from cadai.result_contract import ResultError
from cadai.waveform_spec_kinds import RECIPE_SCHEMAS, check_recipe


class Offline:
    def call(self, *unused):
        pytest.fail("catalog or offline measurement contacted Virtuoso")


def failed_call(server, name, arguments):
    response = server.handle({
        "jsonrpc": "2.0", "id": "invalid", "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    if "error" in response:
        return {
            **response["error"].get("data", {}),
            "ok": False,
            "message": response["error"]["message"],
        }
    detail = json.loads(response["result"]["content"][0]["text"])
    assert response["result"]["isError"] is True
    assert detail["ok"] is False
    return detail


def listing(**args):
    return call_catalog("list_measurement_capabilities", args)


def detail(capability_id, **args):
    return call_catalog("get_measurement_capability", dict(capability_id=capability_id, **args))


IDS = [
    kind + "." + op
    for kind, schema in RECIPE_SCHEMAS.items()
    for op in schema["properties"]["operation"]["enum"]
]


def test_offline_registry_search_paging_and_restart():
    server = McpServer(Offline())  # No workspace required; no artifact writes.
    tools = server.handle(dict(jsonrpc="2.0", id=1, method="tools/list"))["result"]["tools"]
    assert CATALOG_NAMES <= {t["name"] for t in tools}
    assert len({t["name"] for t in TOOLS}) == len(TOOLS)
    page, all_ids = listing(limit=4), []
    revision = page["catalog_revision"]
    while True:
        assert page["total"] == 17 and page["catalog_revision"] == revision
        all_ids += [e["capability_id"] for e in page["items"]]
        if page["next_offset"] is None:
            break
        page = listing(limit=4, offset=page["next_offset"])
    assert all_ids == sorted(IDS)
    assert listing(offset=100)["items"] == []
    assert listing(kind="ac_signal", operation="phase")["total"] == 1
    assert listing(tool_name="measure_ac_response")["total"] == 2
    assert listing(query="PHASE")["total"] == 3
    assert listing(query="[.*]")["items"] == []  # Literal, not regex.
    assert listing(kind="single", operation="phase")["items"] == []
    assert listing(operation="unknown")["items"] == []
    assert mcp_call(server, "list_measurement_capabilities", {}) == listing()
    assert mcp_call(
        McpServer(Offline()),
        "get_measurement_capability",
        dict(capability_id="ac_transfer.phase", catalog_revision=revision),
    ) == detail("ac_transfer.phase")
    # Initialization collects entry context when a live client exists. An
    # offline catalog session has no client, while catalog calls above must
    # remain offline even when one is present.
    init = McpServer(None).handle(dict(jsonrpc="2.0", id=2, method="initialize", params={}))
    assert "list_measurement_capabilities" in init["result"]["instructions"]
    assert "get_measurement_capability" in init["result"]["instructions"]


@pytest.mark.parametrize("capability_id", IDS)
def test_discover_validate_execute_each_operation(tmp_path, capability_id):
    server = McpServer(Offline(), workspace=tmp_path)
    item = mcp_call(server, "get_measurement_capability", dict(capability_id=capability_id))[
        "capability"
    ]
    args = task(tmp_path, item["kind"], item["operation"])
    validate(args, item["tool_input_schema"])
    validate(args["recipes"][0], item["recipe_schema"])
    check_recipe(args["recipes"][0], item["kind"], args.get("denominator_floor"))
    report = mcp_call(server, item["tool_name"], args)
    assert report["ok"] and report["all_measured"], report
    assert report["rows"][0]["status"] == "scalar"
    assert report["spec_qualified"] is None
    assert item["tool_input_schema"] == next(
        t["inputSchema"] for t in TOOLS if t["name"] == item["tool_name"]
    )
    assert set(item["required_recipe_fields"]) == set(args["recipes"][0])
    # Each declared field is really mandatory; an irrelevant field is never silently accepted.
    for field in item["required_recipe_fields"]:
        bad = deepcopy(args)
        del bad["recipes"][0][field]
        with pytest.raises(CircuitSpecError):
            validate(bad["recipes"][0], item["recipe_schema"])
        assert not failed_call(server, item["tool_name"], bad)["ok"], field
    bad = deepcopy(args)
    bad["recipes"][0]["project_default"] = 1
    with pytest.raises(CircuitSpecError):
        validate(bad["recipes"][0], item["recipe_schema"])
    assert not failed_call(server, item["tool_name"], bad)["ok"]


@pytest.mark.parametrize(
    "name,args",
    [
        ("list_measurement_capabilities", {"limit": 0}),
        ("list_measurement_capabilities", {"limit": True}),
        ("list_measurement_capabilities", {"offset": -1}),
        ("list_measurement_capabilities", {"kind": "arbitrary"}),
        ("list_measurement_capabilities", {"query": "a" * 129}),
        ("list_measurement_capabilities", {"query": "\n"}),
        ("list_measurement_capabilities", {"unknown": 1}),
        ("get_measurement_capability", {}),
        ("get_measurement_capability", {"capability_id": "PHASE"}),
        (
            "get_measurement_capability",
            {"capability_id": "ac_transfer.phase", "catalog_revision": "0" * 64},
        ),
        (
            "get_measurement_capability",
            {"capability_id": "ac_transfer.phase", "catalog_revision": "bad"},
        ),
    ],
)
def test_invalid_queries_and_stale_selection_refused(name, args):
    with pytest.raises(ResultError):
        call_catalog(name, args)
    assert not failed_call(McpServer(Offline()), name, args)["ok"]


def test_revision_changes_with_contract_and_returns_are_isolated(monkeypatch):
    import cadai.measurement_catalog as module

    original = detail("single.sample")
    original["capability"]["recipe_schema"]["properties"]["at"].clear()
    assert detail("single.sample")["capability"]["recipe_schema"]["properties"]["at"]
    revision = listing()["catalog_revision"]
    tools = deepcopy(module.MEASUREMENT_TOOLS)
    tools["measure_waveform"]["inputSchema"]["properties"]["recipes"]["maxItems"] = 7
    monkeypatch.setattr(module, "MEASUREMENT_TOOLS", tools)
    updated = detail("single.sample")
    assert updated["catalog_revision"] != revision
    assert updated["capability"]["limits"]["recipes_per_call"] == 7
    with pytest.raises(ResultError, match="revision changed"):
        detail("single.sample", catalog_revision=revision)


def test_units_boundaries_and_no_project_defaults():
    result = detail("ac_response.phase_margin")
    c = result["capability"]
    assert c["analysis"]["required_name"] == "ac"
    assert c["output_unit"] == "deg_or_rad"
    assert c["specification"]["top_level_denominator_floor_required"]
    assert c["specification"]["stability_qualified"] is None
    assert c["limits"]["recipes_per_call"] == 16
    assert c["recipe_schema"]["properties"]["phase_anchor_turns"]["minimum"] == -16
    assert result["unit_registry"]["mV"]["scale_to_base"] == 0.001
    assert result["unit_registry"]["MV"]["scale_to_base"] == 1e6
    assert {"phase_branch", "strict_crossing", "phase_floor"} <= {r["code"] for r in c["rules"]}
    assert detail("single.crossing")["capability"]["analysis"]["required_name"] is None
    assert detail("pair.delay")["capability"]["input"]["axis"] == "time"
    assert "bandpass_width" in detail("ac_response.bandwidth")["capability"]["unsupported"]
    for name in IDS:
        cap = detail(name)["capability"]

        def walk(value):
            if isinstance(value, dict):
                assert "default" not in value and "examples" not in value
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        walk(cap["recipe_schema"])
        walk(cap["tool_input_schema"])


def test_discovery_is_bounded_and_does_not_write_workspace(tmp_path):
    server = McpServer(Offline(), workspace=tmp_path)
    summary = mcp_call(server, "list_measurement_capabilities", {})
    assert len(json.dumps(summary).encode()) < 16000
    for row in summary["items"]:
        response = mcp_call(
            server,
            "get_measurement_capability",
            dict(capability_id=row["capability_id"], catalog_revision=summary["catalog_revision"]),
        )
        assert len(json.dumps(response, indent=2).encode()) < 32000
    assert list(tmp_path.iterdir()) == []
