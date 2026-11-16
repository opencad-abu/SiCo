"""Exercise MCP -> controller -> protocol -> SKILL -> persistent catalog."""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from cadai.controller import RequestDispatcher
from cadai.launch import build_claude_mcp_config, build_codex_command
from cadai.mcp import McpServer
from cadai.runtime import RuntimePaths
from cadai.template_catalog import TemplateCatalog
from cadai.template_schema import TOOL_NAMES

AI = Path(__file__).resolve().parents[2]
REFERENCE_CDS = Path(os.environ.get("SICO_AI_TEST_TEMPLATE_CDS_LIB",
                    AI.parent / ".sico/circuit-workflow-study/template-capture/cds.lib"))


def test_template_launch_and_profile_visibility(tmp_path):
    env = {"SICO_CIRCUIT_TEMPLATES_DIR": str(tmp_path)}
    command = build_codex_command(
        "/bin/codex", "/bin/python3", tmp_path, AI / "python", 300, environment=env
    )
    assert any("SICO_CIRCUIT_TEMPLATES_DIR" in arg for arg in command)
    runtime = RuntimePaths(tmp_path, tmp_path / "socket", tmp_path / "spool")
    config = json.loads(
        build_claude_mcp_config(
            "/bin/python3", AI / "python", runtime, "test-token", 300, environment=env
        )
    )
    assert config["mcpServers"]["sico_ic"]["env"]["SICO_CIRCUIT_TEMPLATES_DIR"] == str(tmp_path)

    class Client:
        def close(self):
            pass

    server = McpServer(Client(), workspace=tmp_path, profile="verification")
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert not TOOL_NAMES & {t["name"] for t in listed["result"]["tools"]}
    for name in TOOL_NAMES:
        response = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            }
        )
        assert "error" in response
    assert build_codex_command(
        "/bin/codex", "/bin/python3", tmp_path, AI / "python", 300,
        environment={"SICO_CIRCUIT_TEMPLATES_DIR": "relative"})



@pytest.mark.skipif(
    not REFERENCE_CDS.is_file(), reason="local read-only reference library is unavailable"
)
def test_template_mcp_controller_dbaccess_round_trip(tmp_path, monkeypatch):
    executable = shutil.which("dbAccess")
    if not executable:
        pytest.skip("dbAccess unavailable")
    (tmp_path / "cds.lib").write_text("INCLUDE " + str(REFERENCE_CDS) + "\n")
    spool = tmp_path / "spool"
    spool.mkdir(mode=0o700)
    runtime = RuntimePaths(tmp_path, tmp_path / "socket", spool)
    seen = []

    class DbTransport:
        def request(self, request, timeout):
            seen.append(request.copy())
            # The process transport changes; parsing, execution, capture, and
            # controller publication use production code without mocks.
            script = "\n".join(
                "load(" + json.dumps(str(AI / "skill" / name)) + ")"
                for name in ["AI_protocol.il", "AI_eval.il", "AI_ipc.il", "AI_templates.il"]
            )
            script += (
                "\nprocedure(aiIpcRespond(cid id ok output message) "
                'printf("CAPTURE_RESPONSE %L\\n" ok))\n'
            )
            script += "parsed=aiJsonParseRequest(" + json.dumps(json.dumps(request)) + ")\n"
            script += (
                "if(car(parsed) then aiIpcExecute(nil cadr(parsed)) "
                'else error("Invalid protocol request"))\nexit()\n'
            )
            completed = subprocess.run(
                [executable], input=script, text=True, capture_output=True, cwd=tmp_path, timeout=30
            )
            (tmp_path / "dbaccess.log").write_text(completed.stdout + completed.stderr)
            assert completed.returncode == 0 and "CAPTURE_RESPONSE t" in completed.stdout, (
                completed.stdout + completed.stderr
            )
            return True, {"result_path": request["output_path"]}

    dispatcher = RequestDispatcher(DbTransport(), runtime, tmp_path)

    class Client:
        def call(self, method, args):
            return True, dispatcher(uuid.uuid4().hex, method, args)

        def close(self):
            pass

    client = Client()
    client.runtime = runtime
    monkeypatch.delenv("SICO_CIRCUIT_TEMPLATES_DIR", raising=False)
    server = McpServer(client, workspace=tmp_path)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "extract_circuit_templates",
            "arguments": {"library": "gsclib045", "cell": "INVX1"},
        },
    }
    result = server.handle(request)
    assert result["result"]["isError"] is False, result
    payload = json.loads(result["result"]["content"][0]["text"])
    assert payload["stage"] == "stored_private"
    assert payload["counts"]["devices"] == 2
    assert payload["assets"] == ["layout", "schematic", "symbol"]
    assert payload["coverage"]["layout"]["binding_status"] == "binding_unavailable"
    assert payload["coverage"]["symbol"]["interface_status"] == "different"
    for req in seen:
        assert Path(req["source_path"]).parent == spool and not Path(req["source_path"]).exists()
    for p in spool.iterdir():
        p.unlink()
    catalog = TemplateCatalog(tmp_path / ".sico/ai/circuit_templates")
    assert catalog.get(payload["template_ref"])["assets"]["symbol"]["ports"]
