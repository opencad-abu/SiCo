"""Actual DD/technology operations and read-only extension discovery in dbAccess."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cadai.skill_check import check_source

ROOT = Path(__file__).resolve().parents[2]


def native(tmp_path, lines, *, virtuoso=False, forbid=("PARSER WARNING",)):
    binary = shutil.which(os.environ.get("CAD_AI_VIRTUOSO", "virtuoso") if virtuoso
                          else os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if not binary:
        pytest.skip("Virtuoso unavailable" if virtuoso else "dbAccess unavailable")
    modules = (
        "AI_protocol.il", "AI_eval.il", "AI_pdk_core.il", "AI_pdk_init.il", "AI_cdf_data.il",
        "AI_circuit.il", "AI_circuit_task.il",
        "AI_circuit_project.il", "AI_circuit_extensions.il", "AI_copilot_targets.il", "AI_copilot_circuit.il",
        "SICO_context.il", "AI_entry_project.il",
    )
    source = []
    for name in modules:
        owner = ROOT.parent / "sico" if name.startswith("SICO_") else ROOT
        path = owner / "skill" / name
        assert check_source(path.read_bytes(), source_name=name)["ok"]
        source.append(f"load({json.dumps(str(path))})")
    (tmp_path / "cds.lib").write_text("")
    source += [f'ddSetForcedLib({json.dumps(str(tmp_path / "cds.lib"))})']
    source += lines + ['aiWriteTextFile("passed" "yes")', "exit()"]
    script = tmp_path / "test.il"
    script.write_text("\n".join(source))
    command = ([binary, "-nographE", "-nocdsinit", "-log", str(tmp_path / "CDS.log"),
                "-restore", str(script)] if virtuoso
               else [binary, "-nocdsinit", "-load", str(script)])
    # Capture through files, never pipes: Virtuoso can leave helpers that keep
    # the inherited pipe write ends open, and subprocess.run would then wait for
    # EOF until the timeout even though the tool already finished.
    # Never forward the GUI tests' QT_QPA_PLATFORM: an offscreen platform
    # makes some Virtuoso -nographE flows crash (SIGSEGV). Production strips
    # every QT_* variable from worker environments for the same reason.
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("QT_")}
    stdout_path, stderr_path = tmp_path / "stdout.log", tmp_path / "stderr.log"
    with stdout_path.open("w", encoding="utf-8") as out, \
            stderr_path.open("w", encoding="utf-8") as err:
        run = subprocess.run(
            command, cwd=tmp_path, text=True, stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, env=environment, timeout=90,
        )
    output = stdout_path.read_text(errors="replace") + stderr_path.read_text(errors="replace")
    if virtuoso and (tmp_path / "CDS.log").exists():
        output += (tmp_path / "CDS.log").read_text(errors="replace")
    assert run.returncode == 0 and (tmp_path / "passed").exists(), output
    assert "*Error* load:" not in output, output
    for text in forbid:
        assert text not in output, output


def test_library_creation_attach_reuse_conflicts_and_partial_receipt(tmp_path):
    work = json.dumps(str(tmp_path))
    native(tmp_path, [
        f'ddCreateLib("TechProbe" strcat({work} "/TechProbe"))',
        'tf=techOpenTechFile("TechProbe" "tech.db" "w")',
        'unless(tf error("test technology missing"))',
        'techSaveTechFile(tf)',
        f'ddCreateLib("DeviceProbe" strcat({work} "/DeviceProbe"))',
        'techBindTechFile(ddGetObj("DeviceProbe") "TechProbe")',
        'aiCircuitBeginTask("t" "background" t)',
        'aiProjectSession("session")',
        f'aiWriteTextFile("prepare.json" '
        f'aiLibraryPrepare("task:t" "p" nil "DeviceProbe" {work} nil))',
        'unless(!ddGetObj("circuit_p") error("prepare created a library"))',
        'aiProjectClaim("session" "library-create:make" "digest")',
        'let(((aiProjectDispatchKey "library-create:make") (aiProjectDispatchDigest "digest")) '
        'aiWriteTextFile("created.json" aiLibraryCreate("library-prepare:p" "make")) '
        'aiWriteTextFile("repeat.json" aiLibraryCreate("library-prepare:p" "make")))',
        'unless(techGetTechLibName(ddGetObj("circuit_p"))=="TechProbe" error("wrong attachment"))',
        'aiWriteTextFile("receipt.json" aiProjectReceipt("session" "library-create:make" "digest" "create_circuit_library"))',
        f'aiWriteTextFile("reuse.json" aiLibraryPrepare("task:t" "reuse" "circuit_p" "DeviceProbe" {work} nil))',
        f'ddCreateLib("OtherTech" strcat({work} "/OtherTech"))',
        'other=techOpenTechFile("OtherTech" "tech.db" "w") techSaveTechFile(other)',
        f'aiWriteTextFile("conflict.json" aiLibraryPrepare("task:t" "bad" "circuit_p" "OtherTech" {work} nil))',
        f'aiWriteTextFile("partial_prepare.json" aiLibraryPrepare("task:t" "partial" "PartialWork" "DeviceProbe" {work} nil))',
        'originalObservation=getd(\'aiLibraryObservation)',
        'procedure(aiLibraryObservation(name) error("injected interruption during readback"))',
        'aiProjectClaim("session" "library-create:partial" "digest")',
        'let(((aiProjectDispatchKey "library-create:partial") (aiProjectDispatchDigest "digest")) '
        'aiWriteTextFile("partial.json" aiLibraryCreate("library-prepare:partial" "partial")))',
        "putd('aiLibraryObservation originalObservation)",
        'let(((aiProjectDispatchKey "library-create:partial") (aiProjectDispatchDigest "digest")) '
        'aiWriteTextFile("partial_repeat.json" aiLibraryCreate("library-prepare:partial" "partial")))',
    ])
    read = lambda name: json.loads((tmp_path / (name + ".json")).read_text())
    assert read("prepare")["next_action"] == "create_circuit_library"
    created = read("created")
    assert created["saved"] and created["created"] and created["technology_library"] == "TechProbe"
    assert read("repeat") == created
    assert json.loads(read("receipt")["receipt_json"]) == created
    assert read("receipt")["library_observation"] == [
        "circuit_p", str(tmp_path / "circuit_p"), created["technology_path"]]
    assert read("reuse")["library_ready"]
    assert not read("conflict")["ok"]
    assert read("partial")["created"] and not read("partial")["saved"]
    assert read("partial_repeat") == read("partial")


def test_library_reattach_needs_the_flag_and_verifies_readback(tmp_path):
    """更换工艺绑定：默认拒绝并给出可选项提示；带用户确认标志时准备并在执行阶段挂接核验。"""
    work = json.dumps(str(tmp_path))
    native(tmp_path, [
        f'ddCreateLib("TechProbe" strcat({work} "/TechProbe"))',
        'tf=techOpenTechFile("TechProbe" "tech.db" "w")',
        'unless(tf error("test technology missing"))',
        'techSaveTechFile(tf)',
        f'ddCreateLib("OtherTech" strcat({work} "/OtherTech"))',
        'other=techOpenTechFile("OtherTech" "tech.db" "w") techSaveTechFile(other)',
        f'ddCreateLib("ReattachProbe" strcat({work} "/ReattachProbe"))',
        'techBindTechFile(ddGetObj("ReattachProbe") "OtherTech")',
        'probeCv=dbOpenCellViewByType("ReattachProbe" "probeCell" "schematic" "schematic" "w")',
        'unless(probeCv error("probe view missing"))',
        'dbSave(probeCv)',
        'aiCircuitBeginTask("t" "background" t)',
        'aiProjectSession("session")',
        f'aiWriteTextFile("refused.json" '
        f'aiLibraryPrepare("task:t" "no" "ReattachProbe" "TechProbe" {work} nil))',
        f'aiWriteTextFile("prepared.json" '
        f'aiLibraryPrepare("task:t" "yes" "ReattachProbe" "TechProbe" {work} t))',
        'aiProjectClaim("session" "library-create:reatt" "digest")',
        'let(((aiProjectDispatchKey "library-create:reatt") (aiProjectDispatchDigest "digest")) '
        'aiWriteTextFile("attached.json" aiLibraryCreate("library-prepare:yes" "reatt")))',
        'unless(techGetTechLibName(ddGetObj("ReattachProbe"))=="TechProbe" '
        'error("reattach failed"))',
    ])
    def read(name):
        return json.loads((tmp_path / (name + ".json")).read_text())

    refused = read("refused")
    assert not refused["ok"] and "reattach_technology=true" in refused["message"]
    prepared = read("prepared")
    assert prepared["reattach_required"] and prepared["next_action"] == "create_circuit_library"
    assert prepared["current_technology_path"].endswith("OtherTech/tech.db")
    assert prepared["library_oa_view_types"] == ["schematic"]
    attached = read("attached")
    assert attached["ok"] and attached["reattached"] and attached["saved"]
    assert attached["previous_technology_path"].endswith("OtherTech/tech.db")
    assert attached["technology_path"].endswith("TechProbe/tech.db")


def test_library_creation_without_technology_leaves_the_library_unbound(tmp_path):
    work = json.dumps(str(tmp_path))
    native(tmp_path, [
        'aiCircuitBeginTask("t" "background" t)',
        'aiProjectSession("session")',
        f'aiWriteTextFile("prepare_unbound.json" '
        f'aiLibraryPrepare("task:t" "p" "Unbound" nil {work} nil))',
        'unless(!ddGetObj("Unbound") error("prepare created a library"))',
        'aiProjectClaim("session" "library-create:unbound" "digest")',
        'let(((aiProjectDispatchKey "library-create:unbound") '
        '(aiProjectDispatchDigest "digest")) '
        'aiWriteTextFile("created_unbound.json" '
        'aiLibraryCreate("library-prepare:p" "unbound")))',
        'unless(ddGetObj("Unbound") error("library was not created"))',
        'unless(techGetTechLibName(ddGetObj("Unbound"))=="cdsDefTechLib" '
        'error("default technology missing"))',
    ])
    created = json.loads((tmp_path / "created_unbound.json").read_text())
    assert created["ok"] and created["created"] and created["saved"]
    assert created["technology_library"] is None
    assert created["technology_path"] is None


def test_extension_discovery_exact_matching_and_function_changes_never_execute_callbacks(tmp_path):
    path = json.dumps(str(tmp_path / "DeviceProbe"))
    native(tmp_path, [
        f'ddCreateLib("DeviceProbe" {path})',
        'procedure(testValidator(master params) error("VALIDATOR_EXECUTED"))',
        'procedure(testCreator(cv master name xy orient params) error("CREATOR_EXECUTED"))',
        f'aiWriteTextFile("empty.json" aiCrListExtensions("DeviceProbe" "m" "symbol" {path}))',
        f'aiCrRegisterExtension("installed:m" "v1" list("DeviceProbe" "m" "symbol") {path} \'testValidator \'testCreator)',
        f'aiWriteTextFile("found.json" aiCrListExtensions("DeviceProbe" "m" "symbol" {path}))',
        f'aiWriteTextFile("other.json" aiCrListExtensions("DeviceProbe" "another" "symbol" {path}))',
        'procedure(testValidator(master params) error("CHANGED_VALIDATOR_EXECUTED"))',
        f'aiWriteTextFile("changed.json" aiCrListExtensions("DeviceProbe" "m" "symbol" {path}))',
    ])
    read = lambda name: json.loads((tmp_path / (name + ".json")).read_text())
    assert not read("empty")["items"] and not read("other")["items"]
    assert read("found")["items"] == [["installed:m", "v1", True]]
    assert read("changed")["items"] == [["installed:m", "v1", False]]
    assert not read("found")["callbacks_executed"] and not read("found")["request_validated"]


def copilot_setup(tmp_path):
    work = json.dumps(str(tmp_path))
    return [
        f'ddCreateLib("TechProbe" strcat({work} "/TechProbe"))',
        'tf=techOpenTechFile("TechProbe" "tech.db" "w") techSaveTechFile(tf)',
        f'ddCreateLib("DeviceProbe" strcat({work} "/DeviceProbe"))',
        'techBindTechFile(ddGetObj("DeviceProbe") "TechProbe")',
        f'source=makeTable("source" nil) source[\'cwd]={work}',
        f'otherSource=makeTable("otherSource" nil) otherSource[\'cwd]={work}',
        'aiProjectSession("session")',
        'aiCopilotDispatch(source "aiCircuitBeginTask" list(list("t" "background" t) nil nil))',
        f'aiCopilotDispatch(source "aiLibraryPrepare" '
        f'list(list("task:t" "p" "Work" "DeviceProbe" {work} nil) nil nil))',
        'expected=aiLibraryBinding(aiLibraryPrepares["library-prepare:p"])',
        f'claim=list("session" "library-create:make" {json.dumps("a" * 64)})',
    ]


def test_studio_native_source_task_claim_binding_and_receipt(tmp_path):
    native(tmp_path, copilot_setup(tmp_path) + [
        'aiWriteTextFile("description.json" aiCopilotDispatch(source "aiCopilotDescribe" '
        'list(list("create_circuit_library" "library-prepare:p" nil nil) nil nil)))',
        'when(errset(aiCopilotDispatch(otherSource "aiCopilotDescribe" '
        'list(list("create_circuit_library" "library-prepare:p" nil nil) nil nil)) nil) '
        'error("cross-source task was accepted"))',
        'when(errset(aiCopilotDispatch(source "aiLibraryCreate" '
        'list(list("library-prepare:p" "make") nil expected)) nil) '
        'error("write without claim was accepted"))',
        'aiCopilotDispatch(source "aiProjectClaim" list(claim nil nil))',
        'when(errset(aiCopilotDispatch(otherSource "aiLibraryCreate" '
        'list(list("library-prepare:p" "make") claim expected)) nil) '
        'error("cross-source claim was accepted"))',
        'wrongExpected=cons("task:wrong" cdr(expected))',
        'when(errset(aiCopilotDispatch(source "aiLibraryCreate" '
        'list(list("library-prepare:p" "make") claim wrongExpected)) nil) '
        'error("changed operation binding was accepted"))',
        'unless(!ddGetObj("Work") error("rejected dispatch created a library"))',
        'aiWriteTextFile("created.json" aiCopilotDispatch(source "aiLibraryCreate" '
        'list(list("library-prepare:p" "make") claim expected)))',
        'aiWriteTextFile("receipt.json" aiEntryProjectRead(source "project_receipt" '
        'append(claim list("create_circuit_library"))))',
    ])
    read = lambda name: json.loads((tmp_path / (name + ".json")).read_text())
    assert read("description")["circuit"]["binding"] == [
        "task:t", "background", ["Work", "library", "library"], "library",
        "library-prepare:p", str(tmp_path / "Work"), str(tmp_path),
    ]
    created = read("created")["circuit"]
    assert created["ok"] and created["saved"]
    assert json.loads(read("receipt")["project"]["receipt_json"]) == created


@pytest.mark.parametrize("change", ["target", "technology", "workspace", "mapping"])
def test_studio_native_rechecks_preparation_before_library_write(tmp_path, change):
    work = json.dumps(str(tmp_path))
    changes = {
        "target": [f'ddCreateLib("Work" strcat({work} "/Work"))'],
        "technology": [
            f'ddCreateLib("OtherTech" strcat({work} "/OtherTech"))',
            'otherTf=techOpenTechFile("OtherTech" "tech.db" "w") techSaveTechFile(otherTf)',
            'techBindTechFile(ddGetObj("DeviceProbe") "OtherTech")',
        ],
        "workspace": [f'source[\'cwd]=strcat({work} "/another")'],
        "mapping": ['aiWriteTextFile("other.lib" "")',
                    f'ddSetForcedLib(strcat({work} "/other.lib"))'],
    }
    native(tmp_path, copilot_setup(tmp_path) + [
        'aiCopilotDispatch(source "aiProjectClaim" list(claim nil nil))',
    ] + changes[change] + [
        'when(errset(aiCopilotDispatch(source "aiLibraryCreate" '
        'list(list("library-prepare:p" "make") claim expected)) nil) '
        'error("stale library preparation was accepted"))',
        'when(aiRcRequests["library-create:make"] error("write was dispatched"))',
    ])
    if change != "target":
        assert not (tmp_path / "Work").exists()
