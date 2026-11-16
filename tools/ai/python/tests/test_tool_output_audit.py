"""Public tool diagnostics survive decoding, chunking, artifacts and aggregation."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from pdk_fixture import Bridge
from test_cdf_tools import Client as CdfClient
from test_cdf_tools import reply as cdf_reply
from test_pdk_bridge import Client as PdkClient

from cadai.cdf_tools import call_cdf
from cadai.mcp import McpServer
from cadai.mcp_snapshot_handler import dispatch_snapshot
from cadai.pdk_binding_geometry import validate_body
from cadai.pdk_bridge import PdkBridge
from cadai.pdk_session import PdkSession
from cadai.skill_diagnostics import SkillDiagnostics, record_output
from cadai.workflow_tools import decode_workflow_result


def output(text, truncated=False):
    return dict(output=text, output_truncated=truncated, output_capture="skill_ports")


def call(server, name, arguments):
    response = server.handle(dict(jsonrpc="2.0", id=1, method="tools/call",
                                  params=dict(name=name, arguments=arguments)))
    assert "error" not in response, response
    return response["result"]["isError"], json.loads(response["result"]["content"][0]["text"])


@pytest.mark.parametrize("value", [dict(value='{"ok":true,"job_id":"job"}'),
    dict(value='{"ok":false,"code":"flow_failed"}'), dict(value="not JSON"),
    dict(value="[]"), dict(value_truncated=True, value="{}"), dict(spooled=True)])
def test_workflow_output_survives_success_and_decode_errors(value):
    ok, result = decode_workflow_result({**value, **output("*WARNING* FLOW\n")})
    assert ok is (value.get("value") == '{"ok":true,"job_id":"job"}')
    assert result["output"] == "*WARNING* FLOW\n"
    assert not result["output_truncated"] and result["output_capture"] == "skill_ports"


def test_cdf_inspection_capture_survives_chunks_and_artifact(tmp_path):
    class Client(CdfClient):
        def call(self, method, arguments):
            ok, result = super().call(method, arguments)
            if arguments["code"].startswith("aiCdfPut"):
                assert method == "eval_skill"
                result.update(output("CDF_READ\n"))
            else:
                assert method == "eval_skill_native"
            return ok, result

    ok, result = call_cdf("inspect_cdf", dict(cdf_ref="cdf:" + "a" * 32),
                          Client(cdf_reply()), tmp_path)
    assert ok and result["output"] == "CDF_READ\n"
    from pathlib import Path

    assert json.loads(Path(result["artifact"]["path"]).read_text())["output"] == "CDF_READ\n"


def test_pdk_public_call_aggregates_chunk_diagnostics_without_changing_snapshot(tmp_path):
    fixture = Bridge().capture("directory", {"library": "synthetic"})

    class Client(PdkClient):
        def __init__(self):
            super().__init__(json.dumps(fixture))
            self.enabled = True

        def call(self, method, args):
            ok, result = super().call(method, args)
            if self.enabled:
                stage = args["code"].split("(", 1)[0]
                result.update(output(stage + "\n"))
            return ok, result

    client = Client()
    server = McpServer(client, workspace=tmp_path)
    server._pdk_session = PdkSession(bridge=PdkBridge(client))
    failed, first = call(server, "search_pdk_devices", {"library": "synthetic"})
    assert not failed
    assert first["output"] == "aiPdkCapture\naiPdkRead\naiPdkDrop\n"
    client.enabled = False
    _, quiet = call(server, "search_pdk_devices", {"library": "synthetic"})
    assert "output" not in quiet
    assert first["context"]["directory_digest"] == quiet["context"]["directory_digest"]
    assert first["items"] == quiet["items"]


def test_pdk_failure_keeps_preceding_output_and_original_error(tmp_path):
    class Client(PdkClient):
        def call(self, method, args):
            if args["code"].startswith("aiPdkCapture"):
                return True, dict(value='{"ok":false,"code":"capture_unavailable",'
                                        '"message":"device unavailable"}',
                                  **output("*WARNING* DEVICE\n*Error* READ_FAILED\n"))
            raise AssertionError("Failed capture must not be retried or read")

    server = McpServer(Client("{}"), workspace=tmp_path)
    server._pdk_session = PdkSession(bridge=PdkBridge(server.client))
    failed, data = call(server, "search_pdk_devices", {"library": "synthetic"})
    assert failed and data["code"] == "capture_unavailable"
    assert data["output"] == "*WARNING* DEVICE\n*Error* READ_FAILED\n"
    assert data["cause"] == "device unavailable"
    assert data["message"].startswith("device unavailable")


@pytest.mark.parametrize("failed", [False, True])
def test_snapshot_adapter_preserves_output_and_error_classification(failed):
    detail = (
        dict(code="skill_error", message="snapshot failed", **output("SNAPSHOT\n"))
        if failed
        else dict(
            ok=True, artifact={"path": "opaque-test-artifact"}, **output("SNAPSHOT\n")
        )
    )
    client = SimpleNamespace(call=lambda *_: (not failed, detail))
    store = SimpleNamespace(register=lambda _: {"snapshot_id": "snapshot:test"})
    ok, result = dispatch_snapshot("snapshot_schematic", {}, client=client, get_store=lambda: store)
    assert ok is not failed and result["output"] == "SNAPSHOT\n"
    if failed:
        assert result["code"] == "skill_error"


def test_geometry_diagnostics_do_not_change_binding_identity():
    body = dict(
        ok=True,
        occupied_bbox=[[0, 0], [1, 1]],
        pin_centers=[["A", [[0, 0]]]],
        signature="shape",
    )
    pins = [dict(name="A", anchors=[dict(xy=[0, 0])])]
    assert validate_body(body, pins) == validate_body({**body, **output("warning")}, pins)


@pytest.mark.parametrize("chunk", ["中文🙂", "\n\t\x01\\\""])
def test_many_calls_share_one_utf8_and_json_budget(chunk):
    with SkillDiagnostics() as diagnostics:
        for _ in range(8):
            record_output(output(chunk * 16384))
        result = diagnostics.attach({"ok": False, "code": "unchanged"})
    assert result["ok"] is False and result["code"] == "unchanged"
    assert result["output_truncated"] and result["output"]
    assert (chunk * 131072).startswith(result["output"])
    assert len(result["output"].encode()) <= 65536
    assert len(json.dumps(result["output"], ensure_ascii=False).encode()) <= 80000


def test_nested_and_concurrent_tool_output_is_isolated():
    def worker(marker):
        with SkillDiagnostics() as outer:
            record_output(output(marker))
            with SkillDiagnostics() as inner:
                record_output(output("inner"))
                assert inner.attach({})["output"] == "inner"
            record_output(output(marker))
            return outer.attach({})["output"]

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(worker, ["A", "B"])) == ["AA", "BB"]
    with SkillDiagnostics() as following:
        assert following.attach({}) == {}


def test_oversized_business_reply_keeps_bounded_diagnostics():
    from cadai.mcp_protocol import tool_result
    from cadai.protocol import encode_line

    response = tool_result(1, True, dict(payload="x" * 2_000_000, **output("中文" * 100000)))
    result = json.loads(response["result"]["content"][0]["text"])
    assert result["code"] == "response_too_large" and result["output_truncated"]
    assert result["output"] and len(result["output"].encode()) <= 65536
    encode_line(response)


def test_rejected_controller_artifact_retains_warning(tmp_path):
    from cadai.controller_artifact import materialize_snapshot_artifact
    from cadai.socket_server import RequestFailure

    with pytest.raises(RequestFailure) as caught:
        materialize_snapshot_artifact(dict(value="{}", **output("*WARNING* BEFORE_BAD_ARTIFACT\n")),
            tmp_path / "snapshot-data-test.jsonl", tmp_path, tmp_path,
            artifact_prefix="snapshot-data-", max_bytes=1024)
    assert caught.value.code == "invalid_result"
    assert caught.value.data["output"] == "*WARNING* BEFORE_BAD_ARTIFACT\n"


def test_sico_artifact_decode_failure_retains_warning(tmp_path):
    from sico.adapters.assistant_tools import _AssistantClient

    broker = SimpleNamespace(assistant_call=lambda *_: dict(ok=True, value="not JSON",
                             **output("*WARNING* BEFORE_BAD_JSON\n")))
    client = _AssistantClient(broker, None, tmp_path, tmp_path / "spool")
    ok, result = client.call("snapshot_schematic", {})
    assert not ok and result["code"] == "snapshot_error"
    assert result["output"] == "*WARNING* BEFORE_BAD_JSON\n"
