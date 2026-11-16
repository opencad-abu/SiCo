"""Explicit source-only EDA circuit-error probe; never part of ordinary regressions."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_RUN_SKILL_SOURCE_PROBES") != "1",
    reason="Explicit source-only EDA acceptance required",
)


def test_native_skill_failure_stages(tmp_path):
    from sico.transport.circuit import encode_value

    binary = shutil.which("dbAccess")
    if not binary:
        pytest.skip("dbAccess unavailable")
    root = Path(__file__).resolve().parents[2]
    lines = [
        f"load({json.dumps(str(root / 'skill' / name))})"
        for name in [
            "AI_protocol.il",
            "AI_eval.il",
            "AI_circuit.il",
            "AI_copilot_literals.il",
            "AI_copilot_targets.il", "AI_copilot_circuit.il",
        ]
    ]
    lines += [
        f'ddCreateLib("Work" {json.dumps(str(tmp_path / "Work"))})',
        """
testSource=makeTable("source" nil)
testSource['projectValid]=t
procedure(sicoSessionTarget(cid target) testSource)
procedure(sicoProjectContextJson(source) "{}")
procedure(aiEntryProjectName(name) stringp(name))
envSetVal("asimenv.startup" "projectDir" 'string "~/simulation")
aiCircuitTasks=makeTable("tasks" nil)
aiCircuitTasks["task:test"]="background"
aiCopilotTasks["task:test"]=testSource
aiSimulationSetups=makeTable("setups" nil)
testSetup=makeTable("setup" nil)
testSetup['task]="task:test"
testSetup['payload]=list(list("Work" "Tb" "maestro"))
aiSimulationSetups["simulation:test"]=testSetup
""",
    ]

    def probe(name, function, values, claim=None, expected=None):
        fields = [name, "circuit_call", "target", function, encode_value([values, claim, expected])]
        lines.append(
            "aiWriteTextFile("
            + json.dumps(name + ".json")
            + " sicoCircuitReply(nil list("
            + " ".join(json.dumps(field) for field in fields)
            + ")))"
        )

    probe("project_home", "aiCopilotDescribe",
          ["run_simulation_setup", "simulation:test", None, None])
    lines.append('envSetVal("asimenv.startup" "projectDir" \'string "relative/simulation")')
    probe(
        "project_dir", "aiCopilotDescribe", ["run_simulation_setup", "simulation:test", None, None]
    )
    lines.append('aiCopilotTasks["task:test"]=nil')
    probe(
        "task_stale", "aiCopilotDescribe", ["run_simulation_setup", "simulation:test", None, None]
    )
    lines.append("testSource['projectValid]=nil")
    probe(
        "source_stale", "aiCopilotDescribe", ["run_simulation_setup", "simulation:test", None, None]
    )
    lines += [
        """
testSource['projectValid]=t
procedure(aiCreateInspect(ref) error("read probe failed"))
"""
    ]
    probe("read_failed", "aiCreateInspect", ["created:test"])
    lines += [
        """
procedure(aiCopilotFinal(fn values expected claim) t)
procedure(aiProjectAssertClaim(@rest args) t)
aiCopilotClaims["claim:test"]=testSource
procedure(aiCreateExecute(@rest args) error("write probe failed"))
"""
    ]
    probe("write_unknown", "aiCreateExecute", ["p", "d", "r"], ["s", "claim:test", "d"], [])
    lines.append('procedure(aiCopilotFinal(fn values expected claim) error("target not writable"))')
    probe("final_rejected", "aiCreateExecute", ["p", "d", "r"], ["s", "claim:test", "d"], [])
    lines.append("procedure(aiCopilotFinal(fn values expected claim) t)")
    lines += [
        """
testLarge="x"
for(testIndex 1 18 testLarge=strcat(testLarge testLarge))
procedure(aiCreateInspect(ref) testLarge)
procedure(aiCreateExecute(@rest args) testLarge)
"""
    ]
    probe("read_large", "aiCreateInspect", ["created:test"])
    probe("write_large", "aiCreateExecute", ["p", "d", "r"], ["s", "claim:test", "d"], [])
    lines.append('procedure(aiCreateInspect(ref) error("%s" testLarge))')
    probe("error_large", "aiCreateInspect", ["created:test"])
    lines.append("procedure(sicoSessionTarget(cid target) nil)")
    probe("missing_target", "aiCreateInspect", ["created:test"])
    lines.append('procedure(aiCopilotListLibraries(all) error("library query failed"))')
    probe("sourceless_failed", "aiCopilotListLibraries", [True])
    lines.append("""aiWriteTextFile("decode_failed.json" sicoCircuitReply(nil
      list("decode_failed" "circuit_call" "target" "aiCopilotListLibraries" "szz")))""")
    lines += ['aiWriteTextFile("passed" "yes")', "exit()"]
    script = tmp_path / "errors.il"
    script.write_text("\n".join(lines))
    completed = subprocess.run(
        [binary, "-nocdsinit", "-load", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert (tmp_path / "passed").exists(), completed.stdout + completed.stderr
    home = json.loads((tmp_path / "project_home.json").read_text())
    assert home["ok"] is True
    assert home["result"]["circuit"]["binding"][6] == str(Path.home() / "simulation")
    expected = {
        "project_dir": ("simulation_project_dir_invalid", "describe", False),
        "task_stale": ("task_source_mismatch", "describe", False),
        "source_stale": ("circuit_context_stale", "source_check", False),
        "read_failed": ("circuit_read_failed", "invoke", False),
        "write_unknown": ("circuit_outcome_unknown", "invoke", True),
        "final_rejected": ("circuit_preflight_failed", "final_check", False),
        "read_large": ("circuit_result_too_large", "encode_reply", False),
        "write_large": ("circuit_result_too_large", "encode_reply", True),
        "error_large": ("circuit_read_failed", "invoke", False),
        "missing_target": ("circuit_requires_target", "resolve_source", False),
        "sourceless_failed": ("circuit_read_failed", "invoke", False),
        "decode_failed": ("circuit_invalid_call", "decode_call", False),
    }
    for name, (code, stage, dispatched) in expected.items():
        reply = json.loads((tmp_path / (name + ".json")).read_text())
        assert reply["ok"] is False and reply["code"] == code, reply
        assert reply["diagnostic"]["stage"] == stage
        assert reply["diagnostic"]["operation_dispatched"] is dispatched
        assert reply["diagnostic"]["raw_error"] and reply["diagnostic"]["request_id"] == name
    project_dir = json.loads((tmp_path / "project_dir.json").read_text())
    assert "relative/simulation" in project_dir["diagnostic"]["raw_error"]
    assert "relative/simulation" in project_dir["diagnostic"]["output"]
    assert project_dir["diagnostic"]["output_capture"] == "skill_ports"
    error_large = json.loads((tmp_path / "error_large.json").read_text())
    assert error_large["diagnostic"]["raw_error_truncated"]
    assert len(error_large["diagnostic"]["raw_error"]) == 2048
