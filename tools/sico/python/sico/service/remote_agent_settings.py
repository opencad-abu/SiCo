"""Narrow project settings facade; no paths, credentials or backend handles from the UI."""

from copy import deepcopy

from ..transport.framing import ProtocolError
from .frontend_session import SessionToken
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress


class RemoteAgentSettings:
    def __init__(self, api):
        self._api = api

    def _address(self, token):
        # A preview ID intentionally reads retained history without opening a backend.
        if isinstance(token, str):
            return SessionAddress(self._api.descriptor.project_id, self._api.descriptor.service_id,
                                  SessionToken(token, None)).record()
        return None if token is None else self._api.address(token).record()

    def read(self, token=None):
        def publish(value):
            required = {"project", "supported_kinds", "project_defaults",
                        "selected_defaults", "session", "defaults_scope"}
            if (not isinstance(value, dict) or not required <= set(value)
                    or set(value) - required - {"pdk_workflow", "model_settings",
                                                "data_sources"}):
                raise ProtocolError("Invalid settings result")
            if not isinstance(value["project"], str) or not isinstance(value["supported_kinds"], list):
                raise ProtocolError("Invalid settings result")
            workflow = value.get("pdk_workflow")
            if workflow is not None and (
                not isinstance(workflow, dict)
                or workflow.get("status") not in {"available", "unavailable"}
            ):
                raise ProtocolError("Invalid settings mode")
            model = value.get("model_settings")
            if model is not None and (
                not isinstance(model, dict)
                or model.get("status") not in {"available", "unavailable"}
            ):
                raise ProtocolError("Invalid settings model")
            data_sources = value.get("data_sources")
            if data_sources is not None and (
                not isinstance(data_sources, dict)
                or data_sources.get("status") not in {"available", "unavailable"}
            ):
                raise ProtocolError("Invalid settings data sources")
            return value
        return self._request(self._api._queries, "agent_settings", {}, token, publish)

    def save(self, manifest, expected_revision, token=None):
        def publish(value):
            outcome = value.get("outcome")
            if outcome not in {"saved", "conflict", "rejected", "uncertain"}:
                raise ProtocolError("Invalid settings save result")
            exact_fields(value, {"outcome", "document" if outcome == "saved" else "message"})
            return value
        return self._request(self._api._commands, "agent_settings_save", {
            "manifest": deepcopy(manifest), "expected_revision": expected_revision}, token, publish)

    def save_mode(self, pdk_workflow, expected_revision, token=None):
        def publish(value):
            outcome = value.get("outcome")
            if outcome not in {"saved", "conflict", "rejected", "uncertain"}:
                raise ProtocolError("Invalid settings mode save result")
            exact_fields(value, {"outcome", "document" if outcome == "saved" else "message"})
            return value
        return self._request(self._api._commands, "agent_mode_save", {
            "pdk_workflow": pdk_workflow, "expected_revision": expected_revision}, token, publish)

    def save_model(self, backend, provider, expected_revision, token=None):
        def publish(value):
            outcome = value.get("outcome")
            if outcome not in {"saved", "conflict", "rejected", "uncertain"}:
                raise ProtocolError("Invalid settings model save result")
            exact_fields(value, {"outcome", "document" if outcome == "saved" else "message"})
            return value
        return self._request(self._api._commands, "agent_model_save", {
            "backend": backend, "provider": provider,
            "expected_revision": expected_revision}, token, publish)

    def save_data_sources(self, pdk_data, circuit_templates, expected_revision, token=None):
        def publish(value):
            outcome = value.get("outcome")
            if outcome not in {"saved", "conflict", "rejected", "uncertain"}:
                raise ProtocolError("Invalid settings data source save result")
            exact_fields(value, {"outcome", "document" if outcome == "saved" else "message"})
            return value
        return self._request(self._api._commands, "agent_data_save", {
            "pdk_data": pdk_data, "circuit_templates": circuit_templates,
            "expected_revision": expected_revision}, token, publish)

    def check(self, identity, manifest, token=None, call=None, skill=None):
        return self._request(self._api._queries, "agent_settings_check", {
            "id": identity, "manifest": deepcopy(manifest), "call": deepcopy(call),
            "skill": deepcopy(skill)}, token,
            lambda value: value)

    def cancel_check(self, identity):
        return self._api._commands.request("agent_settings_check_cancel", {"id": identity})

    def knowledge(self, identity, manifest, action, arguments, token=None):
        return self._request(self._api._queries, "agent_knowledge", {
            "id": identity, "manifest": deepcopy(manifest), "action": action,
            "arguments": deepcopy(arguments)}, token, lambda value: value)

    def cancel_knowledge(self, identity):
        return self._api._commands.request("agent_knowledge_cancel", {"id": identity})

    def _request(self, lane, operation, values, token, publish):
        address, epoch = self._address(token), lane.epoch
        def validate():
            if self._api.detached or lane.epoch != epoch or self._address(token) != address:
                raise ValueError("设置来源连接已更换，请重新读取核对")
        def result(value):
            validate()
            return publish(value)
        return lane.request(operation, {"address": address, **values}, result, validate=validate)
