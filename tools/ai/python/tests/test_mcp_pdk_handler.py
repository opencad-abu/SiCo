"""PDK discovery and binding routing contracts after MCP handler extraction."""

import json

import pytest

from cadai import mcp_pdk_handler as handler
import cadai.mcp_bindings as mcp_module
from cadai.mcp import McpServer, _TOOLS_BY_NAME
from cadai.pdk_schema import PDK_NAMES, PdkArgumentError, PdkUnavailable
from cadai.pdk_binding_schema import BINDING_NAMES


class FailingSession:
    def call(self, name, arguments):
        raise PdkUnavailable("fixture_unavailable", "fixture unavailable")


class FailingBindings:
    def bind(self, arguments):
        raise PdkUnavailable("fixture_unavailable", "fixture unavailable")


@pytest.mark.parametrize("name", sorted(PDK_NAMES))
def test_pdk_session_unavailability_keeps_tool_error_mapping(name):
    ok, detail = handler.dispatch_pdk(
        name, {}, session=FailingSession(), bindings=None, client=None,
    )
    assert not ok
    assert detail["code"] == "fixture_unavailable"


def test_binding_unavailability_keeps_tool_error_mapping(monkeypatch):
    monkeypatch.setattr(handler, "call_extensions", lambda *args, **kwargs: (_ for _ in ()).throw(
        PdkUnavailable("fixture_unavailable", "fixture unavailable")
    ))
    ok, detail = handler.dispatch_pdk(
        "list_project_extensions", {}, session=None, bindings=None, client=None,
    )
    assert not ok and detail["code"] == "fixture_unavailable"

    ok, detail = handler.dispatch_pdk(
        "bind_pdk_device", {}, session=None, bindings=FailingBindings(), client=None,
    )
    assert not ok and detail["code"] == "fixture_unavailable"


def test_unprepared_pdk_fails_with_the_required_next_tool():
    class UnpreparedBindings:
        def bind(self, arguments):
            raise PdkUnavailable(
                "pdk_standard_unavailable",
                "standard package unavailable",
                {
                    "ok": False,
                    "status": "preparation_required",
                    "selection_required": False,
                    "next_action": "prepare_pdk_data",
                },
            )

    ok, detail = handler.dispatch_pdk(
        "bind_pdk_device", {}, session=None, bindings=UnpreparedBindings(), client=None,
    )
    assert not ok
    assert detail["code"] == "pdk_preparation_required"
    assert detail["next_action"] == "prepare_pdk_data"
    assert "do not reconstruct" in detail["message"]


def test_selection_error_without_a_pending_choice_is_not_a_success():
    class UnpreparedBindings:
        def bind(self, arguments):
            raise PdkUnavailable(
                "selection_required",
                "selection resolved to a single unprepared candidate",
                {
                    "ok": False,
                    "status": "preparation_required",
                    "selection_required": False,
                },
            )

    ok, detail = handler.dispatch_pdk(
        "bind_pdk_device", {}, session=None, bindings=UnpreparedBindings(), client=None,
    )
    assert not ok
    assert detail["code"] == "pdk_preparation_required"
    assert detail["selection_required"] is False


def test_pdk_argument_errors_become_handler_argument_errors():
    class InvalidSession:
        def call(self, name, arguments):
            raise PdkArgumentError("fixture invalid")

    with pytest.raises(handler.PdkHandlerArgumentError, match="fixture invalid"):
        handler.dispatch_pdk(
            "search_pdk_devices", {}, session=InvalidSession(), bindings=None, client=None,
        )


def test_pdk_names_are_registered_and_verification_filters_them():
    assert PDK_NAMES | BINDING_NAMES <= _TOOLS_BY_NAME.keys()
    server = McpServer(None, profile="verification")
    for name in PDK_NAMES | BINDING_NAMES:
        response = server.handle({
            "id": name,
            "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        })
        assert response["error"]["code"] == -32602


def test_mcp_server_preserves_pdk_unavailability_envelope(monkeypatch):
    monkeypatch.setattr(mcp_module, "dispatch_pdk", lambda *args, **kwargs: (
        False, {"ok": False, "code": "fixture_unavailable", "message": "fixture"}
    ))
    response = McpServer(None).handle({
        "id": 4,
        "method": "tools/call",
        "params": {"name": "search_pdk_devices", "arguments": {}},
    })
    assert response["result"]["isError"]
    assert json.loads(response["result"]["content"][0]["text"])["code"] == "fixture_unavailable"
