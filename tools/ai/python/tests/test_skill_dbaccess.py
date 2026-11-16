from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]


def _skill_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _result(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_ai_skill_dbaccess_round_trip(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    expression = tmp_path / "expression.il"
    expression.write_text(
        'progn(printf("AI_CAPTURE_OK\\n") '
        'warn("AI_CAPTURE_WARNING\\n") '
        'fprintf(errport "AI_CAPTURE_ERROR_PORT\\n") nil)',
        encoding="utf-8",
    )
    failing = tmp_path / "failing.il"
    failing.write_text('error("AI_EXPECTED_ERROR")', encoding="utf-8")
    loaded = tmp_path / "loaded.ils"
    loaded.write_text('procedure(aiProbeLoaded() "AI_LOAD_OK")\nnil\n', encoding="utf-8")
    large_loaded = tmp_path / "large-output.ils"
    large_loaded.write_text('printf("' + ("x" * 70_000) + '")\nnil\n', encoding="utf-8")
    oversized = tmp_path / "oversized.il"
    oversized.write_text(";" * 65_537, encoding="utf-8")
    production_python_root = (AI_ROOT.parents[1] / "runtime/python").resolve()
    production_python = production_python_root / "bin" / "python3"
    if not production_python.is_file() or not os.access(production_python, os.X_OK):
        pytest.skip("fixed CAD Python 3.9.13 is unavailable")
    configured_python = tmp_path / "configured Python's executable"
    configured_python.symlink_to(production_python)
    unrelated_python_root = tmp_path / "unrelated-python-root"
    missing_python = tmp_path / "missing-python"
    missing_entry = tmp_path / "missing-entry"
    python_entry = AI_ROOT / "python/sico-ai"

    eval_result = tmp_path / "eval.json"
    error_result = tmp_path / "error.json"
    load_result = tmp_path / "load.json"
    large_result = tmp_path / "large.json"
    oversized_result = tmp_path / "oversized.json"
    context_result = tmp_path / "context.json"
    libraries_result = tmp_path / "libraries.json"
    missing_library_result = tmp_path / "missing-library.json"
    named_layout_result = tmp_path / "named-layout.json"
    forced_error_result = tmp_path / "forced-error.json"
    close_result = tmp_path / "close.json"
    snapshot_result = tmp_path / "snapshot.jsonl"
    inspection_lib = "SicoTest"
    inspection_cell = "emptyLayout"
    snapshot_cell = "snapshotTop"
    snapshot_leaf = "snapshotLeaf"
    inspection_root = tmp_path / "SicoTest"
    request = json.dumps(
        {
            "id": "0123456789abcdef0123456789abcdef",
            "method": "eval_skill",
            "source_path": str(expression),
            "output_path": str(eval_result),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    sources = [
        AI_ROOT / "skill/AI_protocol.il",
        AI_ROOT / "skill/AI_eval.il",
        AI_ROOT / "skill/AI_ipc.il",
        AI_ROOT / "skill/AI_launcher.il",
        AI_ROOT / "skill/AI_live_model.il",
        AI_ROOT / "skill/AI_inspect_values.il",
        AI_ROOT / "skill/AI_inspect_records.il",
        AI_ROOT / "skill/AI_inspect_snapshot.il",
        AI_ROOT / "skill/AI_inspect.il",
        AI_ROOT / "skill/AI_flow_command.il",
        AI_ROOT / "skill/AI_flows.il",
        AI_ROOT / "skill/AI_maestro.il",
        AI_ROOT / "skill/AI_maestro_validate.il",
    ]
    loader = AI_ROOT / "skill++/AI.ils"
    lines = [
        f"procedure(witMenuToolsRoot() {_skill_string(AI_ROOT.parent)})",
        'setShellEnvVar("CAD_HOME" "/not/the/cad/home")',
        f"load({_skill_string(loader)})",
        f"if(and(aiResolveRoot()=={_skill_string(AI_ROOT)} "
            'aiInspectRevision()=="20261101.ai.inspect.v12" '
        'aiWorkflowRevision()=="20261024.sico.flow.v8" '
        'aiMaestroRevision()=="20260908.ai.maestro.v2") '
        'then printf("AI_LOADER_MODULES_OK\\n"))',
    ]
    lines.extend(f"load({_skill_string(path)})" for path in sources)
    lines.extend(f"load({_skill_string(path)})" for path in sources)
    lines.extend(
        (
            f"parsed=aiJsonParseRequest({_skill_string(request)})",
            "if(and(car(parsed) cadr(parsed)['method]==\"eval_skill\") "
            'then printf("AI_PROTOCOL_OK\\n"))',
            f"evalOk=aiEvalExpression({_skill_string(expression)} "
            f"{_skill_string(eval_result)})",
            f"errorOk=aiEvalExpression({_skill_string(failing)} "
            f"{_skill_string(error_result)})",
            f"loadOk=aiLoadSkillFile({_skill_string(loaded)} "
            f"{_skill_string(load_result)})",
            f"largeOk=aiLoadSkillFile({_skill_string(large_loaded)} "
            f"{_skill_string(large_result)})",
            f"oversizedOk=aiEvalExpression({_skill_string(oversized)} "
            f"{_skill_string(oversized_result)})",
            f"contextOk=aiGetContext({_skill_string(context_result)})",
            'when(ddGetObj("SicoTest") error("SicoTest already resolves outside this fresh fixture"))',
            f"inspectLib=ddCreateLib({_skill_string(inspection_lib)} "
            f"{_skill_string(inspection_root)})",
            f"inspectCv=dbOpenCellViewByType({_skill_string(inspection_lib)} "
            f"{_skill_string(inspection_cell)} \"layout\" \"maskLayout\" \"w\")",
            "dbSave(inspectCv)",
            "dbClose(inspectCv)",
            f"snapshotCv=dbOpenCellViewByType({_skill_string(inspection_lib)} "
            f"{_skill_string(snapshot_leaf)} \"schematic\" \"schematic\" \"w\")",
            'leafNet=dbCreateNet(snapshotCv "D")',
            'leafTerm=dbCreateTerm(leafNet "D" "input")',
            'leafPin=dbCreatePin(leafNet nil "D" leafTerm)',
            "dbSave(snapshotCv)",
            "dbClose(snapshotCv)",
            f"snapshotCv=dbOpenCellViewByType({_skill_string(inspection_lib)} "
            f"{_skill_string(snapshot_cell)} \"schematic\" \"schematic\" \"w\")",
            'topNet=dbCreateNet(snapshotCv "out")',
            'topTerm=dbCreateTerm(topNet "OUT" "output")',
            'topPin=dbCreatePin(topNet nil "OUT" topTerm)',
            f"leafReadCv=dbOpenCellViewByType({_skill_string(inspection_lib)} "
            f"{_skill_string(snapshot_leaf)} \"schematic\" \"schematic\" \"r\")",
            'snapshotInst=dbCreateInst(snapshotCv leafReadCv "X0" list(0 0) "R0")',
            'snapshotInstTerm=dbCreateInstTerm(topNet snapshotInst car(leafReadCv~>terminals))',
            "dbSave(snapshotCv)",
            "dbClose(snapshotCv)",
            "dbClose(leafReadCv)",
            "inspectLibraries=aiListLibraries(1)",
            f"aiWriteTextFile({_skill_string(libraries_result)} inspectLibraries)",
            'inspectMissing=aiInspectLibrary("__ai_inspect_missing_library__")',
            f"aiWriteTextFile({_skill_string(missing_library_result)} inspectMissing)",
            f"inspectLayout=aiInspectLayout({_skill_string(inspection_lib)} "
            f"{_skill_string(inspection_cell)} \"layout\" \"maskLayout\" 1)",
            f"aiWriteTextFile({_skill_string(named_layout_result)} inspectLayout)",
            f"snapshotValue=aiSnapshotSchematic({_skill_string(inspection_lib)} "
            f"{_skill_string(snapshot_cell)} \"schematic\" \"schematic\" "
            f"{_skill_string(snapshot_result)} nil)",
            'if(and(index(snapshotValue "\\\"ok\\\":true") '
            f'isFile({_skill_string(snapshot_result)})) '
            'then printf("AI_SCHEMATIC_SNAPSHOT_OK\\n"))',
            'if(and(aiInspectJsonNumber(4)=="4" '
            'aiInspectJsonNumber(1.25)=="1.25" '
            'aiInspectJsonNumber(nil)=="null") '
            'then printf("AI_INSPECTION_NUMBER_JSON_OK\\n"))',
            'procedure(aiInspectCellViewJson(cellView budget) '
            'error("AI_INSPECT_FORCED_BODY_ERROR"))',
            f"inspectForced=aiInspectLayout({_skill_string(inspection_lib)} "
            f"{_skill_string(inspection_cell)} \"layout\" \"maskLayout\" 1)",
            f"aiWriteTextFile({_skill_string(forced_error_result)} inspectForced)",
            f"verifyCv=dbOpenCellViewByType({_skill_string(inspection_lib)} "
            f"{_skill_string(inspection_cell)} \"layout\" \"maskLayout\" \"r\")",
            "firstClose=car(errset(dbClose(verifyCv) nil))",
            "secondClose=car(errset(dbClose(verifyCv) nil))",
            'closeJson=sprintf(nil "{\\"first_close\\":%s,\\"second_close\\":%s}" '
            'if(firstClose then "true" else "false") '
            'if(secondClose then "true" else "false"))',
            f"aiWriteTextFile({_skill_string(close_result)} closeJson)",
            'printf("AI_INSPECTION_OK\\n")',
            "if(and(evalOk !errorOk loadOk largeOk !oversizedOk contextOk "
            'isCallable(\'aiProbeLoaded) aiProbeLoaded()=="AI_LOAD_OK") '
            'then printf("AI_EVALUATOR_OK\\n"))',
            "if(and(aiProtocolRevision()==\"20260909.ai.protocol.v4\" "
            "aiEvaluatorRevision()==\"20261101.ai.eval.v6\" "
            "aiIpcRevision()==\"20260911.ai.ipc.v13\" "
            "aiLiveModelRevision()==\"20261101.ai.live.v2\" "
            "aiLauncherRevision()==\"20261024.sico.launcher.v23\" "
            "aiInspectRevision()==\"20261101.ai.inspect.v12\" "
            "aiWorkflowRevision()==\"20261024.sico.flow.v8\" "
            "aiMaestroRevision()==\"20260908.ai.maestro.v2\") "
            'then printf("AI_RELOAD_OK\\n"))',
            f"procedure(witMenuToolsRoot() {_skill_string(AI_ROOT.parent)})",
            'setShellEnvVar("CAD_HOME" "/not/the/cad/home")',
            f"load({_skill_string(loader)})",
            f"if(and(aiResolveRoot()=={_skill_string(AI_ROOT)} "
            'aiLoaderRevision()=="20260924.ai.loader.v34") '
            'then printf("AI_MENU_ROOT_OK\\n"))',
            'unsetShellEnvVar("SICO_AI_PYTHON_ENTRY")',
            'unsetShellEnvVar("CAD_CODEX_PYTHON_ENTRY")',
            f"if(aiPythonEntry()=={_skill_string(python_entry)} "
            'then printf("AI_SOURCE_ENTRY_OK\\n"))',
            f'setShellEnvVar("SICO_PYTHON_ROOT" {_skill_string(unrelated_python_root)})',
            f'setShellEnvVar("SICO_PYTHON" {_skill_string(configured_python)})',
            f"if(aiPythonExecutable()=={_skill_string(production_python)} "
            'then printf("AI_CONFIGURED_PYTHON_OK\\n"))',
            f'setShellEnvVar("SICO_AI_PYTHON_ENTRY" {_skill_string(python_entry)})',
            'setShellEnvVar("PROJ_AI_LSF_EXE" "")',
            f"claudeCommand=aiControllerCommand({_skill_string(tmp_path)} \"claude\")",
            "if(and(claudeCommand index(claudeCommand \" session --agent 'claude'\") "
            'index(claudeCommand "--workspace") '
            'index(claudeCommand "SICO_TEMP_DIR=") '
            'index(claudeCommand "/.sico/ai") '
            'index(claudeCommand "SICO_VIRTUOSO_LOADEDMODULES=") '
            'index(claudeCommand "SICO_VIRTUOSO_MODULEPATH=") '
            'index(claudeCommand "SICO_VIRTUOSO_LD_LIBRARY_PATH=") '
            'index(claudeCommand " TMPDIR=") '
            'index(claudeCommand " SQLITE_TMPDIR=") '
            'index(claudeCommand " XDG_CACHE_HOME=") '
            'index(claudeCommand " XDG_RUNTIME_DIR=") '
            '!index(claudeCommand "lsf-session")) '
            'then printf("AI_CLAUDE_COMMAND_OK\\n"))',
            'setShellEnvVar("PROJ_AI_LSF_EXE" "/usr/bin/bsub")',
            'setShellEnvVar("PROJ_AI_LSF_ARGS" "-I -q interactive")',
            f"lsfCommand=aiControllerCommand({_skill_string(tmp_path)} \"codex\")",
            "lsfReadyDelay=aiControllerReadyDelay()",
            'if(and(index(lsfCommand " lsf-session ") '
            '!index(lsfCommand "interactive")) '
            'then printf("AI_LSF_COMMAND_OK\\n"))',
            'setShellEnvVar("PROJ_AI_LSF_EXE" "")',
            'setShellEnvVar("PROJ_AI_LSF_ARGS" "")',
            "if(and(lsfReadyDelay==3000 aiControllerReadyDelay()==50) "
            'then printf("AI_LSF_READY_DELAY_OK\\n"))',
            "aiProbeActivations=0",
            "procedure(aiIpcActivate() "
            "aiProbeActivations=aiProbeActivations+1 'aiProbeCid)",
            "cadDisplayAiAssistant()",
            "if(aiProbeActivations==1 "
            'then printf("AI_ACTIVE_SESSION_MENU_OK\\n"))',
            f'setShellEnvVar("SICO_PYTHON" {_skill_string(configured_python)})',
            f'setShellEnvVar("SICO_AI_PYTHON_ENTRY" {_skill_string(missing_entry)})',
            f"if(!aiStart({_skill_string(tmp_path)}) "
            'then printf("AI_MISSING_ENTRY_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    for marker in (
        "AI_PROTOCOL_OK",
        "AI_LOADER_MODULES_OK",
        "AI_EVALUATOR_OK",
        "AI_RELOAD_OK",
        "AI_MENU_ROOT_OK",
        "AI_CONFIGURED_PYTHON_OK",
        "AI_SOURCE_ENTRY_OK",
        "AI_CLAUDE_COMMAND_OK",
        "AI_LSF_COMMAND_OK",
        "AI_LSF_READY_DELAY_OK",
        "AI_ACTIVE_SESSION_MENU_OK",
        "AI_MISSING_ENTRY_OK",
        "AI_INSPECTION_OK",
        "AI_INSPECTION_NUMBER_JSON_OK",
        "AI_SCHEMATIC_SNAPSHOT_OK",
    ):
        assert marker in output, output
    assert "AI Assistant Python entry is missing" in output
    assert "*Error*" not in output
    assert "unbound variable" not in output

    evaluated = _result(eval_result)
    assert evaluated["ok"] is True
    assert evaluated["value"] == "nil"
    assert evaluated["value_truncated"] is False
    assert "AI_CAPTURE_OK" in str(evaluated["output"])
    assert "AI_CAPTURE_WARNING" in str(evaluated["output"])
    assert "AI_CAPTURE_ERROR_PORT" in str(evaluated["output"])
    assert evaluated["output_truncated"] is False
    assert evaluated["output_capture"] == "skill_ports"

    failed = _result(error_result)
    assert failed["ok"] is False
    assert "AI_EXPECTED_ERROR" in str(failed["error"])
    assert "*Error* AI_EXPECTED_ERROR" in str(failed["output"])
    assert failed["output_capture"] == "skill_ports"
    assert _result(load_result)["ok"] is True
    large = _result(large_result)
    assert large["ok"] is True
    assert large["output_truncated"] is True
    assert len(str(large["output"])) == 65_536
    oversized_payload = _result(oversized_result)
    assert oversized_payload["ok"] is False
    assert "exceeds 65536 bytes" in str(oversized_payload["error"])

    context = _result(context_result)
    assert context["ok"] is True
    assert isinstance(context["cwd"], str) and context["cwd"]
    assert context["selection_count"] == 0

    libraries = _result(libraries_result)
    assert libraries["ok"] is True
    assert libraries["kind"] == "libraries"
    assert libraries["max_items"] == 1
    assert isinstance(libraries["libraries"], list)
    assert len(libraries["libraries"]) <= 1
    assert isinstance(libraries["truncated"], bool)

    missing_library = _result(missing_library_result)
    assert missing_library["ok"] is False
    assert missing_library["error"] == {
        "code": "library_not_found",
        "message": "the requested library was not found",
    }

    named_layout = _result(named_layout_result)
    assert named_layout == {
        "ok": True,
        "kind": "layout",
        "cellview": {
            "lib": inspection_lib,
            "cell": inspection_cell,
            "view_name": "layout",
            "oa_view_type": "maskLayout",
            "oa_view_type_source": "platform_api",
        },
        "shapes": [],
            "instances": [],
            "modified": False,
            "max_items": 1,
        "truncated": False,
    }

    forced_error = _result(forced_error_result)
    assert forced_error["ok"] is False
    assert forced_error["error"] == {
        "code": "inspection_failed",
        "message": "layout inspection failed",
    }
    assert _result(close_result) == {"first_close": True, "second_close": False}
    snapshot_lines = [
        json.loads(line) for line in snapshot_result.read_text(encoding="utf-8").splitlines()
    ]
    assert snapshot_lines[0]["record_type"] == "header"
    assert snapshot_lines[0]["schema_version"] == "cad.schematic.snapshot.v1"
    assert snapshot_lines[0]["cellview"] == {
        "lib": inspection_lib,
        "cell": snapshot_cell,
        "view": "schematic",
    }
    assert snapshot_lines[-1] == {
        "record_type": "footer",
        "counts": {"instances": 1, "nets": 1, "terminals": 1},
        "truncated": False,
    }
    assert [line["record_type"] for line in snapshot_lines] == [
        "header",
        "instance",
        "net",
        "terminal",
        "footer",
    ]
    instance = snapshot_lines[1]["data"]
    assert instance["name"] == "X0"
    assert instance["terminals"][0]["net"] == "out"
    assert snapshot_lines[2]["data"]["name"] == "out"
    assert snapshot_lines[3]["data"]["pins"] == [{"net": "out"}]
