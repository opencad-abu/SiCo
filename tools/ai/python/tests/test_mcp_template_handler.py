"""Template-family routing contracts after MCP handler extraction."""

import json

import pytest

from cadai import mcp_template_handler as handler
from cadai.mcp import _TOOLS_BY_NAME, McpServer
from cadai.template_schema import TemplateError, TemplateUnavailable


@pytest.mark.parametrize(
    "name,arguments,operation,code",
    [
        ("query_circuit_templates", {}, "call_template", "template_catalog_unavailable"),
        ("inspect_template_symbol", {"symbol_ref": "template-symbol:s1"},
         "call_symbol", "template_symbol_unavailable"),
    ],
)
def test_template_domain_unavailability_keeps_error_mapping(
    monkeypatch, name, arguments, operation, code
):
    def fail(*args, **kwargs):
        raise TemplateUnavailable("fixture unavailable")

    monkeypatch.setattr(handler, operation, fail)
    response = McpServer(None).handle({
        "id": 7,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert response["result"]["isError"]
    detail = json.loads(response["result"]["content"][0]["text"])
    expected = (
        "template_extraction_unavailable"
        if name == "extract_circuit_templates"
        else code
    )
    assert detail["code"] == expected


def test_template_validation_stays_mcp_invalid_params(monkeypatch):
    def invalid(*args, **kwargs):
        raise TemplateError("fixture invalid")

    monkeypatch.setattr(handler, "call_template", invalid)
    response = McpServer(None).handle({
        "id": 8,
        "method": "tools/call",
        "params": {"name": "query_circuit_templates", "arguments": {"limit": 0}},
    })
    assert response["error"]["code"] == -32602


def test_template_handler_names_are_registered_and_verification_filters_them():
    names = (
        handler.TEMPLATE_CATALOG_NAMES
        | handler.TEMPLATE_SYMBOL_NAMES
        | handler.GENERIC_NAMES
        | handler.TEMPLATE_PLACEMENT_NAMES
        | {handler.TEMPLATE_CIRCUIT_NAME, handler.TEMPLATE_PREPARE_NAME}
    )
    assert names <= _TOOLS_BY_NAME.keys()
    server = McpServer(None, profile="verification")
    for name in names:
        response = server.handle({
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        })
        assert response["error"]["code"] == -32602


def test_template_handler_converts_domain_argument_errors(monkeypatch):
    def fail(*args, **kwargs):
        raise TemplateError("fixture invalid")

    monkeypatch.setattr(handler, "call_template", fail)
    with pytest.raises(handler.TemplateHandlerArgumentError, match="fixture invalid"):
        handler.dispatch_template(
            "query_circuit_templates", {}, workspace=None, client=None,
        )


def test_prepare_uses_registered_handler_and_retained_bindings(monkeypatch, tmp_path):
    from circuit_spec_fixture import sample

    server = McpServer(None, workspace=tmp_path)
    adapter = object()
    server._pdk_bindings = adapter
    calls = []

    def prepare(name, args, **kwargs):
        calls.append((name, args, kwargs))
        return {"status": "needs_geometry", "plan": None}

    monkeypatch.setattr(handler, "call_prepare", prepare)
    spec, _ = sample()
    arguments = {"spec": spec}
    response = server.handle({"id": 9, "method": "tools/call", "params": {
        "name": "prepare_circuit_template_plan", "arguments": arguments}})
    assert response["result"]["isError"] is False
    assert calls == [("prepare_circuit_template_plan", arguments,
                      {"workspace": tmp_path, "pdk_bindings": adapter})]
    assert server._pdk_session is None
