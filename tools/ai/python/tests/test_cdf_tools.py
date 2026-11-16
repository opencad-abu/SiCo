"""CDF contracts, native transport, tagged values and artifact integrity."""

import copy
import hashlib
import json

import pytest

from cadai.shared.cdf_results import condition, normalize, properties, raw
from cadai.workflows.cdf_edit.cdf_tools import (
    CDF_TOOLS,
    build_call,
    call_cdf,
    inspection_next_action,
    probe_next_action,
)
from cadai.workflows.cdf_edit.cdf_transport import transfer
from cadai.shared.schema_errors import CircuitSpecError
from cadai.mcp import McpServer
from cadai.workflows.knowledge.skill_check import check_source


def args():
    return dict(
        cdf_ref="cdf:" + "a" * 32,
        task_ref="task:test",
        request_id="probe",
        target=dict(library="SicoTest", cell="probe", view="schematic"),
        parameters=dict(w="1u", fingers=2, enabled=False, scale=1.5),
        extension_ref="project:qualified",
    )


def test_fixed_expression_and_explicit_effect_annotations():
    data = args()
    before = copy.deepcopy(data)
    code = build_call("probe_cdf_parameters", data)
    assert code.startswith("aiCdfProbe(") and check_source(code.encode())["ok"]
    assert 'list("enabled" "boolean" nil)' in code
    assert 'list("fingers" "integer" 2)' in code
    assert 'list("scale" "number" 1.5)' in code
    assert data == before
    tools = {t["name"]: t for t in CDF_TOOLS}
    assert not tools["probe_cdf_parameters"]["annotations"]["readOnlyHint"]
    assert tools["inspect_cdf"]["annotations"]["readOnlyHint"]
    assert tools["get_cdf_probe"]["annotations"]["readOnlyHint"]


@pytest.mark.parametrize(
    "update",
    [
        dict(target=dict(library="userDesign", cell="probe", view="schematic")),
        dict(target=dict(library="work", cell="x", view="symbol")),
        dict(callback="evil()"),
        dict(order=["w", "fingers"]),
        dict(task_ref="missing"),
        dict(cdf_ref="cdf:wrong"),
        dict(parameters={"w": float("nan")}),
    ],
)
def test_probe_invalid_before_native(update):
    with pytest.raises(CircuitSpecError):
        build_call("probe_cdf_parameters", dict(args(), **update))


@pytest.mark.parametrize(
    "data",
    [
        {},
        dict(cdf_ref="cdf:" + "a" * 32, target=dict(library="l", cell="c", view="v")),
        dict(instance="M0", cdf_ref="cdf:" + "a" * 32),
        dict(cdf_ref="cdf:" + "a" * 32, parameters=["w", "w"]),
        dict(cdf_ref="cdf:" + "a" * 32, limit=65),
    ],
)
def test_inspection_target_and_paging_boundaries(data):
    with pytest.raises(CircuitSpecError):
        build_call("inspect_cdf", data)


def test_inspect_cdf_clamps_an_oversized_page_to_the_bounded_page(tmp_path, monkeypatch):
    calls = []

    def transfer(_client, code, capture=False):
        calls.append(code)
        return True, reply()

    monkeypatch.setattr("cadai.workflows.cdf_edit.cdf_tools.transfer", transfer)
    ok, _ = call_cdf(
        "inspect_cdf",
        dict(cdf_ref="cdf:" + "a" * 32, limit=200),
        None,
        tmp_path,
    )
    assert ok is True
    assert " 0 64 " in calls[0]
    assert " 0 200 " not in calls[0]


def test_quoted_parameter_value_is_never_executable_source():
    data = args()
    data["parameters"] = {"w": '") exit() ("'}
    code = build_call("probe_cdf_parameters", data)
    assert '\\"' in code and check_source(code.encode())["ok"]


def test_cdf_conditions_and_typed_values_are_not_evaluated():
    assert raw(["nil", None])["type"] == "nil"
    assert raw(["symbol", "nil"])["type"] == "symbol"
    assert raw(["string", ""])["value"] == ""
    assert raw(["integer", 0])["value"] == 0
    assert raw(["list", None])["value"] == []
    assert raw(None)["status"] == "not_applicable"
    assert raw(["unsupported", "opaque"])["status"] == "unsupported"
    assert condition(["string", "cdfgData->mode"])["status"] == "context_required"
    assert condition(["string", "nil"])["value"] is False
    assert condition(["symbol", "t"])["value"] is True
    assert condition(["nil", None])["status"] == "absent"
    plist = raw(
        [
            "list",
            [["nil", None], ["symbol", "termOrder"], ["list", [["string", "D"], ["string", "G"]]]],
        ]
    )
    assert properties(plist)["termOrder"]["value"][0]["value"] == "D"
    assert properties(raw(["list", [["symbol", "eval"], ["string", "danger()"]]])) is None


