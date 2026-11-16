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


def test_maestro_skill_stub_lifecycle_with_dbaccess(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    protocol = AI_ROOT / "skill/AI_protocol.il"
    maestro = AI_ROOT / "skill/AI_maestro.il"
    library_path = tmp_path / "library"
    source = _skill_string(maestro)
    lines = [
        f"load({_skill_string(protocol)})",
        f"load({_skill_string(maestro)})",
        f"load({_skill_string(maestro.with_name('AI_maestro_validate.il'))})",
        'probeLibName="aiMaestroDbProbe"',
        f"probeLib=ddCreateLib(probeLibName {_skill_string(library_path)})",
        'probeSchematic=dbOpenCellViewByType(probeLibName "tb" "schematic" '
        '"schematic" "w")',
        "dbSave(probeSchematic) dbClose(probeSchematic)",
        'probeSetup=dbOpenCellViewByType(probeLibName "tb" "maestro" '
        '"maskLayout" "w")',
        "dbSave(probeSetup) dbClose(probeSetup)",
        "probeSessions=nil probeTests=nil probeRunCount=0 probeImmediate=nil",
        "probeCallbackOnStop=nil probeStopped=nil probeSpec=nil probeEnv=nil",
        'probeMessages="" probeCheckedHistory=nil probeQueued=nil probeCloseResult=t',
        "probeEnqueueResult=t probeRunStartFailure=nil",
        "procedure(hiEnqueueCmd(command @optional logCommand) "
        "probeQueued=command probeEnqueueResult)",
        "procedure(maeGetSessions() probeSessions)",
        "procedure(axlGetSessionLibName(session) probeLibName)",
        'procedure(axlGetSessionCellName(session) "tb")',
        'procedure(axlGetSessionViewName(session) "maestro")',
        'procedure(maeOpenSetup(lib cell view @rest args) "probeSession")',
        "procedure(maeCreateTest(name @rest args) probeTests=list(name) t)",
        "procedure(maeDeleteTest(name @rest args) "
        "probeTests=remove(name probeTests) t)",
        "procedure(maeSetAnalysis(test analysis @rest args) t)",
        "procedure(maeSetVar(name value @rest args) t)",
        "procedure(maeSetEnvOption(test @rest args) probeEnv=args t)",
        "procedure(maeAddOutput(name test @rest args) t)",
        "procedure(maeSetSpec(name test @rest args) probeSpec=args t)",
        "procedure(maeSetCorner(name @rest args) t)",
        "procedure(maeGetSetup(@rest args) probeTests)",
        "procedure(maeSaveSetup(@rest args) t)",
        "procedure(maeCloseSession(@rest args) probeCloseResult)",
        "procedure(maeRunSimulation(@rest args) "
        "if(probeRunStartFailure then nil else progn("
        "probeRunCount=probeRunCount+1 "
        'probeHistory=sprintf(nil "Interactive.%d" probeRunCount) '
        'when(probeImmediate aiMaestroRunCallback("probeSession" probeRunCount)) '
        "probeHistory)))",
        "procedure(maeStopSimulation(@rest args) probeStopped=args "
        'when(probeCallbackOnStop aiMaestroRunCallback("probeSession" 999)) t)',
        "procedure(maeCheckSimStatus(session history) "
        "probeCheckedHistory=history list(list(\"nominal\" \"tt\" \"PASS\")))",
        "procedure(maeGetSimulationMessages(@rest args) probeMessages)",
        "procedure(axlGetRunStatus(session @rest args) list(1 2))",
        "if(and(probeLib probeSchematic probeSetup "
        'ddIsObjReadable(ddGetObj(probeLibName "tb" "schematic")) '
        'ddIsObjWritable(ddGetObj(probeLibName "tb" "maestro"))) '
        'then printf("AI_MAESTRO_ACCESS_OK\\n"))',
        "probeCreateArgs=list(probeLibName \"tb\" \"maestro\" \"update\" "
        '"nominal" probeLibName "tb" "schematic" "spectre" '
        'list(list("tran" t list(list("stop" "1n")))) '
        f"list(list(\"VDD\" \"1.2\")) list({source} list({source} \"tt\")) "
        f"list({source}) "
        'list(list("gain" "point" nil "1" nil nil '
        'list(list("minimum" "0")))) '
        'list(list("tt" t list("nominal") nil list(list("TEMP" "27")))))',
        'probeSessions=list("uiSession")',
        "probeOccupied=apply('aiMaestroCreate probeCreateArgs)",
        'if(index(probeOccupied "setup_in_use") '
        'then printf("AI_MAESTRO_SESSION_OCCUPIED_OK\\n"))',
        "probeSessions=nil",
        'probeNoClobber=aiMaestroCreate(probeLibName "tb" "maestro" "error" '
        '"nominal" probeLibName "tb" "schematic" "spectre" '
        'list(list("tran" t nil)) nil nil nil nil nil)',
        'if(index(probeNoClobber "setup_exists") '
        'then printf("AI_MAESTRO_NO_CLOBBER_OK\\n"))',
        "probeBadFile=aiMaestroCreate(probeLibName \"tb\" \"maestro\" "
        '"update" "nominal" probeLibName "tb" "schematic" "spectre" '
        'list(list("tran" t nil)) nil list("/missing/model.scs") nil nil nil)',
        'if(index(probeBadFile "invalid_arguments") '
        'then printf("AI_MAESTRO_FILE_GUARD_OK\\n"))',
        "probeCreated=apply('aiMaestroCreate probeCreateArgs)",
        "probeOptions=cadr(member(?options probeEnv))",
        'probeModels=cadr(assoc("modelFiles" probeOptions))',
        'if(and(index(probeCreated "\\\"ok\\\":true") '
        "length(probeModels)==2 car(probeModels)==" + source + " "
        "equal(cadr(probeModels) list(" + source + ' "tt")) '
        'cadr(member(?minimum probeSpec))=="0" member(?session probeSpec)) '
        'then printf("AI_MAESTRO_CREATE_STUB_OK\\n"))',
        "probeCloseResult=nil",
        "probeCreateCloseFailure=apply('aiMaestroCreate probeCreateArgs)",
        'probeCreateCleanupJob=aiMaestroState[\'jobs]["maestro-1"]',
        'if(and(index(probeCreateCloseFailure "session_close_failed") '
        'index(probeCreateCloseFailure "\\\"job_id\\\":\\\"maestro-1\\\"") '
        'probeCreateCleanupJob[\'session]=="probeSession") '
        'then printf("AI_MAESTRO_CREATE_CLOSE_RETAINED_OK\\n"))',
        "probeCloseResult=t probeCreateStop=aiMaestroStop(\"maestro-1\")",
        'if(index(probeCreateStop "\\\"state\\\":\\\"stopped\\\"") '
        'then printf("AI_MAESTRO_CREATE_CLOSE_RECOVERED_OK\\n"))',
        'aiMaestroState[\'jobs]=makeTable("probeResetJobs" nil) ',
        "aiMaestroState['counter]=0",
        f"probeMessages={_skill_string('x' * 5000)}",
        "probeImmediate=t",
        'probeRun1=aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'probeQueued1=probeQueued aiMaestroFinishPending("maestro-1")',
        'probeStatus1=aiMaestroStatus("maestro-1")',
        'probeJob1=aiMaestroState[\'jobs]["maestro-1"]',
        'if(and(index(probeRun1 "\\\"state\\\":\\\"running\\\"") '
        'index(probeStatus1 "\\\"state\\\":\\\"failed\\\"") '
        'index(probeQueued1 "aiMaestroFinishPending") '
        'probeCheckedHistory=="Interactive.1" strlen(probeJob1[\'messages])==4096 '
        'null(probeJob1[\'session])) '
        'then printf("AI_MAESTRO_EARLY_CALLBACK_OK\\n"))',
        'probeMessages="" probeImmediate=nil',
        'probeRun2=aiMaestroRun(probeLibName "tb" "maestro" "monte_carlo")',
        'probeStatus2=aiMaestroStatus("maestro-2")',
        "probeCallbackOnStop=t",
        'probeStop2=aiMaestroStop("maestro-2")',
        "probeStoppedHist=cadr(member(?historyName probeStopped))",
        'if(and(index(probeRun2 "\\\"state\\\":\\\"running\\\"") '
        'index(probeStatus2 "\\\"progress\\\":[1,2]") '
        'equal(probeStoppedHist list("Interactive.2")) '
        'index(probeStop2 "\\\"state\\\":\\\"stopped\\\"") '
        "null(aiMaestroState['jobs][\"maestro-2\"]['runId])) "
        'then printf("AI_MAESTRO_STOP_ORDER_OK\\n"))',
        "probeImmediate=t probeCloseResult=nil",
        'probeRun3=aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'aiMaestroFinishPending("maestro-3")',
        'probeStatus3=aiMaestroStatus("maestro-3")',
        'if(and(index(probeRun3 "\\\"state\\\":\\\"running\\\"") '
        'index(probeStatus3 "\\\"state\\\":\\\"failed\\\"") '
        'index(probeStatus3 "\\\"session_open\\\":true")) '
        'then printf("AI_MAESTRO_CLOSE_FAILURE_RETAINED_OK\\n"))',
        "probeCleanup3=aiMaestroCleanup()",
        'if(and(!probeCleanup3 aiMaestroState[\'jobs]["maestro-3"][\'session]) '
        'then printf("AI_MAESTRO_CLEANUP_FAILURE_RETAINED_OK\\n"))',
        'probeCloseResult=t probeStop3=aiMaestroStop("maestro-3") probeImmediate=nil',
        'if(index(probeStop3 "\\\"state\\\":\\\"stopped\\\"") '
        'then printf("AI_MAESTRO_CLOSE_FAILURE_RECOVERED_OK\\n"))',
        "probeImmediate=t probeEnqueueResult=nil",
        'probeRun4=aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'probeStatus4=aiMaestroStatus("maestro-4")',
        'if(and(index(probeRun4 "\\\"state\\\":\\\"failed\\\"") '
        'index(probeStatus4 "could not schedule simulation completion cleanup") '
        'index(probeStatus4 "\\\"session_open\\\":true")) '
        'then printf("AI_MAESTRO_ENQUEUE_FAILURE_RETAINED_OK\\n"))',
        'probeEnqueueResult=t probeCloseResult=t probeStop4=aiMaestroStop("maestro-4")',
        'if(index(probeStop4 "\\\"state\\\":\\\"stopped\\\"") '
        'then printf("AI_MAESTRO_ENQUEUE_FAILURE_RECOVERED_OK\\n"))',
        "probeRunStartFailure=t probeCloseResult=nil probeImmediate=nil",
        'probeRun5=aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'if(and(index(probeRun5 "run_start_failed") '
        'index(probeRun5 "\\\"job_id\\\":\\\"maestro-5\\\"") '
        'aiMaestroState[\'jobs]["maestro-5"][\'session]) '
        'then printf("AI_MAESTRO_START_FAILURE_RETAINED_OK\\n"))',
        'probeRunStartFailure=nil probeCloseResult=t probeStop5=aiMaestroStop("maestro-5")',
        'if(index(probeStop5 "\\\"state\\\":\\\"stopped\\\"") '
        'then printf("AI_MAESTRO_START_FAILURE_RECOVERED_OK\\n"))',
        'probeSessions=list("uiSession")',
        'probeOccupiedRun=aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'if(index(probeOccupiedRun "setup_in_use") '
        'then printf("AI_MAESTRO_RUN_OCCUPIED_OK\\n"))',
        "probeSessions=nil probeImmediate=nil",
        "putd('maeCheckSimStatus nil)",
        "procedure(axlGetRunStatus(session @rest args) probeProgress)",
        'probeProgress=list(2 2) probeMessages=""',
        'probeLegacy=aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'probeLegacyRunning=aiMaestroStatus("maestro-6")',
        'aiMaestroRunCallback("probeSession" 6) aiMaestroFinishPending("maestro-6")',
        'probeLegacyDone=aiMaestroStatus("maestro-6")',
        'if(and(!isCallable(\'maeCheckSimStatus) '
        'index(probeLegacy "\\\"state\\\":\\\"running\\\"") '
        'index(probeLegacyRunning "\\\"progress\\\":[2,2]") '
        'index(probeLegacyDone "\\\"state\\\":\\\"completed\\\"") '
        'index(probeLegacyDone "\\\"checks\\\":null") '
        'index(probeLegacyDone "\\\"status_source\\\":\\\"axlGetRunStatus\\\"") '
        'index(probeLegacyDone "\\\"session_open\\\":false")) '
        'then printf("AI_MAESTRO_IC618_COMPLETE_OK\\n"))',
        'probeMessages="ERROR: simulator failed"',
        'aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'aiMaestroRunCallback("probeSession" 7) aiMaestroFinishPending("maestro-7")',
        'if(index(aiMaestroStatus("maestro-7") "\\\"state\\\":\\\"failed\\\"") '
        'then printf("AI_MAESTRO_IC618_ERROR_OK\\n"))',
        'probeMessages="" probeProgress=list(0 0)',
        'aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'aiMaestroRunCallback("probeSession" 8) aiMaestroFinishPending("maestro-8")',
        'if(index(aiMaestroStatus("maestro-8") "do not confirm completion") '
        'then printf("AI_MAESTRO_IC618_NO_FALSE_PASS_OK\\n"))',
        'procedure(maeCheckSimStatus(session history) error("unavailable in this mode"))',
        'probeProgress=list(1 1)',
        'aiMaestroRun(probeLibName "tb" "maestro" "single")',
        'aiMaestroRunCallback("probeSession" 9) aiMaestroFinishPending("maestro-9")',
        'if(index(aiMaestroStatus("maestro-9") "\\\"state\\\":\\\"completed\\\"") '
        'then printf("AI_MAESTRO_OPTIONAL_API_FALLBACK_OK\\n"))',
        "probeSessions=nil aiMaestroState['jobs]=makeTable(\"probeCapJobs\" nil)",
        "for(probeIndex 1 64 probeJob=makeTable(\"probeCapJob\" nil) "
        "probeJob['sequence]=probeIndex "
        "probeJob['state]=if(probeIndex<=2 then \"completed\" else \"running\") "
        "when(probeIndex==1 probeJob['session]=\"retainedSession\") "
        "aiMaestroState['jobs][sprintf(nil \"cap-%d\" probeIndex)]=probeJob)",
        "if(and(aiMaestroReserveJobSlot() aiMaestroState['jobs][\"cap-1\"] "
        "!aiMaestroState['jobs][\"cap-2\"]) "
        'then printf("AI_MAESTRO_EVICTION_OK\\n"))',
        'probeJob=makeTable("probeCapJob64" nil) probeJob[\'sequence]=65 '
        'probeJob[\'state]="running" aiMaestroState[\'jobs]["cap-65"]=probeJob',
        'if(!aiMaestroReserveJobSlot() '
        'then printf("AI_MAESTRO_JOB_CAP_OK\\n"))',
        "probeJobsBefore=aiMaestroState['jobs]",
        f"load({_skill_string(maestro)})",
        f"load({_skill_string(maestro.with_name('AI_maestro_validate.il'))})",
        "if(and(aiMaestroState['jobs]==probeJobsBefore "
        'aiMaestroRevision()=="20260908.ai.maestro.v2") '
        'then printf("AI_MAESTRO_RELOAD_STATE_OK\\n"))',
        'probeCloseResult=nil aiMaestroVersion="probe.upgrade"',
        f"probeBlockedReload=errset(load({_skill_string(maestro)}) nil)",
        "if(and(!probeBlockedReload eq(aiMaestroState['jobs] probeJobsBefore) "
        'aiMaestroState[\'jobs]["cap-1"][\'session]=="retainedSession") '
        'then printf("AI_MAESTRO_RELOAD_CLOSE_BLOCKED_OK\\n"))',
        'probeCloseResult=t aiMaestroVersion="20260908.ai.maestro.v2"',
        "exit()",
    ]
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
        "AI_MAESTRO_ACCESS_OK",
        "AI_MAESTRO_SESSION_OCCUPIED_OK",
        "AI_MAESTRO_NO_CLOBBER_OK",
        "AI_MAESTRO_FILE_GUARD_OK",
        "AI_MAESTRO_CREATE_STUB_OK",
        "AI_MAESTRO_CREATE_CLOSE_RETAINED_OK",
        "AI_MAESTRO_CREATE_CLOSE_RECOVERED_OK",
        "AI_MAESTRO_EARLY_CALLBACK_OK",
        "AI_MAESTRO_STOP_ORDER_OK",
        "AI_MAESTRO_CLOSE_FAILURE_RETAINED_OK",
        "AI_MAESTRO_CLEANUP_FAILURE_RETAINED_OK",
        "AI_MAESTRO_CLOSE_FAILURE_RECOVERED_OK",
        "AI_MAESTRO_ENQUEUE_FAILURE_RETAINED_OK",
        "AI_MAESTRO_ENQUEUE_FAILURE_RECOVERED_OK",
        "AI_MAESTRO_START_FAILURE_RETAINED_OK",
        "AI_MAESTRO_START_FAILURE_RECOVERED_OK",
        "AI_MAESTRO_RUN_OCCUPIED_OK",
        "AI_MAESTRO_IC618_COMPLETE_OK",
        "AI_MAESTRO_IC618_ERROR_OK",
        "AI_MAESTRO_IC618_NO_FALSE_PASS_OK",
        "AI_MAESTRO_OPTIONAL_API_FALLBACK_OK",
        "AI_MAESTRO_EVICTION_OK",
        "AI_MAESTRO_JOB_CAP_OK",
        "AI_MAESTRO_RELOAD_STATE_OK",
        "AI_MAESTRO_RELOAD_CLOSE_BLOCKED_OK",
    ):
        assert marker in output, output
    expected_reload_error = (
        "*Error* AI Assistant cannot reload while a Maestro session remains open"
    )
    assert output.count(expected_reload_error) == 1, output
    assert "*Error*" not in output.replace(expected_reload_error, "")
    assert "unbound variable" not in output
    assert "too many arguments" not in output
    assert "still unclosed" not in output
