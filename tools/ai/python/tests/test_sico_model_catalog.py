"""Gateway model catalog: bounded listing, fallback and pinned runtime injection."""

from __future__ import annotations

import json
import os
import queue
import shlex
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from sico.codex.backend import CodexBackend
from sico.codex.model_catalog import prepare
from sico.codex.settings import CodexSettings
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    Handler,
    native_codex,
    registry,
)

TEMPLATE_ENTRY = {
    "slug": "gpt-template",
    "display_name": "GPT Template",
    "description": "Template",
    "default_reasoning_level": "medium",
    "supported_reasoning_levels": [{"effort": "medium", "description": "Balanced"}],
    "default_reasoning_summary": "auto",
    "supports_reasoning_summary_parameter": True,
    "support_verbosity": True,
    "default_verbosity": "high",
    "shell_type": "unified_exec",
    "visibility": "list",
    "supported_in_api": True,
    "priority": 9,
    "additional_speed_tiers": [],
    "service_tiers": [],
    "availability_nux": None,
    "upgrade": None,
    "model_messages": {"instructions_template": "Template instructions."},
    "truncation_policy": {"mode": "tokens", "limit": 10000},
    "input_modalities": ["text", "image"],
    "supports_image_detail_original": True,
    "experimental_supported_tools": [],
}


def bundled(*entries):
    return {"models": [dict(TEMPLATE_ENTRY, **entry) for entry in entries]}


class CatalogHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.paths.append(self.path)
        self.server.authorizations.append(self.headers.get("Authorization"))
        status, body = self.server.answer
        if status in {301, 302, 307, 308}:
            self.send_response(status)
            self.send_header("Location", self.server.location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), CatalogHandler)
    server.answer = (200, {"data": []})
    server.location = "/v1/redirected"
    server.paths, server.authorizations = [], []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def provider_for(gateway, model="deepseek-v4-flash", environment=None):
    values = dict(os.environ)
    values.pop("SICO_REASONING_EFFORTS", None)
    values.update(environment or {})
    return SimpleNamespace(
        models_endpoint=f"http://127.0.0.1:{gateway.server_port}/v1/models",
        model=model,
        api_key="catalog-test-secret",
        environment=values,
    )


def stub_codex(tmp_path, catalog, exit_code=None):
    payload = tmp_path / "bundled-models.json"
    payload.write_text(json.dumps(catalog))
    script = tmp_path / "codex-stub"
    if exit_code is None:
        script.write_text("#!/bin/sh\nexec cat " + shlex.quote(str(payload)) + "\n")
    else:
        script.write_text("#!/bin/sh\nexit " + str(exit_code) + "\n")
    script.chmod(0o755)
    return str(script)


def read_entries(home):
    raw = (home / "model_catalog.json").read_text()
    return json.loads(raw)["models"]


