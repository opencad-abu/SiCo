"""ADE tools use fixed target reads, preserve recoverable errors, and never send source."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext, NeedsReconcile, ToolCall
from sico.transport.broker import ContextBroker
from sico.transport.methods import QueryUnavailable


def bound(**snapshot):
    return BoundContext(
        "ade-instance",
        "ade-generation",
        "bound",
        {
            "valid": True,
            "ade_session": "session1",
            "capabilities": ["get_context", "read_ade_setup", "read_ade_history"],
            **snapshot,
        },
    )


def test_ade_tools_reject_arguments_and_non_ade_before_dispatch():
    class Broker:
        def read(self, *args):
            pytest.fail("Unexpected dispatch")

    tools = context_tools(Broker())
    for method in ("read_ade_setup", "read_ade_history"):
        assert (
            tools.execute(ToolCall("id", method, {"session": "other"}), bound()).status
            == "invalid_arguments"
        )
        unavailable = tools.execute(ToolCall("id", method, {}), bound(ade_session=""))
        assert unavailable.status == "unavailable"
        assert unavailable.data["code"] == "ade_binding_required"
        assert unavailable.data["next_action"] == "open_assistant_from_ade"


def test_old_relay_capabilities_require_reopen_without_dispatch():
    with ContextBroker() as broker:
        with pytest.raises(QueryUnavailable, match="重新加载"):
            broker.read(bound(capabilities=["get_context"]), "read_ade_setup")
        with pytest.raises(ValueError, match="Unsupported"):
            broker.read(bound(), "eval_skill")


def test_real_relay_routes_ade_methods_and_keeps_connection_after_read_error(tmp_path):
    entry = Path(__file__).resolve().parents[3] / "sico/python/sico-entry"
    with ContextBroker(3) as broker:
        config = tmp_path / "connection.json"
        config.write_text(
            json.dumps(
                {"host": broker.address[0], "port": broker.address[1], "token": broker.token}
            )
        )
        config.chmod(0o600)
        process = subprocess.Popen(
            [sys.executable, "-s", str(entry), "relay", "--connection-file", str(config)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "CAD_PYTHON": sys.executable},
        )
        target = bound(test="nominal")
        seen, errors = [], []

        def skill():
            try:
                for reply in (
                    {
                        "ok": True,
                        "result": {
                            "valid": True,
                            "contract": "ade_setup.v1",
                            "bound_test": "nominal",
                        },
                    },
                    {"ok": False, "code": "read_failed"},
                    {"ok": True, "result": {"valid": True}},
                    {"ok": False, "code": "stale_context"},
                ):
                    line = process.stdout.readline().decode().strip().split(" ")
                    assert len(line[0]) == 32
                    seen.append(line[1] if len(line) == 2 else "get_context")
                    reply["id"] = line[0]
                    process.stdin.write((json.dumps(reply) + "\n").encode())
                    process.stdin.flush()
            except BaseException as exc:
                errors.append(exc)

        try:
            process.stdin.write((json.dumps({"context": target.record()}) + "\n").encode())
            process.stdin.flush()
            broker.context(target.instance_id)
            thread = threading.Thread(target=skill)
            thread.start()
            tools = context_tools(broker)
            assert (
                tools.execute(ToolCall("a", "read_ade_setup", {}), target).data["bound_test"]
                == "nominal"
            )
            assert (
                tools.execute(ToolCall("b", "read_ade_history", {}), target).status == "read_failed"
            )
            assert broker.get_context(target)["valid"]
            with pytest.raises(NeedsReconcile):
                tools.execute(ToolCall("c", "read_ade_setup", {}), target)
            thread.join(3)
            assert not thread.is_alive() and not errors
            assert seen == ["read_ade_setup", "read_ade_history", "get_context", "read_ade_setup"]
            process.stdin.close()
            assert process.wait(timeout=5) == 0, process.stderr.read().decode()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
