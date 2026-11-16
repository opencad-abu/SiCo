"""AC MCP metadata, Cartesian page integrity, release and input validation."""

import json

import pytest
from ac_fixture import recipe
from test_circuit_rc_gui import mcp_call
from test_waveform_transport import ARGS, Client

from cadai.mcp import McpServer

AC_ARGS = dict(ARGS, analysis="ac")


class AcClient(Client):
    def __init__(self, mutation=None, page_mutation=None):
        super().__init__()
        self.ac_mutation, self.page_mutation = mutation, page_mutation

    def call(self, name, args):
        code = args["code"]
        ok, result = super().call(
            name, dict(args, code=code.replace("aiAcCapture", "aiWaveCapture"))
        )
        detail = json.loads(result["value"])
        if code.startswith("aiAcCapture"):
            detail.update(analysis="ac", x_unit="Hz", native_y_type="doublecomplex")
            if self.ac_mutation:
                self.ac_mutation(detail)
        if code.startswith("aiWavePage"):
            detail["samples"] = [[r[0] * 1000, r[1], -r[1]] for r in detail["samples"]]
            if self.page_mutation:
                self.page_mutation(detail)
        return ok, dict(value=json.dumps(detail))


def test_mcp_ac_capture_paging_and_offline(tmp_path):
    client = AcClient()
    server = McpServer(client, workspace=tmp_path)
    captured = mcp_call(server, "read_maestro_ac_waveform", AC_ARGS)
    assert captured["ok"] and captured["sample_count"] == 257
    assert (
        captured["native_y_type"] == "doublecomplex"
        and captured["representation"] == "cartesian_complex"
    )
    assert client.calls[-1].startswith("aiWaveRelease")
    count = len(client.calls)
    page = mcp_call(
        server, "query_ac_waveform", dict(waveform_ref=captured["waveform_ref"], offset=256)
    )
    assert page["samples"] == [[2000, 2, -2]] and page["next_offset"] is None
    result = mcp_call(
        server,
        "measure_ac_waveform",
        dict(waveform_ref=captured["waveform_ref"], recipes=[recipe("phase")]),
    )
    assert result["rows"][0]["value"] == -45 and len(client.calls) == count


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(history="other"),
        lambda m: m.update(analysis="tran"),
        lambda m: m.update(point=8),
        lambda m: m.update(native_y_type=None),
        lambda m: m.update(sample_count=65537),
        lambda m: m.update(sample_count=258),
        lambda m: m.update(pages=1),
        lambda m: m.update(status="family"),
        lambda m: m.update(capture_id="wrong"),
        lambda m: m.update(x_unit=["Hz"]),
    ],
)
def test_bad_metadata_refused_and_released(tmp_path, mutation):
    client = AcClient(mutation)
    result = mcp_call(McpServer(client, workspace=tmp_path), "read_maestro_ac_waveform", AC_ARGS)
    assert not result["ok"] and client.calls[-1].startswith("aiWaveRelease")
    assert not list((tmp_path / ".sico/ai/measurement-results").glob("ac_waveform_*.json"))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["samples"][0].pop(),
        lambda p: p["samples"][0].__setitem__(0, -1),
        lambda p: p["samples"][0].__setitem__(1, True),
        lambda p: p["samples"].reverse(),
        lambda p: p["samples"].clear(),
    ],
)
def test_bad_cartesian_pages_refused(tmp_path, mutation):
    client = AcClient(page_mutation=mutation)
    result = mcp_call(McpServer(client, workspace=tmp_path), "read_maestro_ac_waveform", AC_ARGS)
    assert not result["ok"] and client.calls[-1].startswith("aiWaveRelease")


@pytest.mark.parametrize(
    "change", [dict(analysis="pac"), dict(point=True), dict(signal=""), dict(extra=1)]
)
def test_arguments_checked_before_dispatch(tmp_path, change):
    client = AcClient()
    response = McpServer(client, workspace=tmp_path).handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "read_maestro_ac_waveform",
            "arguments": dict(AC_ARGS, **change),
        },
    })
    assert response["error"]["data"]["code"] == "invalid_arguments"
    assert response["error"]["data"]["retry_same_request"] is False
    assert not client.calls
