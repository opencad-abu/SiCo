"""Real OA target observations and backups, without modifying a user's libraries."""

import json

import pytest
from test_circuit_library_native import ROOT, native


def test_copilot_target_catalog_works_without_gui_windows(tmp_path):
    native(tmp_path, [
        f'ddCreateLib("Work" {json.dumps(str(tmp_path / "Work"))})',
        'v=dbOpenCellViewByType("Work" "Design" "schematic" "schematic" "a")',
        'unless(v error("schematic view could not be created"))',
        'dbSave(v) dbClose(v)',
        'aiWriteTextFile("libraries.json" aiCopilotListLibraries(t))',
        'aiWriteTextFile("cells.json" aiCopilotListCells("Work"))',
        'aiWriteTextFile("views.json" aiCopilotListViews("Work" "Design" "schematic"))',
    ])
    libraries = json.loads((tmp_path / "libraries.json").read_text())
    cells = json.loads((tmp_path / "cells.json").read_text())
    views = json.loads((tmp_path / "views.json").read_text())
    assert libraries["ok"] and any(row["name"] == "Work" for row in libraries["libraries"])
    assert cells == {"ok": True, "cells": ["Design"]}
    assert views == {"ok": True, "views": [{
        "view_name": "schematic", "oa_view_type": "schematic",
        "oa_view_type_source": "platform_api", "open": False,
    }]}


def test_target_empty_unsaved_stale_and_verified_backup(tmp_path):
    modules = ["AI_circuit_extensions.il", "AI_circuit_create_check.il",
               "AI_circuit_text.il", "AI_circuit_preview.il", "AI_circuit_create_build.il",
               "AI_circuit_create.il"]
    native(tmp_path, [f"load({json.dumps(str(ROOT / 'skill' / name))})" for name in modules] + [
        f'ddCreateLib("Work" {json.dumps(str(tmp_path / "Work"))})',
        'aiCircuitBeginTask("t" "background" t)',
        'v=dbOpenCellViewByType("Work" "Design" "schematic" "schematic" "a")',
        'aiWriteTextFile("readonly.json" aiCrInspectTarget(nil "readonly" '
        'list("Work" "Design" "schematic")))',
        'r=makeTable("readonly" nil) r[\'task]="task:t" '
        'r[\'payload]=list(list("Work" "Design" "schematic")) '
        'r[\'targetRecord]=aiCrTargets["circuit-target:readonly"] r[\'targetAction]="reuse_empty"',
        'when(errset(aiCrTargetCheck(r) nil) error("read-only observation authorized a write"))',
        'unless(aiCrInspectTarget("task:t" "readonly" '
        'list("Work" "Design" "schematic")) error("owned re-inspection failed")) '
        'r[\'targetRecord]=aiCrTargets["circuit-target:readonly"] '
        'unless(aiCrTargetCheck(r) error("owned observation was not promoted"))',
        'aiWriteTextFile("empty.json" aiCrInspectTarget("task:t" "empty" '
        'list("Work" "Design" "schematic")))',
        'e=makeTable("entry" nil) e[\'task]="task:t" '
        'e[\'payload]=list(list("Work" "Design" "schematic"))',
        'e[\'targetRecord]=aiCrTargets["circuit-target:empty"] e[\'targetAction]="reuse_empty"',
        'unless(aiCrTargetCheck(e) error("empty view rejected"))',
        'dbCreateRect(v list("text" "drawing") list(0:0 1:1))',
        'when(errset(aiCrTargetCheck(e) nil) error("stale observation accepted"))',
        'aiWriteTextFile("partial.json" aiCrInspectTarget("task:t" "partial" '
        'list("Work" "Design" "schematic")))',
        'e[\'targetRecord]=aiCrTargets["circuit-target:partial"]',
        'when(errset(aiCrTargetCheck(e) nil) error("nonempty treated as empty"))',
        'e[\'targetAction]="replace" e[\'ref]="created:backup_test"',
        'unless(aiCrTargetCheck(e) error("replace evidence rejected"))',
        'before=aiCrSnapshot(v) aiCrExisting(v e)',
        'unless(!v~>shapes error("replacement did not clear shapes"))',
        'backup=dbOpenCellViewByType("Work" "Design" "backup_backup_test" "" "r")',
        'unless(and(backup equal(before aiCrSnapshot(backup))) error("backup differs"))',
        'dbClose(backup) dbClose(v)',
    ])
    empty = json.loads((tmp_path / "empty.json").read_text())
    partial = json.loads((tmp_path / "partial.json").read_text())
    readonly = json.loads((tmp_path / "readonly.json").read_text())
    assert readonly["ok"] and readonly["content_state"] == "empty"
    assert empty["ok"] and empty["content_state"] == "empty"
    assert partial["ok"] and partial["content_state"] == "nonempty"
    assert partial["modified_in_session"]


