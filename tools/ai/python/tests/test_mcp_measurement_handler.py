"""Measurement routing and verification-profile rejection contracts."""

import json

import pytest

from cadai import mcp_measurement_handler as handler
from cadai.mcp import _TOOLS_BY_NAME, McpServer
from cadai.result_contract import ResultError


@pytest.mark.parametrize("name,arguments,operation,code", [
    ("list_measurement_capabilities", {}, "call_catalog", "measurement_catalog_error"),
    ("query_ac_response", {"report_ref": "ac_response_" + "a" * 64},
     "call_response", "ac_response_error"),
    ("query_ac_waveform", {"waveform_ref": "ac_waveform_" + "a" * 64},
     "call_ac", "ac_waveform_error"),
    ("query_waveform_spec_report", {"report_ref": "wave_specs_" + "a" * 64},
     "call_waveform_specs", "waveform_spec_error"),
    ("query_waveform", {"waveform_ref": "waveform_" + "a" * 64},
     "call_waveform", "waveform_error"),
    ("query_maestro_results", {"result_ref": "results_" + "a" * 64},
     "call_result_tool", "measurement_results_error"),
])
def test_domain_failure_keeps_mcp_tool_error_mapping(
    monkeypatch, name, arguments, operation, code
):
    def fail(*args, **kwargs):
        raise ResultError("fixture failure")

    monkeypatch.setattr(handler, operation, fail)
    response = McpServer(None).handle({
        "id": 7, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert response["result"]["isError"]
    detail = json.loads(response["result"]["content"][0]["text"])
    assert detail["ok"] is False
    assert detail["code"] == code
    assert detail["cause"] == "fixture failure"
    assert detail["message"].startswith("fixture failure")
    assert detail["tool"] == name
    assert detail["retry_same_request"] is False


def test_verification_rejects_every_measurement_tool_before_domain_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("restricted tools must not execute")

    for name in ("call_catalog", "call_response", "call_ac", "call_waveform_specs",
                 "call_waveform", "call_result_tool"):
        monkeypatch.setattr(handler, name, forbidden)
    server = McpServer(None, profile="verification")
    assert handler.MEASUREMENT_NAMES <= _TOOLS_BY_NAME.keys()
    for name in handler.MEASUREMENT_NAMES:
        response = server.handle({"id": name, "method": "tools/call", "params": {"name": name}})
        assert response["error"]["code"] == -32602
