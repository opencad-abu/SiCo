"""An accepted Ask launches a reusable desktop without reopening its backend."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from agent_service_business_fixtures import business_service
from agent_service_launch_fixtures import eventually
from sico.transport.bridge_client import BridgeClient
from test_agent_desktop import read_line
from test_agent_service_host import host_bridge, submit, target

ENTRY = Path(__file__).absolute().parents[3] / "sico/python/sico-entry"


@pytest.mark.parametrize("view", ["schematic", "layout", "maestro"])
def test_first_ask_launch_attaches_the_accepted_runtime_and_reuses_its_pipe(
    tmp_path, monkeypatch, view,
):
    data = tmp_path / "data"
    data.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(data, target_is_directory=True)
    environment = {"SICO_PDK_DATA": str(alias)}
    context = target(tmp_path, "first", view=view)
    with host_bridge(tmp_path, context) as (_, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
        first = submit(service, client, context, environment=environment)
        assert first.outcome == "accepted"
        address = first.address
        owner = service.owner.controllers[address.session.session_id]
        assert owner.wait(5)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("SICO_", "CAD_"))}
        # Attachment must use the already accepted backend, even if a second
        # provider resolution in the desktop would now reject its environment.
        env.update(SICO_PYTHON=sys.executable, QT_QPA_PLATFORM="offscreen",
                   SICO_BACKEND="python", SICO_MODEL="incomplete-unused-config")
        process = subprocess.Popen(
            [sys.executable, "-s", str(ENTRY), "launch", "--launch-dir", str(tmp_path),
             "--session", address.session.session_id, "--service-id", address.service_id,
             "--runtime-id", address.session.runtime_id],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, bufsize=0,
        )

        def acquired(controller):
            return [row["payload"] for row in controller.journal.events()
                    if row["kind"] == "session.control" and row["payload"]["action"] == "acquire"]

        try:
            process.stdin.write((json.dumps(dict(context=context.record())) + "\n").encode())
            assert read_line(process) == ("@shown " + address.session.session_id + "\n").encode()
            eventually(lambda: acquired(owner))
            assert service.owner.controllers[address.session.session_id] is owner
            assert owner.runtime_id == address.session.runtime_id
            assert len(service.owner.controllers) == 1
            # A second view of the same design retains the sole owner.
            related_context = target(tmp_path, "related", view="symbol")
            related = submit(service, client, related_context, environment=environment)
            assert related.address == address
            assert owner.wait(5)
            process.stdin.write(("@attach " + address.session.session_id + "\n").encode())
            # An unrelated target becomes a different page in this same desktop.
            other_context = target(tmp_path, "other", cell="Other", view=view)
            other = submit(service, client, other_context, environment=environment)
            assert other.address != address and other.outcome == "accepted"
            other_owner = service.owner.controllers[other.address.session.session_id]
            assert other_owner.wait(5)
            process.stdin.write(("@attach " + other.address.session.session_id + "\n").encode())
            eventually(lambda: acquired(other_owner))
            assert acquired(other_owner)[0]["client_id"] == acquired(owner)[0]["client_id"]
            assert len(service.owner.controllers) == 2 and process.poll() is None
            assert [row["payload"]["context"] for row in owner.journal.events()
                    if row["kind"] == "task.started"] == [
                        context.record(), related_context.record()]
            assert [row["payload"]["context"] for row in other_owner.journal.events()
                    if row["kind"] == "task.started"] == [other_context.record()]
        finally:
            if process.poll() is None:
                process.stdin.write(b"@shutdown\n")
            try:
                code = process.wait(8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
                raise
        assert code == 0, process.stderr.read().decode()
        assert not owner.closing and not other_owner.closing
        log = tmp_path / ".sico/ai/agent/runtime" / address.session.session_id / "desktop.log"
        assert "Traceback" not in log.read_text()