def test_stale_task_read_only_inspection_is_source_scoped(tmp_path):
    native(tmp_path, [
        f'ddCreateLib("Work" {json.dumps(str(tmp_path / "Work"))})',
        f"load({json.dumps(str(ROOT / 'skill' / 'AI_circuit_create_check.il'))})",
        'procedure(sicoProjectContextJson(source) source[\'projectValid]=t "{}")',
        'procedure(aiCopilotOwner(source) '
        'list("session" "runtime" "instance" "generation" source[\'cwd]))',
        'testSource=makeTable("source" nil) testSource[\'projectValid]=t '
        'testSource[\'valid]=t testSource[\'cwd]="'
        + str(tmp_path) + '"',
        'aiCircuitTasks["task:old"]="background"',
        'aiCopilotTasks["task:old"]=makeTable("oldSource" nil)',
        'aiWriteTextFile("inspect.json" aiCopilotDispatch(testSource "aiCrInspectTarget" '
        'list(list("task:old" "read" list("Work" "Design" "schematic")) nil nil)))',
        'aiWriteTextFile("inspect-nil.json" aiCopilotDispatch(testSource "aiCrInspectTarget" '
        'list(list(nil "read-nil" list("Work" "Design" "schematic")) nil nil)))',
        'aiWriteTextFile("record-kind.json" '
        'if(aiCrTargets["circuit-target:read"]["task"] "owned" "read-only"))',
        'entry=makeTable("entry" nil) entry[\'task]="task:old" '
        'entry[\'payload]=list(list("Work" "Design" "schematic")) '
        'entry[\'targetRecord]=aiCrTargets["circuit-target:read"] '
        'entry[\'targetAction]="reuse_empty"',
        'when(errset(aiCrTargetCheck(entry) nil) '
        'error("read-only observation authorized a write"))',
    ])
    inspected = json.loads((tmp_path / "inspect.json").read_text())
    inspected_nil = json.loads((tmp_path / "inspect-nil.json").read_text())
    assert inspected["valid"] and inspected["circuit"]["ok"]
    assert inspected_nil["valid"] and inspected_nil["circuit"]["ok"]
    assert inspected["circuit"]["content_state"] == "absent"
    assert (tmp_path / "record-kind.json").read_text() == "read-only"