def test_probe_failures_name_the_corrective_read_or_type():
    assert "master symbol" in probe_next_action({
        "ok": False,
        "error": "*Error* probe requires an effective-cell symbol CDF capture",
    })
    assert "live CDF type" in probe_next_action({
        "ok": False,
        "error": 'CDF parameter fingers: live type "string" does not accept requested integer',
    })
    assert probe_next_action({"ok": True}) is None
    assert "direct instance name" in inspection_next_action({
        "ok": False,
        "error": "*Error* instance not found; direct instance name required",
    })


def reply():
    return dict(
        ok=True,
        cdf_ref="cdf:" + "a" * 32,
        target=["analogLib", "res", "symbol"],
        instance=None,
        parameters=[],
        hooks=[["nil", None], ["nil", None]],
        sim_info=["list", None],
        missing_parameters=None,
    )


class Client:
    def __init__(self, result, *, corrupt=False):
        self.content = json.dumps(result, ensure_ascii=False).encode()
        self.calls = []
        self.corrupt = corrupt

    def call(self, method, arguments):
        code = arguments["code"]
        self.calls.append(code)
        if code.startswith("aiCdfPut("):
            value = dict(ok=True, bytes=len(self.content))
        elif code.startswith("aiCdfRead("):
            offset = int(code.rsplit(" ", 1)[1][:-1])
            value = dict(
                ok=True, hex="XX" if self.corrupt else self.content[offset : offset + 6000].hex()
            )
        else:
            assert code.startswith("aiCdfDrop(")
            value = dict(ok=True)
        return True, dict(ok=True, value=json.dumps(value))


def test_multibyte_large_transport_exact_and_drop():
    result = dict(ok=True, description="宽度/电压" * 5000)
    client = Client(result)
    assert transfer(client, "aiExample()") == (True, result)
    assert len(client.calls) > 5 and client.calls[-1].startswith("aiCdfDrop(")
    assert sum(c.startswith("aiCdfPut(") for c in client.calls) == 1


def test_corrupt_transfer_is_dropped_without_reexecution():
    client = Client(reply(), corrupt=True)
    with pytest.raises(CircuitSpecError):
        transfer(client, "aiExample()")
    assert len(client.calls) == 3 and client.calls[-1].startswith("aiCdfDrop(")


