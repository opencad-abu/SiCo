"""Copilot template capture respects the native artifact ownership contract."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_agent_assistant_tools import bound
from test_template_transport import AI, REFERENCE_CDS

from sico.adapters.assistant_tools import _AssistantClient
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import ToolCall
from sico.storage.journal import SessionJournal


def capture_path(code):
    match = re.search(r'"([^"\n]*template-data-[0-9a-f]+\.jsonl)"', code)
    assert match, code
    return Path(match.group(1))


def test_capture_leaves_path_creation_to_native_writer_and_secures_artifact(tmp_path):
    class Broker:
        def assistant_call(self, context, method, code):
            path = capture_path(code)
            assert not path.exists(), "native aiTplCapture refuses an existing path"
            path.write_bytes(b"capture\n")
            path.chmod(0o664)
            return {"ok": True, "value": json.dumps({"ok": True, "artifact_path": str(path)}),
                    "output": "capture diagnostic\n", "output_truncated": False,
                    "output_capture": "skill_ports"}

    client = _AssistantClient(Broker(), bound(tmp_path), tmp_path, tmp_path / "spool")
    for _ in range(2):
        ok, detail = client.call("capture_circuit_template", {"library": "work", "cell": "top"})
        assert ok, detail
        assert detail["output"] == "capture diagnostic\n"
        path = Path(detail["artifact"]["path"])
        assert path.read_bytes() == b"capture\n"
        assert path.stat().st_mode & 0o777 == 0o600
        path.unlink()
    assert not list(client.runtime.spool.iterdir())


@pytest.mark.parametrize("failure", ["native", "metadata", "missing", "symlink"])
def test_failed_capture_does_not_leave_partial_artifacts(tmp_path, failure):
    outside = tmp_path / "outside"
    outside.write_text("preserve")

    class Broker:
        def assistant_call(self, context, method, code):
            path = capture_path(code)
            assert not path.exists()
            if failure == "symlink":
                path.symlink_to(outside)
            elif failure != "missing":
                path.write_text("partial")
            if failure == "native":
                return {"ok": False, "code": "skill_error", "message": "capture failed"}
            return {"ok": True, "value": ("invalid json" if failure == "metadata" else
                    json.dumps({"ok": True, "artifact_path": str(path)}))}

    client = _AssistantClient(Broker(), bound(tmp_path), tmp_path, tmp_path / "spool")
    ok, detail = client.call("capture_circuit_template", {"library": "work", "cell": "top"})
    assert not ok, detail
    assert not list(client.runtime.spool.iterdir())
    assert outside.read_text() == "preserve"


@pytest.mark.skipif(not REFERENCE_CDS.is_file(), reason="reference library unavailable")
def test_copilot_template_extraction_dbaccess_round_trip(tmp_path, monkeypatch):
    executable = shutil.which("dbAccess")
    if not executable:
        pytest.skip("dbAccess unavailable")
    (tmp_path / "cds.lib").write_text("INCLUDE " + str(REFERENCE_CDS) + "\n")
    monkeypatch.delenv("SICO_CIRCUIT_TEMPLATES_DIR", raising=False)

    class Broker:
        def assistant_call(self, context, method, code):
            assert method == "eval_skill"
            path = capture_path(code)
            assert not path.exists()
            script = "\n".join(
                "load(" + json.dumps(str(AI / "skill" / name)) + ")"
                for name in ("AI_protocol.il", "AI_eval.il", "AI_circuit_create_check.il",
                             "AI_template_capture_geometry.il", "AI_template_native_content.il",
                             "AI_templates.il")
            )
            script += "\nprintf(\"CAPTURE_RESULT %s\\n\" " + code + ")\nexit()\n"
            result = subprocess.run([executable], input=script, text=True,
                                    capture_output=True, cwd=tmp_path, timeout=45)
            with (tmp_path / "capture.log").open("a") as log:
                log.write(result.stdout + result.stderr)
            assert result.returncode == 0, result.stderr
            rows = [line.removeprefix("CAPTURE_RESULT ") for line in result.stdout.splitlines()
                    if line.startswith("CAPTURE_RESULT ")]
            assert len(rows) == 1, result.stdout + result.stderr
            return {"ok": True, "value": rows[0]}

    with SessionJournal(tmp_path, "template-native") as journal:
        registry = context_tools(Broker(), journal)
        refs = []
        for attempt in range(2):
            result = registry.execute(ToolCall("capture" + str(attempt), "extract_circuit_templates",
                                               {"library": "gsclib045", "cell": "INVX1"}),
                                      bound(tmp_path))
            assert result.status == "ok", result
            assert result.data["stage"] == "published"
            assert result.data["counts"]["devices"] == 2
            assert result.data["assets"] == ["layout", "schematic", "symbol"]
            refs.append(result.data["template_ref"])
        assert refs[0] == refs[1]
