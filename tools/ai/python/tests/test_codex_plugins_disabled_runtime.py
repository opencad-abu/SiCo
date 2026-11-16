"""Pinned native runtime keeps ordinary resources usable without plugin sync."""

import json

import pytest
from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)
from test_codex_resources_runtime import cap_read, make_resources, output, tool_call


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_multiturn_skills_and_capabilities_without_github_sync(
        tmp_path, gateway, native_codex, tool_format, monkeypatch):  # noqa: F811
    manifest, config = make_resources(tmp_path)
    config["plugins"] = []
    manifest.write_text(json.dumps(config))
    provider = settings(gateway, native_codex, tool_format)
    provider.options["external_resources"] = str(manifest)
    calls = []
    original = AppServer.request

    def request(self, method, params, **kwargs):
        calls.append(method)
        return original(self, method, params, **kwargs)

    monkeypatch.setattr(AppServer, "request", request)
    gateway.responses.put(lambda request: tool_call(
        request, "skills", "list", "skills-list", {"authority": {"kind": "executor"}}))
    gateway.responses.put(cap_read)
    gateway.responses.put(answer("Capability proof received."))
    with SessionJournal(tmp_path, "no-plugin-sync") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.run("Read the local capability proof."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            assert "B_RESOURCE_PROOF" in output(gateway.requests[-1], "skills-read")
            assert "b-user-proof" in json.dumps(gateway.requests[0])
            thread = backend.thread_id
            gateway.responses.put(answer("Second turn completed."))
            events = list(backend.run("Continue this conversation."))
            assert events[-1]["kind"] == "task.completed", events[-5:]
            assert backend.thread_id == thread
            home = backend.runtime.home
        finally:
            backend.close()
        assert {method for method in calls if method.startswith("plugin/")} <= {
            "plugin/installed"}
        for name in ("plugins.sync.lock", "plugins.sha", "plugins"):
            assert not (home / ".tmp" / name).exists()