def test_artifact_digest_and_mcp_route(tmp_path):
    client = Client(reply())
    ok, result = call_cdf(
        "inspect_cdf",
        dict(target=dict(library="analogLib", cell="res", view="symbol")),
        client,
        tmp_path,
    )
    assert ok
    from pathlib import Path

    content = Path(result["artifact"]["path"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == result["artifact"]["sha256"]
    assert json.loads(content)["expressions_evaluated"] is False
    server = McpServer(client, workspace=tmp_path)
    response = server.handle(
        dict(
            jsonrpc="2.0",
            id=1,
            method="tools/call",
            params=dict(name="inspect_cdf", arguments=dict(cdf_ref="cdf:" + "a" * 32)),
        )
    )
    assert not response["result"].get("isError")


def simulator_plist(count):
    """aiCdfInspect 形状的 simInfo 片段：count 个保留字段。"""
    rows = [["nil", None]]
    for index in range(count):
        rows.append(["symbol", "attr%03d" % index])
        rows.append(["string", "值" * 20])
    return ["list", rows]


def test_large_simulator_interfaces_stay_out_of_the_inline_page(tmp_path):
    """同一 plist 的多份拷贝不再内联：artifact 保留全量，内联页不超预览预算。"""
    from pathlib import Path

    data = reply()
    data["sim_info"] = ["list", [
        ["nil", None],
        ["symbol", "auLvs"],
        ["list", [["nil", None], ["symbol", "componentName"], ["string", "res"]]],
        ["symbol", "spectre"], simulator_plist(80),
        ["symbol", "ams"], simulator_plist(60),
    ]]
    ok, result = call_cdf(
        "inspect_cdf",
        dict(target=dict(library="analogLib", cell="res", view="symbol")),
        Client(data),
        tmp_path,
    )
    assert ok
    assert len(json.dumps(result, ensure_ascii=False)) <= 7_600
    items = {item["name"]: item for item in result["simulators"]["items"]}
    assert items["auLvs"]["attributes"]["componentName"]["value"] == "res"
    assert items["spectre"]["attributes"] == "artifact_only"
    assert items["spectre"]["attribute_count"] == 80
    assert items["spectre"]["attribute_names"][:2] == ["attr000", "attr001"]
    assert result["sim_info"]["status"] == "artifact_only"
    assert "parameters_detail" not in result
    content = json.loads(Path(result["artifact"]["path"]).read_bytes())
    assert content["sim_info"]["type"] == "list"
    assert content["simulators"]["items"][1]["attributes"]["attr000"]["value"] == "值" * 20
    assert content["simulators"]["items"][1]["raw"]["type"] == "list"


def parameter_row(name, current):
    def tagged(value, kind="string"):
        return [kind, value]

    return [
        name, "string", name.upper(), tagged(""), tagged(current), tagged(None, "nil"),
        tagged("time"), tagged(None, "nil"), tagged(None, "nil"), tagged(None, "nil"),
        tagged("t", "symbol"), tagged("t", "symbol"), [True, "string", tagged(current)],
    ]


def test_parameter_rows_project_edit_context_out_of_the_inline_page(tmp_path):
    from pathlib import Path

    data = reply()
    data["instance"] = "VIN_SRC"
    data["parameters"] = [parameter_row("per", "10n"), parameter_row("pw", "5n")]
    ok, result = call_cdf(
        "inspect_cdf",
        dict(
            target=dict(library="analogLib", cell="vpulse", view="symbol"),
            instance="VIN_SRC",
            parameters=["per", "pw"],
        ),
        Client(data),
        tmp_path,
    )
    assert ok
    rows = {row["name"]: row for row in result["parameters"]}
    assert rows["per"]["current"]["value"] == "10n"
    assert rows["per"]["display"] == {"status": "artifact_only", "reason": "edit_context"}
    assert rows["per"]["editable"] == {"status": "artifact_only", "reason": "edit_context"}
    assert rows["per"]["instance_property"] == {"status": "equals_current"}
    assert "parameters_detail" not in result
    content = json.loads(Path(result["artifact"]["path"]).read_bytes())
    assert content["parameters"][0]["display"]["status"] == "absent"
    assert content["parameters"][0]["instance_property"]["status"] == "present"


def test_oversized_parameter_page_falls_back_to_name_and_current(tmp_path):
    from pathlib import Path

    data = reply()
    data["instance"] = "VIN_SRC"
    data["parameters"] = [parameter_row("p%03d" % index, "值" * 40) for index in range(40)]
    ok, result = call_cdf(
        "inspect_cdf",
        dict(target=dict(library="analogLib", cell="vpulse", view="symbol"), instance="VIN_SRC"),
        Client(data),
        tmp_path,
    )
    assert ok and result["parameters_detail"] == "artifact_only"
    assert len(json.dumps(result, ensure_ascii=False)) <= 7_600
    assert set(result["parameters"][0]) == {"name", "current"}
    content = json.loads(Path(result["artifact"]["path"]).read_bytes())
    assert content["parameters"][0]["units"]["value"] == "time"


def test_huge_parameter_page_reports_only_the_count(tmp_path):
    data = reply()
    data["instance"] = "VIN_SRC"
    data["parameters"] = [parameter_row("p%03d" % index, "值" * 40) for index in range(400)]
    ok, result = call_cdf(
        "inspect_cdf",
        dict(target=dict(library="analogLib", cell="vpulse", view="symbol"), instance="VIN_SRC"),
        Client(data),
        tmp_path,
    )
    assert ok and result["parameters_detail"] == "artifact_only"
    assert result["parameters"] == {
        "status": "artifact_only",
        "reason": "inline_response_limit",
        "count": 400,
    }
    assert len(json.dumps(result, ensure_ascii=False)) <= 7_600


def test_step_trace_reports_changes_and_no_fabricated_matching():
    r = dict(
        ok=False,
        parameters=[],
        requested=[["w", "string", "1u"]],
        requested_matches=None,
        trace=[
            ["cell_before", [["w", ["string", "1u"]]]],
            ["project:set:fingers", [["w", ["string", "2u"]]]],
        ],
    )
    result = normalize(r, "get_cdf_probe")
    assert result["trace"][1]["changes"][0]["before"]["value"] == "1u"
    assert result["trace"][1]["changes"][0]["after"]["value"] == "2u"
    assert result["all_requested_match"] is None
    assert result["requested_matches"][0]["matched"] is None
