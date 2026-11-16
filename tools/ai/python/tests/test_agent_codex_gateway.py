"""Opt-in Responses gateway acceptance using real Codex and synthetic read-only MCP data."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import Counter
from pathlib import Path

import pytest
from test_agent_codex_native import (  # noqa: F401
    answer,
    gateway,
    native_codex,
    settings,
    sse,
)

from sico.codex.backend import CodexBackend
from sico.codex.settings import CodexSettings
from sico.core.contracts import BoundContext, ToolResult
from sico.core.tools import ReadTool, ToolRegistry, no_arguments
from sico.providers.config import load_provider
from sico.storage.journal import SessionJournal, open_private

CONTEXT = BoundContext(
    "gateway-acceptance", "synthetic-generation", "synthetic-target", {"valid": True}
)


def require(condition, message):
    # Do not let pytest's assertion rewriting print credentials or model error bodies.
    if not condition:
        pytest.fail(message, pytrace=False)


def probe_tools(receipts):
    tools = ToolRegistry()

    def read(inputs, target):
        receipt = "receipt-" + uuid.uuid4().hex
        receipts.append(receipt)
        return ToolResult("ok", "Synthetic gateway acceptance data", {"receipt": receipt})

    tools.register(
        ReadTool(
            "read_gateway_probe",
            "Return a fresh receipt for this synthetic, read-only gateway acceptance test.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            no_arguments,
            read,
        )
    )
    return tools


def check_process_credentials(backend, secrets):
    child = backend.runtime.rpc.child
    require(
        all(secret not in str(child.args).encode() for secret in secrets),
        "A model credential appeared in app-server argv",
    )
    pending, found = [str(child.pid)], False
    visited = set()
    while pending:
        pid = pending.pop()
        if pid in visited:
            continue
        visited.add(pid)
        folder = Path("/proc") / pid
        try:
            command = (folder / "cmdline").read_bytes()
            if b"sico.codex.mcp" in command.split(b"\0"):
                environment = (folder / "environ").read_bytes()
                require(
                    all(secret not in environment and secret not in command for secret in secrets),
                    "A model credential reached the MCP subprocess",
                )
                found = True
            for task in (folder / "task").iterdir():
                pending.extend((task / "children").read_text().split())
        except FileNotFoundError:
            continue
    require(found, "The test-owned MCP subprocess was not found")


def check_recorded_credentials(directory, secrets):
    overlap = max(map(len, secrets)) - 1
    for path in directory.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            tail = b""
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                data = tail + chunk
                require(
                    all(secret not in data for secret in secrets),
                    "A model credential appeared in private session evidence",
                )
                tail = data[-overlap:] if overlap else b""


def exercise_gateway(tmp_path, provider, receipts, *, scope):
    report = {
        "scope": scope,
        "cad_data": "synthetic read-only fixture; no Virtuoso or simulation",
        "endpoint": provider.endpoint,
        "model": provider.model,
        "tool_format": provider.tool_format,
        "codex_version": "0.162.1",
        "status": "failed",
        "phases": [],
    }
    secrets = [provider.api_key.encode()]
    tools = probe_tools(receipts)
    thread_id, first_receipt = None, None
    try:
        for turns in (("first", "continuation"), ("resumed_thread",)):
            with SessionJournal(tmp_path, "gateway") as journal:
                backend = CodexBackend(provider, tools, journal, CONTEXT)
                try:
                    # The middle turn stays in the same app-server; only the last phase resumes.
                    for turn in turns:
                        count = len(receipts)
                        prompt = (
                            "This is a synthetic gateway acceptance test. Call read_gateway_probe "
                            "exactly once, then include its exact receipt in your answer. "
                            "There is no real design and no design operation is needed."
                        )
                        if turn != "first":
                            prompt += " Also repeat the receipt from the first turn of this thread."
                        deadline = time.monotonic() + 90
                        events = list(
                            backend.run(
                                prompt,
                                lambda: time.monotonic() > deadline,
                                input_id="gateway-" + turn,
                            )
                        )
                        row = {
                            "phase": turn,
                            "status": backend.state.task["status"],
                            "tool_calls": len(receipts) - count,
                            "events": dict(Counter(event["kind"] for event in events)),
                        }
                        report["phases"].append(row)
                        require(row["status"] == "completed", "Gateway turn failed: " + turn)
                        require(row["tool_calls"] == 1, "Expected one actual MCP call: " + turn)
                        text = backend.state.task["text"]
                        require(receipts[-1] in text, "The model did not receive its tool receipt")
                        first_receipt = first_receipt or receipts[0]
                        require(first_receipt in text, "The model lost the first-turn tool receipt")
                        if thread_id:
                            require(backend.thread_id == thread_id, "Codex thread identity changed")
                        thread_id = backend.thread_id
                        if backend.runtime.wrapper:
                            secrets.append(backend.runtime.wrapper.token.encode())
                        check_process_credentials(backend, secrets)
                        row.update(
                            tool_result_continued=True, history_preserved=True, key_isolated=True
                        )
                finally:
                    backend.close()
                    check_recorded_credentials(journal.directory, secrets)
        report["status"] = "passed"
    finally:
        fd = open_private(
            tmp_path / "gateway-acceptance.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY
        )
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    return report


@pytest.mark.skipif(
    os.environ.get("CAD_TEST_COPILOT_GATEWAY") != "1",
    reason="Set CAD_TEST_COPILOT_GATEWAY=1 and explicit Codex Responses provider configuration",
)
def test_live_responses_gateway(tmp_path):
    try:
        provider = load_provider(
            launch_dir=os.environ.get("CAD_TEST_COPILOT_LAUNCH_DIR", os.getcwd())
        )
        require(isinstance(provider, CodexSettings), "Live acceptance requires backend=codex")
        provider.executable()
    except Exception as exc:
        pytest.fail(
            "Live Codex configuration is invalid (" + type(exc).__name__ + ")", pytrace=False
        )
    exercise_gateway(tmp_path, provider, [], scope="configured gateway; deployment unverified")


@pytest.mark.parametrize("tool_format", ["flat", "native"])
def test_gateway_acceptance_driver(tmp_path, native_codex, gateway, tool_format):  # noqa: F811
    receipts = []
    for index in range(3):
        name = (
            {"name": "mcp__sico_ic__read_gateway_probe"}
            if tool_format == "flat"
            else {"namespace": "mcp__sico_ic", "name": "read_gateway_probe"}
        )
        gateway.responses.put(
            sse(
                {
                    "type": "function_call",
                    "id": f"fc-{index}",
                    "call_id": f"call-{index}",
                    "arguments": "{}",
                    **name,
                }
            )
        )
        gateway.responses.put(lambda request: answer(" ".join(receipts)))
    report = exercise_gateway(
        tmp_path,
        settings(gateway, native_codex, tool_format),
        receipts,
        scope="local simulated model",
    )
    assert report["status"] == "passed"
    assert [row["phase"] for row in report["phases"]] == ["first", "continuation", "resumed_thread"]
    assert len(gateway.requests) == 6


@pytest.mark.parametrize("tool_format", ["flat", "native"])
def test_codex_preserves_explicit_responses_path(
    tmp_path, native_codex, gateway, tool_format  # noqa: F811
):
    provider = settings(gateway, native_codex, tool_format)
    provider = CodexSettings(
        {**provider.options, "base_url": f"http://127.0.0.1:{gateway.server_port}/api/v3/responses"},
        provider.environment,
    )
    gateway.responses.put(answer("OK"))
    with SessionJournal(tmp_path, "custom-path") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        try:
            events = list(backend.run("Reply OK.", input_id="path"))
            assert events[-1]["kind"] == "task.completed"
            assert gateway.paths == ["/api/v3/responses"]
        finally:
            backend.close()
