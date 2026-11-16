# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent transport contract relay process contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from agent_transport_contract_support import context

from sico.core.contracts import NeedsReconcile
from sico.transport.broker import ContextBroker


def test_subprocess_relay_inline_response_and_parent_eof(tmp_path):
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
            env={**os.environ, "SICO_PYTHON": sys.executable},
        )
        try:
            bound = context()
            process.stdin.write((json.dumps({"context": bound.record()}) + "\n").encode())
            process.stdin.flush()
            broker.context("i")

            def skill():
                request_id = process.stdout.readline().decode().strip()
                assert len(request_id) == 32
                result = {
                    "id": request_id,
                    "ok": True,
                    "result": {"valid": True, "中文": "直接回传"},
                }
                process.stdin.write((json.dumps(result) + "\n").encode())
                process.stdin.flush()

            thread = threading.Thread(target=skill)
            thread.start()
            assert broker.get_context(bound)["中文"] == "直接回传"
            thread.join(3)
            assert not thread.is_alive()
            broker.close()
            process.stdin.close()
            assert process.wait(timeout=6) == 0, process.stderr.read().decode()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_subprocess_relay_quarantines_timed_out_skill_request(tmp_path):
    entry = Path(__file__).resolve().parents[3] / "sico/python/sico-entry"
    with ContextBroker(5, operation_timeout=2) as broker:
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
            env={
                **os.environ,
                "SICO_PYTHON": sys.executable,
                "SICO_SKILL_TIMEOUT": "1",
            },
        )
        try:
            bound = context()
            process.stdin.write((json.dumps({"context": bound.record()}) + "\n").encode())
            process.stdin.flush()
            broker.context("i")

            first = {}

            def first_call():
                with pytest.raises(NeedsReconcile) as raised:
                    broker.get_context(bound)
                first["error"] = str(raised.value)

            call = threading.Thread(target=first_call)
            call.start()
            request_line = process.stdout.readline().decode().strip().split(" ")
            assert len(request_line[0]) == 32 and len(request_line) == 1
            call.join(3)
            assert not call.is_alive() and "skill_timeout" in first["error"]

            # A newer request is rejected while quarantine is active and is
            # never written to the SKILL stream.
            pending = {}

            def pending_call():
                with pytest.raises(NeedsReconcile) as raised:
                    broker.get_context(bound)
                pending["error"] = str(raised.value)

            call = threading.Thread(target=pending_call)
            call.start()
            call.join(3)
            assert not call.is_alive() and "skill_response_pending" in pending["error"]

            # The late response clears quarantine; it is never delivered as
            # the result of a newer broker request.
            late = {"id": request_line[0], "ok": True, "result": {"valid": True}}
            process.stdin.write((json.dumps(late) + "\n").encode())
            process.stdin.flush()
            time.sleep(0.25)

            second = {}

            def second_call():
                second["result"] = broker.get_context(bound)

            call = threading.Thread(target=second_call)
            call.start()
            request_line = process.stdout.readline().decode().strip().split(" ")
            assert len(request_line[0]) == 32 and len(request_line) == 1
            response = {"id": request_line[0], "ok": True, "result": {"valid": True}}
            process.stdin.write((json.dumps(response) + "\n").encode())
            process.stdin.flush()
            call.join(3)
            assert not call.is_alive() and second["result"]["valid"] is True
            process.stdin.close()
            assert process.wait(timeout=5) == 0
            stderr = process.stderr.read().decode()
            assert "Traceback" not in stderr
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
