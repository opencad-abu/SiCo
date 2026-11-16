"""Capture provenance, page integrity, release and offline MCP dispatch."""

import json

import pytest
from test_circuit_rc_gui import mcp_call
from test_result_contract import fixture_data
from test_waveform_measure import recipe

from cadai.mcp import McpServer

ARGS = dict(
    context_ref="design:source",
    history="CustomHistory.9",
    test="arbitrary_characterization",
    corner="slow_hot",
    point=7,
    analysis="tran",
    signal='/escaped"signal',
)


class Client:
    def __init__(self, mutation=None):
        self.calls = []
        self.mutation = mutation

    def call(self, name, args):
        assert name == "eval_skill_native"
        code = args["code"]
        self.calls.append(code)
        meta, records, _, _ = fixture_data()
        if code.startswith("aiResultsCapture"):
            detail = dict(meta, ok=True, pages=1, context_ref=ARGS["context_ref"])
        elif code.startswith("aiResultsPage"):
            detail = dict(ok=True, records=records)
        elif code.startswith("aiWaveCapture"):
            token = json.loads(code.rsplit(" ", 1)[1][:-1])
            detail = dict(
                ok=True,
                **ARGS,
                target=meta["target"],
                session="s1",
                capture_id=token,
                results_directory="/work/psf",
                pages=2,
                sample_count=257,
                status="waveform",
                x_unit="s",
                y_unit="V",
            )
            if self.mutation:
                self.mutation(detail)
        elif code.startswith("aiWavePage"):
            points = range(256) if code.endswith(" 0)") else range(256, 257)
            detail = dict(ok=True, samples=[[i / 128, i / 128] for i in points])
        else:
            detail = dict(ok=True)
        return True, dict(value=json.dumps(detail))


def test_registered_mcp_capture_and_offline_measurement(tmp_path):
    client = Client()
    server = McpServer(client, workspace=tmp_path)
    captured = mcp_call(server, "read_maestro_waveform", ARGS)
    assert captured["ok"], captured
    assert captured["source"]["point"] == 7
    assert captured["source"]["coordinate"]["parameters"][1]["name"] == "supply"
    assert client.calls[-1].startswith("aiWaveRelease")
    n = len(client.calls)
    result = mcp_call(
        server,
        "measure_waveform",
        dict(waveform_ref=captured["waveform_ref"], recipes=[recipe("mean")]),
    )
    assert result["rows"][0]["value"] == pytest.approx(1)
    assert len(client.calls) == n
    assert '\\"signal' in next(c for c in client.calls if c.startswith("aiWaveCapture"))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(point=8),
        lambda m: m.update(history="Latest.99"),
        lambda m: m.update(target=["different", "cell", "view"]),
        lambda m: m.update(capture_id="wrong"),
        lambda m: m.update(session=None),
        lambda m: m.update(results_directory=None),
        lambda m: m.update(pages=True),
        lambda m: m.update(pages=257),
        lambda m: m.update(sample_count=258),
        lambda m: m.update(sample_count=65537),
        lambda m: m.update(status="family"),
        lambda m: m.update(status="unexpected"),
        lambda m: m.update(x_unit=["s"]),
    ],
)
def test_corrupt_capture_refused_and_private_pages_released(tmp_path, mutation):
    client = Client(mutation)
    result = mcp_call(McpServer(client, workspace=tmp_path), "read_maestro_waveform", ARGS)
    assert not result["ok"], result
    assert client.calls[-1].startswith("aiWaveRelease")
    assert not list((tmp_path / ".sico/ai/measurement-results").glob("waveform_*.json"))


@pytest.mark.parametrize("change", [dict(point=True), dict(analysis=""), dict(extra="field")])
def test_input_checked_before_dispatch(tmp_path, change):
    client = Client()
    response = McpServer(client, workspace=tmp_path).handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "read_maestro_waveform",
            "arguments": dict(ARGS, **change),
        },
    })
    assert response["error"]["data"]["code"] == "invalid_arguments"
    assert not client.calls


def test_page_exception_releases_capture(tmp_path):
    class FailedPage(Client):
        def call(self, name, args):
            if args["code"].startswith("aiWavePage"):
                return False, {"message": "unavailable page"}
            return super().call(name, args)

    client = FailedPage()
    assert not mcp_call(McpServer(client, workspace=tmp_path), "read_maestro_waveform", ARGS)["ok"]
    assert client.calls[-1].startswith("aiWaveRelease")
