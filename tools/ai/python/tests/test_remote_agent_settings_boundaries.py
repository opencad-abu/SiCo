"""The settings facade validates wire values and rejects stale source connections."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sico.service.frontend_session import SessionToken
from sico.service.remote_agent_settings import RemoteAgentSettings
from sico.transport.framing import ProtocolError

PROJECT = "a" * 32
SERVICE = "b" * 32
RUNTIME = "c" * 32
HISTORY = "old"


class Lane:
    def __init__(self, value=None, epoch=1):
        self.value, self.epoch, self.calls = value, epoch, []

    def request(self, operation, values, result=None, validate=None):
        self.calls.append((operation, deepcopy(values)))
        if validate is not None:
            validate()
        return values if result is None else result(self.value)


class Api:
    def __init__(self):
        self.descriptor = SimpleNamespace(project_id=PROJECT, service_id=SERVICE)
        self._queries = Lane()
        self._commands = Lane()
        self.detached = False
        self.addresses = []

    def address(self, token):
        self.addresses.append(token)
        return SimpleNamespace(record=lambda: {
            "project_id": PROJECT, "service_id": SERVICE,
            "session_id": token.session_id, "runtime_id": token.runtime_id})


def valid_read():
    return {
        "project": "/project",
        "supported_kinds": ["skill_roots"],
        "project_defaults": {"status": "available"},
        "selected_defaults": {"status": "available"},
        "session": None,
        "defaults_scope": "service",
        "pdk_workflow": {"status": "available"},
        "model_settings": {"status": "unavailable"},
        "data_sources": {"status": "available"},
    }


def test_read_addresses_history_and_passes_validated_values():
    api = Api()
    api._queries.value = valid_read()
    settings = RemoteAgentSettings(api)
    assert settings.read(HISTORY) == api._queries.value
    assert api._queries.calls == [("agent_settings", {
        "address": {"project_id": PROJECT, "service_id": SERVICE,
                    "session_id": HISTORY, "runtime_id": None},
    })]

    assert settings.read(None) == api._queries.value
    assert api._queries.calls[-1][1] == {"address": None}

    token = SessionToken("live", RUNTIME)
    assert settings.read(token) == api._queries.value
    assert api._queries.calls[-1][1]["address"]["runtime_id"] == RUNTIME


@pytest.mark.parametrize("change", [
    "not-a-map",
    "missing",
    "extra",
    "project",
    "supported",
    "workflow-shape",
    "workflow-status",
    "model-shape",
    "model-status",
    "data-shape",
    "data-status",
])
def test_read_rejects_an_invalid_settings_projection(change):
    api = Api()
    value = valid_read()
    if change == "not-a-map":
        value = []
    elif change == "missing":
        value.pop("session")
    elif change == "extra":
        value["unexpected"] = True
    elif change == "project":
        value["project"] = 3
    elif change == "supported":
        value["supported_kinds"] = {}
    elif change == "workflow-shape":
        value["pdk_workflow"] = []
    elif change == "workflow-status":
        value["pdk_workflow"] = {"status": "unknown"}
    elif change == "model-shape":
        value["model_settings"] = "model"
    elif change == "model-status":
        value["model_settings"] = {"status": "unknown"}
    elif change == "data-shape":
        value["data_sources"] = []
    else:
        value["data_sources"] = {"status": "unknown"}
    api._queries.value = value
    with pytest.raises(ProtocolError):
        RemoteAgentSettings(api).read()


@pytest.mark.parametrize("method,args,operation,keys", [
    ("save", ({"version": 1}, None), "agent_settings_save",
     {"manifest": {"version": 1}, "expected_revision": None}),
    ("save_mode", ("design", "a" * 64), "agent_mode_save",
     {"pdk_workflow": "design", "expected_revision": "a" * 64}),
    ("save_model", ("python", "openai", None), "agent_model_save",
     {"backend": "python", "provider": "openai", "expected_revision": None}),
    ("save_data_sources", ("/pdk", None, None), "agent_data_save",
     {"pdk_data": "/pdk", "circuit_templates": None, "expected_revision": None}),
])
@pytest.mark.parametrize("outcome", ["saved", "conflict", "rejected", "uncertain"])
def test_save_methods_accept_only_explicit_outcomes(method, args, operation, keys, outcome):
    api = Api()
    value = {"outcome": outcome}
    value["document" if outcome == "saved" else "message"] = (
        {"revision": None} if outcome == "saved" else "not saved")
    api._commands.value = value
    assert getattr(RemoteAgentSettings(api), method)(*args, token=HISTORY) == value
    captured = api._commands.calls[0][1]
    assert api._commands.calls[0][0] == operation
    assert captured.pop("address")["session_id"] == HISTORY
    assert captured == keys


@pytest.mark.parametrize("method,args", [
    ("save", ({"version": 1}, None)),
    ("save_mode", ("design", None)),
    ("save_model", ("python", "openai", None)),
    ("save_data_sources", ("/pdk", None, None)),
])
@pytest.mark.parametrize("value", [
    {},
    {"outcome": "unknown", "message": "x"},
    {"outcome": "saved", "message": "wrong field"},
    {"outcome": "conflict"},
    {"outcome": "conflict", "message": "x", "document": {}},
])
def test_save_methods_reject_invalid_receipts(method, args, value):
    api = Api()
    api._commands.value = value
    with pytest.raises(ProtocolError):
        getattr(RemoteAgentSettings(api), method)(*args)


def test_check_knowledge_and_cancellation_keep_deep_copies_and_identity():
    api = Api()
    api._queries.value = {"status": "checked"}
    settings = RemoteAgentSettings(api)
    manifest = {"skill_roots": [{"id": "one"}]}
    call, skill, arguments = {"query": "x"}, {"name": "same"}, {"tool": "read"}
    assert settings.check("check-id", manifest, HISTORY, call=call, skill=skill) == {
        "status": "checked"}
    assert api._queries.calls[-1][0] == "agent_settings_check"
    assert api._queries.calls[-1][1]["id"] == "check-id"
    manifest["skill_roots"][0]["id"] = "changed"
    call["query"] = "changed"
    skill["name"] = "changed"
    assert api._queries.calls[-1][1]["manifest"]["skill_roots"][0]["id"] == "one"
    assert api._queries.calls[-1][1]["call"] == {"query": "x"}
    assert api._queries.calls[-1][1]["skill"] == {"name": "same"}

    api._queries.value = {"status": "knowledge"}
    assert settings.knowledge("knowledge-id", manifest, "preview", arguments) == {
        "status": "knowledge"}
    assert api._queries.calls[-1][0] == "agent_knowledge"
    arguments["tool"] = "changed"
    assert api._queries.calls[-1][1]["arguments"] == {"tool": "read"}

    settings.cancel_check("check-id")
    assert api._commands.calls[-1] == ("agent_settings_check_cancel", {"id": "check-id"})
    settings.cancel_knowledge("knowledge-id")
    assert api._commands.calls[-1] == ("agent_knowledge_cancel",
                                       {"id": "knowledge-id"})


@pytest.mark.parametrize("change", ["detached", "epoch", "address"])
def test_stale_source_connection_is_rejected_before_publishing(change, monkeypatch):
    api = Api()
    settings = RemoteAgentSettings(api)
    lane = api._queries
    original = lane.request

    def stale(operation, values, result=None, validate=None):
        if change == "detached":
            api.detached = True
        elif change == "epoch":
            lane.epoch += 1
        else:
            monkeypatch.setattr(api, "address",
                                lambda token: SimpleNamespace(record=lambda: {
                                    "project_id": PROJECT,
                                    "service_id": SERVICE,
                                    "session_id": token.session_id,
                                    "runtime_id": "d" * 32}))
        return original(operation, values, result, validate)

    lane.request = stale
    with pytest.raises(ValueError, match="连接已更换"):
        settings.read(SessionToken("live", RUNTIME))