def test_gateway_listing_drives_the_catalog(gateway, tmp_path):
    gateway.answer = (200, {"data": [
        {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
        {"id": "deepseek-reasoner", "description": "Reasoning model"},
    ]})
    report = prepare(provider_for(gateway), tmp_path,
                     stub_codex(tmp_path, bundled({"slug": "gpt-template"})))
    assert report == {"path": tmp_path / "model_catalog.json", "source": "gateway",
                      "models": 2, "reason": ""}
    entries = read_entries(tmp_path)
    assert [entry["slug"] for entry in entries] == ["deepseek-v4-flash", "deepseek-reasoner"]
    assert entries[0]["display_name"] == "DeepSeek V4 Flash"
    assert entries[1]["display_name"] == "deepseek-reasoner"
    assert entries[0]["description"] is None
    for entry in entries:
        assert entry["visibility"] == "list"
        assert entry["supported_in_api"] is True
        assert entry["input_modalities"] == ["text"]
        assert entry["supported_reasoning_levels"] == [
            {"effort": "low", "description": ""},
            {"effort": "medium", "description": ""},
            {"effort": "high", "description": ""},
            {"effort": "max", "description": ""},
        ]
        assert entry["default_reasoning_level"] is None
        assert entry["default_reasoning_summary"] == "none"
        assert entry["supports_reasoning_summary_parameter"] is False
        assert entry["support_verbosity"] is False
        assert entry["default_verbosity"] is None
        assert entry["upgrade"] is None
        assert entry["model_messages"] == {"instructions_template": "Template instructions."}
    assert [entry["priority"] for entry in entries] == [1, 2]
    assert gateway.paths == ["/v1/models"]
    assert gateway.authorizations == ["Bearer catalog-test-secret"]
    assert (tmp_path / "model_catalog.json").stat().st_mode & 0o777 == 0o600


def test_alternate_keys_and_duplicates_keep_listing_order(gateway, tmp_path):
    gateway.answer = (200, {"models": [
        {"slug": "deepseek-v4-flash"},
        {"model": "deepseek-reasoner"},
        {"id": "deepseek-v4-flash"},
        {"id": "   "},
        {"id": "bad\nname"},
        "not-a-row",
    ]})
    prepare(provider_for(gateway), tmp_path, stub_codex(tmp_path, bundled({})))
    assert [entry["slug"] for entry in read_entries(tmp_path)] == [
        "deepseek-v4-flash", "deepseek-reasoner"]


@pytest.mark.parametrize("answer,reason", [
    ((500, {"error": "boom"}), "http_500"),
    ((200, b"{not json"), "invalid"),
    ((200, {"data": "no"}), "invalid"),
    ((200, {"data": []}), "empty"),
    ((200, b"[" + b" " * 262_200 + b"]"), "invalid"),
])
def test_failed_listing_keeps_configured_model_only(gateway, tmp_path, answer, reason):
    gateway.answer = answer
    report = prepare(provider_for(gateway), tmp_path, stub_codex(tmp_path, bundled({})))
    assert report["source"] == "fallback"
    assert report["reason"] == reason
    assert [entry["slug"] for entry in read_entries(tmp_path)] == ["deepseek-v4-flash"]


def test_unreachable_listing_falls_back(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    provider = SimpleNamespace(models_endpoint=f"http://127.0.0.1:{port}/v1/models",
                               model="deepseek-v4-flash", api_key="secret",
                               environment=dict(os.environ))
    report = prepare(provider, tmp_path, stub_codex(tmp_path, bundled({})))
    assert (report["source"], report["reason"]) == ("fallback", "unreachable")


def test_redirect_is_not_followed(gateway, tmp_path):
    gateway.answer = (302, {})
    report = prepare(provider_for(gateway), tmp_path, stub_codex(tmp_path, bundled({})))
    assert (report["source"], report["reason"]) == ("fallback", "http_302")
    assert gateway.paths == ["/v1/models"]


def test_configured_model_is_inserted_when_missing(gateway, tmp_path):
    gateway.answer = (200, {"data": [{"id": "deepseek-reasoner"}]})
    prepare(provider_for(gateway), tmp_path, stub_codex(tmp_path, bundled({})))
    assert [entry["slug"] for entry in read_entries(tmp_path)] == [
        "deepseek-v4-flash", "deepseek-reasoner"]


def test_declared_capabilities_pass_through(gateway, tmp_path):
    gateway.answer = (200, {"data": [{
        "id": "deepseek-v4-flash",
        "supported_reasoning_levels": [{"effort": "low"},
                                       {"effort": "high", "description": "Deep"}],
        "default_reasoning_level": "high",
        "input_modalities": ["text", "image"],
        "service_tiers": [{"id": "priority", "name": "Fast"}],
    }]})
    prepare(provider_for(gateway), tmp_path, stub_codex(tmp_path, bundled({})))
    entry = read_entries(tmp_path)[0]
    assert entry["supported_reasoning_levels"] == [
        {"effort": "low", "description": ""}, {"effort": "high", "description": "Deep"}]
    assert entry["default_reasoning_level"] == "high"
    assert entry["input_modalities"] == ["text", "image"]
    assert entry["service_tiers"] == [{"id": "priority", "name": "Fast"}]


def test_provider_effort_and_context_window_pass_through(gateway, tmp_path):
    """The observed provider shape: name/effort/context_window/input_modalities."""
    gateway.answer = (200, {"data": [{
        "id": "deepseek-flash",
        "object": "model",
        "owned_by": "deepseek",
        "name": "DeepSeek-V4.1-Flash",
        "context_window": 1048576,
        "max_output_tokens": 393216,
        "input_modalities": ["text", "image"],
        "output_modalities": ["text"],
        "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"},
        "api_capabilities": {"anthropic_messages": {"system_prompt_update": "in-history"}},
    }]})
    prepare(provider_for(gateway, model="deepseek-v4-flash"), tmp_path,
            stub_codex(tmp_path, bundled({})))
    configured, gateway_row = read_entries(tmp_path)
    assert configured["slug"] == "deepseek-v4-flash"
    assert configured["supported_reasoning_levels"] == [
        {"effort": "low", "description": ""},
        {"effort": "medium", "description": ""},
        {"effort": "high", "description": ""},
        {"effort": "max", "description": ""}]
    assert gateway_row["display_name"] == "DeepSeek-V4.1-Flash"
    assert gateway_row["supported_reasoning_levels"] == [
        {"effort": "low", "description": ""},
        {"effort": "high", "description": ""},
        {"effort": "max", "description": ""}]
    assert gateway_row["default_reasoning_level"] == "high"
    assert gateway_row["context_window"] == 1048576
    assert gateway_row["max_context_window"] == 1048576
    assert gateway_row["input_modalities"] == ["text", "image"]


@pytest.mark.parametrize("row", [
    {"id": "m", "supported_reasoning_levels": "high"},
    {"id": "m", "supported_reasoning_levels": [{"effort": 3}]},
    {"id": "m", "default_reasoning_level": ""},
    {"id": "m", "input_modalities": "text"},
    {"id": "m", "input_modalities": ["video"]},
    {"id": "m", "service_tiers": [{"id": "priority"}]},
    {"id": "m", "effort": "high"},
    {"id": "m", "effort": {"supported_levels": []}},
    {"id": "m", "effort": {"supported_levels": ["  "]}},
    {"id": "m", "effort": {"supported_levels": ["low"], "default_level": 3}},
    {"id": "m", "context_window": "big"},
    {"id": "m", "context_window": 1024},
    {"id": "m", "context_window": True},
])
def test_malformed_capability_declarations_fall_back(gateway, tmp_path, row):
    gateway.answer = (200, {"data": [row]})
    report = prepare(provider_for(gateway, model="fallback-model"), tmp_path,
                     stub_codex(tmp_path, bundled({})))
    assert (report["source"], report["reason"]) == ("fallback", "invalid")
    assert [entry["slug"] for entry in read_entries(tmp_path)] == ["fallback-model"]


def test_reasoning_fallback_can_be_configured_or_disabled(gateway, tmp_path):
    gateway.answer = (200, {"data": [{"id": "gateway-model"}]})
    template = stub_codex(tmp_path, bundled({}))

    configured = tmp_path / "configured"
    configured.mkdir()
    provider = provider_for(
        gateway, environment={"SICO_REASONING_EFFORTS": "minimal, high, minimal"})
    prepare(provider, configured, template)
    assert [row["effort"] for row in read_entries(configured)[0][
        "supported_reasoning_levels"]] == ["minimal", "high"]

    disabled = tmp_path / "disabled"
    disabled.mkdir()
    provider = provider_for(gateway, environment={"SICO_REASONING_EFFORTS": "none"})
    prepare(provider, disabled, template)
    assert read_entries(disabled)[0]["supported_reasoning_levels"] == []


def test_template_prefers_the_configured_slug(gateway, tmp_path):
    gateway.answer = (200, {"data": [{"id": "deepseek-v4-flash"}]})
    catalog = bundled(
        {"slug": "gpt-flagship", "priority": 1, "marker": "flagship"},
        {"slug": "gpt-plain", "priority": 12, "marker": "plain"},
        {"slug": "gpt-hidden", "priority": 99, "visibility": "hide", "marker": "hidden"},
        {"slug": "deepseek-v4-flash", "priority": 50, "marker": "exact"},
    )
    prepare(provider_for(gateway), tmp_path, stub_codex(tmp_path, catalog))
    assert read_entries(tmp_path)[0]["marker"] == "exact"


def test_template_uses_last_picker_visible_entry_by_default(gateway, tmp_path):
    gateway.answer = (200, {"data": [{"id": "deepseek-reasoner"}]})
    catalog = bundled(
        {"slug": "gpt-flagship", "priority": 1, "marker": "flagship"},
        {"slug": "gpt-plain", "priority": 12, "marker": "plain"},
        {"slug": "gpt-hidden", "priority": 99, "visibility": "hide", "marker": "hidden"},
    )
    prepare(provider_for(gateway, model="deepseek-v4-flash"), tmp_path,
            stub_codex(tmp_path, catalog))
    entries = read_entries(tmp_path)
    assert [entry["marker"] for entry in entries] == ["plain", "plain"]


def test_template_probe_failure_disables_injection(gateway, tmp_path):
    gateway.answer = (200, {"data": [{"id": "deepseek-v4-flash"}]})
    report = prepare(provider_for(gateway), tmp_path, stub_codex(tmp_path, {}, exit_code=2))
    assert report == {"path": None, "source": "none", "models": 0,
                      "reason": "template_unavailable"}
    assert not (tmp_path / "model_catalog.json").exists()


@pytest.mark.parametrize("base_url,expected", [
    ("https://api.deepseek.com", "https://api.deepseek.com/v1/models"),
    ("https://proxy.example/v1", "https://proxy.example/v1/models"),
    ("https://proxy.example/v1/responses", "https://proxy.example/v1/models"),
])
def test_models_endpoint_derivation(base_url, expected):
    provider = CodexSettings({"provider": "responses", "base_url": base_url, "model": "m"},
                             {"SICO_API_KEY": "secret"})
    assert provider.models_endpoint == expected
    assert provider.base_url == expected[: -len("/models")]


class CatalogGatewayHandler(Handler):
    def do_GET(self):  # noqa: N802
        self.server.paths.append(self.path)
        self.server.authorizations.append(self.headers.get("Authorization"))
        data = json.dumps({"data": [
            {"id": "deepseek-flash", "name": "DeepSeek-V4.1-Flash",
             "context_window": 1048576, "input_modalities": ["text", "image"],
             "effort": {"supported_levels": ["low", "high", "max"],
                        "default_level": "high"}},
            {"id": "deepseek-v4-pro", "name": "DeepSeek-V4-Pro"},
        ]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def catalog_gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), CatalogGatewayHandler)
    server.responses = queue.Queue()
    server.requests, server.paths, server.authorizations = [], [], []
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_pinned_runtime_lists_only_gateway_models(
        tmp_path, native_codex, catalog_gateway):  # noqa: F811
    provider = CodexSettings(
        {"backend": "codex", "provider": "responses",
         "base_url": f"http://127.0.0.1:{catalog_gateway.server_port}/v1",
         "model": "deepseek-v4-flash", "codex_executable": native_codex,
         "tool_format": "native", "timeout": 10},
        {**os.environ, "SICO_API_KEY": "copilot-test-secret",
         "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )
    with SessionJournal(tmp_path, "catalog") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            status = backend.turn_options.status()
            models = [row["model"] for row in status["models"]]
            assert models == ["deepseek-v4-flash", "deepseek-flash", "deepseek-v4-pro"]
            assert catalog_gateway.paths == ["/v1/models"]
            assert catalog_gateway.authorizations == ["Bearer copilot-test-secret"]
            written = json.loads((journal.directory / "codex/model_catalog.json").read_text())
            assert [row["slug"] for row in written["models"]] == models
            advertised = status["models"][1]
            assert advertised["defaultReasoningEffort"] == "high"
            assert [row["reasoningEffort"] for row in advertised["supportedReasoningEfforts"]] == [
                "low", "high", "max"]
            configured = status["models"][0]
            assert [row["reasoningEffort"]
                    for row in configured["supportedReasoningEfforts"]] == [
                "low", "medium", "high", "max"]
            assert advertised["inputModalities"] == ["text", "image"]
        finally:
            backend.close()
