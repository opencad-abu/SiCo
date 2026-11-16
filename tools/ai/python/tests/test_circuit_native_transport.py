"""Native expression path preserves output-port lifetime for asynchronous ADE."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from cadai.controller import RequestDispatcher
from cadai.mcp import McpServer
from test_controller import FakeTransport, runtime
from test_circuit_tools import Client, call


def test_native_transport_uses_same_bounded_private_spool(runtime, tmp_path):
    transport = FakeTransport({"ok": True, "value": "nil", "output": "", "output_capture": "ciw"})
    dispatcher = RequestDispatcher(transport, runtime, tmp_path)
    result = dispatcher("a"*32, "eval_skill_native", {"code": "aiRcStatus(\"rc-run:one\")"})
    assert transport.requests[0]["method"] == "eval_skill_native"
    assert transport.sources == [b'aiRcStatus("rc-run:one")']
    assert result["output_capture"] == "ciw"
    assert not Path(transport.requests[0]["source_path"]).exists()


def test_existing_maestro_workflow_also_uses_native_port():
    client = Client()
    call(McpServer(client), "run_maestro_simulation", {"library": "work", "cell": "tb", "view": "maestro"})
    assert client.calls == [("eval_skill_native", {"code": 'aiMaestroRun("work" "tb" "maestro" "single")'})]


def test_native_preserves_port_and_protocol_roundtrip(tmp_path):
    executable = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if not executable:
        pytest.skip("dbAccess unavailable")
    root = Path(__file__).resolve().parents[2] / "skill"
    (tmp_path / "native.il").write_text('progn(aiDelayedPort=poport printf("NATIVE_DIRECT_OUTPUT\\n") nil)')
    (tmp_path / "legacy.il").write_text('printf("LEGACY_CAPTURE_OUTPUT\\n")')
    request = json.dumps({"id": "a"*32, "method": "eval_skill_native",
                          "source_path": "native.il", "output_path": "native.json"})
    lines = [f'load({json.dumps(str(root/n))})' for n in ("AI_protocol.il", "AI_eval.il")]
    lines += ['nativeOuterPort=poport', 'aiEvalNativeExpression("native.il" "native.json")',
              'unless(aiDelayedPort==nativeOuterPort error("native evaluator replaced output port"))',
              'fprintf(aiDelayedPort "DELAYED_OUTPUT_PORT_STILL_OPEN\\n")',
              'aiEvalExpression("legacy.il" "legacy.json")',
              f'unless(car(aiJsonParseRequest({json.dumps(request)})) error("native method rejected"))',
              'aiEvalNativeExpression("missing.il" "missing.json")', 'exit()']
    process = subprocess.run([executable], input="\n".join(lines), text=True,
                             capture_output=True, cwd=tmp_path, timeout=30)
    assert process.returncode == 0 and "*Error*" not in process.stdout, process.stdout
    assert "DELAYED_OUTPUT_PORT_STILL_OPEN" in process.stdout
    native = json.loads((tmp_path/"native.json").read_text())
    assert native["ok"] is True and native["value"] == "nil"
    assert native["output_capture"] == "ciw" and native["output"] == ""
    legacy = json.loads((tmp_path/"legacy.json").read_text())
    assert legacy["output_capture"] == "skill_ports" and "LEGACY_CAPTURE_OUTPUT" in legacy["output"]
    assert json.loads((tmp_path/"missing.json").read_text())["ok"] is False