@pytest.mark.parametrize("action", ["reuse_empty", "continue", "replace"])
def test_copilot_existing_target_description_and_final_content_check(tmp_path, action):
    modules = ["AI_circuit_create_check.il", "AI_circuit_text.il", "AI_circuit_preview.il",
               "AI_circuit_create_build.il", "AI_circuit_create.il"]
    native(tmp_path, [f"load({json.dumps(str(ROOT / 'skill' / name))})" for name in modules] + [
        f'ddCreateLib("Work" {json.dumps(str(tmp_path / "Work"))})',
        f'source=makeTable("source" nil) source[\'cwd]={json.dumps(str(tmp_path))}',
        'aiCopilotDispatch(source "aiCircuitBeginTask" list(list("t" "background" t) nil nil))',
        'v=dbOpenCellViewByType("Work" "Design" "schematic" "schematic" "a")',
    ] + ([] if action == "reuse_empty" else [
        'dbCreateRect(v list("text" "drawing") list(0:0 1:1))',
    ]) + [
        'aiCopilotDispatch(source "aiCrInspectTarget" '
        'list(list("task:t" "observed" list("Work" "Design" "schematic")) nil nil))',
        'e=makeTable("entry" nil) e[\'task]="task:t" e[\'digest]="digest" '
        'e[\'payload]=list(list("Work" "Design" "schematic")) '
        'e[\'targetRecord]=aiCrTargets["circuit-target:observed"]',
        f'e[\'targetAction]={json.dumps(action)} aiCreatePrepares["create-prepare:test"]=e',
        'aiWriteTextFile("description.json" aiCopilotDispatch(source "aiCopilotDescribe" '
        'list(list("create_circuit_from_plan" "create-prepare:test" nil nil) nil nil)))',
        'expected=list("task:t" "background" list("Work" "Design" "schematic") '
        '"schematic" "digest" ddGetObjReadPath(ddGetObj("Work")) nil)',
        'let(((aiCopilotSource source)) '
        'unless(aiCopilotFinal("aiCreateExecute" list("create-prepare:test" "digest" "make") '
        'expected list("session" "create-execute:make" "claim")) '
        'error("Existing target rejected")))',
        'dbCreateRect(v list("text" "drawing") list(2:2 3:3))',
        'let(((aiCopilotSource source)) '
        'when(errset(aiCopilotFinal("aiCreateExecute" list("create-prepare:test" "digest" "make") '
        'expected list("session" "create-execute:make" "claim")) nil) '
        'error("Stale target accepted")))',
        'dbClose(v)',
    ])
    description = json.loads((tmp_path / "description.json").read_text())["circuit"]
    assert description["ok"] and description["target_intent"] == "edit"
    assert description["binding"][2] == ["Work", "Design", "schematic"]


def test_read_only_target_and_oversized_contents_fail_before_execution(tmp_path):
    modules = ["AI_circuit_create_check.il", "AI_circuit_text.il", "AI_circuit_preview.il",
               "AI_circuit_create_build.il", "AI_circuit_create.il"]
    native(tmp_path, [f"load({json.dumps(str(ROOT / 'skill' / name))})" for name in modules] + [
        f'ddCreateLib("Work" {json.dumps(str(tmp_path / "Work"))})',
        'aiCircuitBeginTask("t" "background" t)',
        'v=dbOpenCellViewByType("Work" "Design" "schematic" "schematic" "a") dbSave(v) dbClose(v)',
        'v=dbOpenCellViewByType("Work" "Design" "schematic" "" "r")',
        'aiWriteTextFile("readonly.json" aiCrInspectTarget("task:t" "readonly" '
        'list("Work" "Design" "schematic")))',
        'e=makeTable("entry" nil) e[\'task]="task:t" '
        'e[\'payload]=list(list("Work" "Design" "schematic"))',
        'e[\'targetRecord]=aiCrTargets["circuit-target:readonly"] e[\'targetAction]="reuse_empty"',
        'when(errset(aiCrTargetCheck(e) nil) error("Read-only target accepted"))',
        'dbClose(v) v=dbOpenCellViewByType("Work" "Design" "schematic" "" "a")',
        'for(i 1 3000 dbCreateRect(v list("text" "drawing") list(0:0 1:1)))',
        'aiWriteTextFile("large.json" aiCrInspectTarget("task:t" "large" '
        'list("Work" "Design" "schematic")))',
        'dbClose(v)',
    ])
    readonly = json.loads((tmp_path / "readonly.json").read_text())
    oversized = json.loads((tmp_path / "large.json").read_text())
    assert readonly["ok"] and readonly["writable"] is False
    assert not oversized["ok"] and oversized["code"] == "target_inspection_failed"
    assert "response capacity" in oversized["message"]
