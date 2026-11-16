"""Shared contract, startup, transport and stale-target boundaries."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest

from cadai.entry_context import TOOLS, build_skill, call_context, enrich, validate_arguments
from cadai.launch import assistant_instructions
from cadai.mcp import McpServer
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, ModelTurn, NeedsReconcile, ToolCall
from sico.service.python_backend import PythonBackend
from sico.service.python_startup import initial_context
from sico.providers.mock import ScriptedProvider
from sico.storage.journal import SessionJournal
from sico.transport.broker import ContextBroker
from sico.transport.methods import QueryUnavailable

REF = "wctx_fixture_1"


def entry():
    return {
        "ok": True,
        "valid": True,
        "schema": "cad.entry.context.v1",
        "session_ref": "fixture",
        "source": {"window_ref": REF, "kind": "schematic", "title_bytes_hex": "e794b5e8b7af"},
    }


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_entry_context", {"code": "exit()"}),
        ("inspect_window", {}),
        ("inspect_window", {"window_ref": 'wctx_x_1") exit()'}),
        ("get_entry_context", {"max_items": True}),
        ("get_entry_context", {"max_items": 51}),
        ("list_windows", {"max_items": 201}),
        ("list_windows", {"offset": -1}),
        ("get_entry_context", {"include_base_libraries": 1}),
        ("get_entry_context", []),
    ],
)
def test_invalid_arguments_never_reach_transport(name, args):
    with pytest.raises(ValueError):
        build_skill(name, args)


def test_encoding_schema_identity_and_facts_guidance_separation():
    raw = entry()
    data = enrich(raw)
    assert data["source"]["title"] == "电路" and "guidance" not in raw
    assert data["guidance"]["api_preference"] == ["sch*", "db*"]
    raw["source"]["title_bytes_hex"] = "ae"
    assert enrich(raw)["source"]["title_status"] == "encoding_unknown"
    for key, value in (("ok", 1), ("valid", None), ("schema", "wrong"), ("session_ref", "other")):
        invalid = deepcopy(raw)
        invalid[key] = value
        with pytest.raises(ValueError):
            enrich(invalid)
    with pytest.raises(ValueError, match="mismatch"):
        enrich(raw, args={"window_ref": "wctx_fixture_2"})
    validate_arguments("list_windows", {"max_items": 200})


def test_question_guidance_uses_the_review_channel_without_a_stage():
    for kind in ("schematic", "layout", "ciw"):
        raw = entry()
        raw["source"]["kind"] = kind
        policy = enrich(raw)["guidance"]["policy"]
        assert "request_user_input" in policy
        assert "审阅中心" in policy
        assert "不得以普通消息反问" in policy
    from sico.codex.audit_instructions import INSTRUCTIONS as audit
    from sico.codex.backend_instructions import INSTRUCTIONS as codex

    assert "尚未选择工程 stage" in audit and "不要为提问先伪造 stage" in audit
    assert "不得以普通消息反问" in codex


def test_layout_guidance_avoids_workflow_and_preflight_name_guesses():
    raw = entry()
    raw["source"]["kind"] = "layout"
    guidance = enrich(raw)["guidance"]["next_read"]
    assert "virtuoso_ui" in guidance
    assert "workflow 不要填 layout" in guidance
    assert "maskLayout" in guidance and "inspect_layout" in guidance


def test_mcp_startup_is_bounded_profile_aware_and_fail_closed():
    class Client:
        def __init__(self):
            self.calls = []
            self.data = entry()

        def call(self, method, args):
            self.calls.append((method, args))
            return True, {"value": json.dumps(self.data)}

    client = Client()
    result = McpServer(client).handle({"id": 1, "method": "initialize"})["result"]
    assert client.calls == [("eval_skill_native", {"code": "aiEntryContext(nil 8 nil)"})]
    assert REF in result["instructions"]
    assert "不可信的 JSON" in result["instructions"]
    client.calls.clear()
    McpServer(client, profile="verification").handle({"id": 2, "method": "initialize"})
    assert client.calls == []
    assert "get_entry_context" not in assistant_instructions("verification")
    client.data = {"ok": False, "code": "source_not_captured"}
    assert not call_context("get_entry_context", {}, client)[0]
    result = McpServer(client).handle({"id": 3, "method": "initialize"})["result"]
    assert "source_not_captured" in result["instructions"]


def test_startup_uses_short_transport_timeout():
    from cadai.entry_context import startup_context

    class Client:
        def call_bounded(self, method, arguments, timeout):
            assert method == "eval_skill_native" and timeout == 5
            assert arguments == {"code": "aiEntryContext(nil 8 nil)"}
            raise TimeoutError("busy editor")

    assert "startup_context_unavailable" in startup_context(Client())


def test_missing_source_keeps_window_discovery_available():
    calls = []

    class Broker:
        def read(self, context, method, args):
            calls.append((method, args))
            if method == "get_entry_context" and not args.get("window_ref"):
                raise QueryUnavailable("source_not_captured", "The task source was not captured")
            if method == "list_windows":
                return {"ok": True, "valid": True, "schema": "cad.windows.v1",
                        "session_ref": "fixture", "windows": [entry()["source"]]}
            return entry()

    tools = context_tools(Broker())
    bound = BoundContext("i", "g", "bound", {"window_ref": ""})
    missing = tools.execute(ToolCall("missing", "get_entry_context", {}), bound)
    assert missing.status == "ok"
    assert missing.data["captured"] is False
    assert missing.data["next_action"] == "use_get_project_context_or_inspect_explicit_target"
    assert "重新加载" not in missing.summary
    listed = tools.execute(ToolCall("windows", "list_windows", {}), bound)
    assert listed.status == "ok"
    ref = listed.data["windows"][0]["window_ref"]
    explicit = tools.execute(ToolCall("explicit", "get_entry_context", {"window_ref": ref}), bound)
    assert explicit.status == "ok" and explicit.data["source"]["window_ref"] == REF
    assert calls == [("get_entry_context", {}), ("list_windows", {}),
                     ("get_entry_context", {"window_ref": REF})]


def test_studio_refreshes_each_task_and_pairs_stale_reply(tmp_path):
    observed = []

    class Broker:
        def read(self, context, method, args):
            observed.append((context, method, args))
            if context.target_id == "stale":
                raise NeedsReconcile("source closed")
            return entry()

    bound = BoundContext("i", "g", "first", {"capabilities": ["get_entry_context"]})
    with SessionJournal(tmp_path, "entry") as journal:
        provider = ScriptedProvider([[ModelTurn("done")]])
        loop = PythonBackend(provider, context_tools(Broker()), journal, bound)
        list(initial_context(loop, lambda: False, context=bound, input_id="startup"))
        assert not provider.requests
        list(loop.run("read my circuit", context=bound, input_id="first"))
        assert len(observed) == 2 and len(provider.requests) == 1
        stale = BoundContext("i", "g", "stale", bound.snapshot)
        list(loop.run("another source", context=stale, input_id="second"))
        assert loop.state.task["status"] == "needs_reconcile"
        assert loop.state.task["pending_calls"] == []
        assert loop.state.messages[-1]["role"] == "tool"
        assert len(provider.requests) == 1


def test_shared_subprocess_relay_tokens_and_read_only_boundary(tmp_path):
    executable = Path(__file__).resolve().parents[3] / "sico/python/sico-entry"
    with ContextBroker(5) as broker:
        config = tmp_path / "connection.json"
        config.write_text(
            json.dumps(
                {"host": broker.address[0], "port": broker.address[1], "token": broker.token}
            )
        )
        config.chmod(0o600)
        proc = subprocess.Popen(
            [sys.executable, "-s", str(executable), "relay", "--connection-file", str(config)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "SICO_PYTHON": sys.executable},
        )
        try:
            bound = BoundContext("i", "g", "bound", {"capabilities": [t["name"] for t in TOOLS]})
            proc.stdin.write((json.dumps({"context": bound.record()}) + "\n").encode())
            proc.stdin.flush()
            broker.context("i")
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    broker.read,
                    bound,
                    "get_entry_context",
                    {"window_ref": REF, "include_base_libraries": True},
                )
                tokens = proc.stdout.readline().decode().strip().split()
                assert tokens[1:] == ["get_entry_context", "bound", REF, "20", "0", "1"]
                proc.stdin.write(
                    (json.dumps({"id": tokens[0], "ok": True, "result": entry()}) + "\n").encode()
                )
                proc.stdin.flush()
                assert enrich(future.result(timeout=5))["source"]["window_ref"] == REF
            with pytest.raises(ValueError):
                broker.read(bound, "eval_skill", {"code": "exit()"})
            with pytest.raises(ValueError):
                broker.read(bound, "get_entry_context", [])
            broker.close()
            proc.stdin.close()
            assert proc.wait(timeout=6) == 0, proc.stderr.read().decode()
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
