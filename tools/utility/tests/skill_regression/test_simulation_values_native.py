"""Simulation recipe values, source identity, and fail-closed lifecycle contracts."""

import json

from utility.tests.skill_regression.native import definitions, load, run


def support():
    return definitions("ai_shared") + "\n" + load("tools/ai/skill/core/AI_test_workspace.il")


def test_dimensions_expected_coordinates_and_reply_values(tmp_path):
    run(tmp_path, support() + r'''
auditTests=list(list("a" list("SicoTest" "top" "schematic") "spectre" nil nil
 list(list("tran" nil)) list(list("out" "signal" "/out" t t)) list("schematic") list("spectre"))
 list("b" list("SicoTest" "top" "schematic") "spectre"))
auditPayload=list(list("SicoTest" "sim" "maestro") list(list("vdd" "1") list("bias" "2")) "27" auditTests
 list(list("slow" "125" nil nil) list("fast" "-40" nil nil))
 list(list("vdd" "[1 2]" list(1 2)) list("bias" "[2 3 4]" list(2 3 4))))
unless(aiSimDimensionCount(auditPayload)==6 error("Sweep product"))
auditExpected=aiSimExpected(auditPayload)
unless(and(length(auditExpected)==24
 equal(car(auditExpected) list("a" "slow" 1))
 equal(car(last(auditExpected)) list("b" "fast" 6))) error("Coordinate ordering"))
unless(equal(aiSimGlobalValues(auditPayload) list(list("vdd" "[1 2]") list("bias" "[2 3 4]"))) error("Global sweep values"))
unless(aiSimDimensionPairLess(list("a" 1) list("z" 0)) error("Pair ordering"))
auditEntry=makeTable("entry" nil) auditEntry['ref]="simulation:test" auditEntry['payload]=auditPayload
 auditEntry['task]="task" auditEntry['mode]="background" auditEntry['saved]=t auditEntry['verified]=t
unless(equal(aiSimEntryTests(auditEntry) list("a" "b")) error("Recipe tests"))
unless(equal(aiSimEntryCorners(auditEntry) nth(4 auditPayload)) error("Recipe corners"))
unless(equal(aiSimEntryExpected(auditEntry) auditExpected) error("Recipe expected"))
unless(aiSimEntryTemp(auditEntry)=="27" error("Recipe temperature"))
aiSimRememberOutput(auditEntry list("captured" t))
auditPort=outfile("reply.json") fprintf(auditPort "%s" aiSimReply(auditEntry)) close(auditPort)
aiSimContext(auditEntry)
auditContext=aiCircuitContexts[auditEntry['context]]
unless(and(auditContext['state]=="open" auditContext['type]=="maestro"
 auditContext['ownership]=="managed_simulation" auditContext['access]=="read"
 equal(auditContext['target] car(auditPayload))) error("Result context"))
auditEntry['payload]=list(car(auditPayload) nil "27" auditTests nil nil)
unless(equal(aiSimExpected(auditEntry['payload]) list(list("a" "nominal" 1) list("b" "nominal" 1))) error("Nominal expected"))
unless(aiSimCoordinateNumber(1.0 1.0000000000001) error("Numeric tolerance"))
when(aiSimCoordinateNumber("1" 1) error("Coordinate text coerced"))
when(aiSimCoordinateNumber(1.01 1) error("Coordinate mismatch"))
''')
    reply = json.loads((tmp_path / "reply.json").read_text())
    assert reply["ok"] and reply["saved"] and reply["readback_verified"]
    assert not reply["released"] and not reply["window_open"] and not reply["spec_qualified"]
    assert reply["output"] == "captured" and reply["output_truncated"]
    assert len(reply["expected_test_points"]) == 24


def test_source_snapshot_view_resolution_and_inspection_cleanup(tmp_path):
    run(tmp_path, support() + r'''
aiTestWorkspaceLibrary()
auditSymbol=dbOpenCellViewByType("SicoTest" "child" "symbol" "schematicSymbol" "a")
dbCreateRect(auditSymbol list("device" "drawing") list(0:0 1:1)) dbSave(auditSymbol)
auditChild=dbOpenCellViewByType("SicoTest" "child" "schematic" "schematic" "a")
dbCreateNet(auditChild "inside") dbSave(auditChild)
auditTop=dbOpenCellViewByType("SicoTest" "top" "schematic" "schematic" "a")
auditInst=dbCreateInst(auditTop auditSymbol "I0" 0:0 "R0")
auditNoConnMaster=dbOpenCellViewByType("basic" "noConn" "symbol" "schematicSymbol" "r")
unless(auditNoConnMaster error("basic/noConn symbol unavailable"))
auditNoConn=dbCreateInst(auditTop auditNoConnMaster "NC0" 0:1 "R0")
dbCreateNet(auditTop "out") dbSave(auditTop)
auditTest=list("test" list("SicoTest" "top" "schematic") "spectre" nil nil nil nil
 list("schematic" "symbol") list("symbol"))
unless(equal(aiSimViewNames("SicoTest" "child") list("schematic" "symbol")) error("View inventory"))
unless(aiSimViewFirst("SicoTest" "child" list("missing" "symbol" "schematic"))=="symbol" error("Switch order"))
unless(null(aiSimViewFirst("SicoTest" "absent" list("schematic"))) error("Missing view selected"))
unless(equal(aiSimViewStep(auditInst list("symbol") list("symbol")) list('stop)) error("Stop view ignored"))
unless(equal(aiSimViewStep(auditInst list("schematic") nil) list('follow "SicoTest" "child" "schematic")) error("Follow target"))
unless(equal(aiSimViewStep(auditNoConn list("spectre" "schematic") nil) list('skip)) error("Builtin marker needs a switch view"))
unless(car(aiSimViewStep(auditInst list("absent") nil))=='problem error("Missing switch accepted"))
unless(aiSimViewCoverage(auditTest) error("Finite hierarchy coverage"))
auditSnapshot=aiSimSourceSnapshot(auditTop)
dbReplaceProp(auditInst "value" "string" "1u")
when(equal(auditSnapshot aiSimSourceSnapshot(auditTop)) error("Raw property change missed"))
dbSave(auditTop) auditSnapshot=aiSimSourceSnapshot(auditTop)
dbReplaceProp(auditTop "spectre" "string" "ADE cache")
dbReplaceProp(auditTop "viewNameList" "string" "cache")
unless(equal(auditSnapshot aiSimSourceSnapshot(auditTop)) error("ADE cache treated as design change"))
dbClose(auditTop) dbClose(auditChild) dbClose(auditSymbol) dbClose(auditNoConnMaster)
auditBefore=length(dbGetOpenCellViews())
; Source status is recorded separately; fixtures omit schCheck extraction cache.
procedure(aiSimSourceReadyP(auditCv) t)
auditSignal=list("vout" "signal" "/I0/inside" nil t)
auditProof=aiSimSignalResolve(auditTest auditSignal nil)
unless(and(equal(car(auditProof) "vout") length(nth(3 auditProof))==1) error("Signal hierarchy proof"))
unless(length(dbGetOpenCellViews())==auditBefore error("Read hierarchy view leaked"))
auditSignal=list("vout" "signal" "/out" nil t)
auditTest=list("test" list("SicoTest" "top" "schematic") "spectre" nil nil nil
 list(auditSignal list("expr" "expression" "2+2" nil nil))
 list("schematic" "symbol") list("symbol"))
unless(equal(aiSimSignalChecks(auditTest nil) list(list("vout" "/out" "net" nil))) error("Signal-only checks"))
auditCapture=aiEvalCaptureCall('aiSimSignalResolve
 list(auditTest list("missing" "signal" "/I0/missing" nil t) nil))
when(car(auditCapture) error("Missing signal accepted"))
unless(length(dbGetOpenCellViews())==auditBefore error("Failed signal leaked view"))
foreach(auditPath list("out" "//out" "/out/" "/")
 unless(null(errset(aiSimSignalResolve(auditTest list("bad" "signal" auditPath) nil) nil)) error("Malformed path accepted")))
procedure(aiSimViewStep(auditInstance auditSwitches auditStops) error("injected view inspection failure"))
auditCapture=aiEvalCaptureCall('aiSimViewCoverage list(auditTest))
when(car(auditCapture) error("Failed view step accepted"))
unless(length(dbGetOpenCellViews())==auditBefore error("Failed view coverage leaked an OA reference"))
''')


def test_source_status_and_active_run_accounting_reject_unknown_values(tmp_path):
    run(tmp_path, support() + r'''
procedure(schExtractStatus(auditCv) auditExtract)
procedure(geHasMarker(auditCv auditIgnore auditSeverity) auditHasErrors)
foreach(auditExtract list("clean" 'clean)
 auditHasErrors=t unless(aiSimSourceReadyP('cv) error("Clean source rejected")))
foreach(auditExtract list("dirty" 'dirty)
 auditHasErrors=nil unless(aiSimSourceReadyP('cv) error("Warnings rejected"))
 auditHasErrors=t when(aiSimSourceReadyP('cv) error("Error markers accepted")))
foreach(auditExtract list(nil "stale" 'unknown)
 when(aiSimSourceReadyP('cv) error("Unknown extraction accepted")))
aiSimulationRuns=makeTable("runs" nil)
auditRun=makeTable("run" nil) auditRun['setup]="setup" auditRun['session]="session"
aiSimulationRuns["run"]=auditRun
foreach(auditState list("starting" "running" "stop_requested" "start_unknown" "context_lost")
 auditRun['state]=auditState
 unless(null(errset(aiSimNoActive("setup") nil)) error("Active run allowed")))
auditRun['state]="completed" unless(aiSimNoActive("setup") error("Terminal run blocked"))
procedure(axlGetRunStatus(auditSession) auditCounts)
auditCounts=list(1 1) unless(aiSimIdle("session") error("Idle session rejected"))
auditCounts=list(0 1) unless(null(errset(aiSimIdle("session") nil)) error("External run accepted"))
auditRun['callback]=t auditRun['progress]=list(0 1)
unless(aiSimIdle("session") error("Settled incomplete run accounting"))
auditRun['progress]=nil unless(null(errset(aiSimIdle("session") nil)) error("Unknown terminal counts accepted"))
auditRun['state]="running" auditRun['callback]=nil
aiSimCallback("foreign" "id") when(auditRun['callback] error("Foreign callback routed"))
aiSimCallback("session" "id") unless(and(auditRun['callback] auditRun['runId]=="id") error("Owned callback lost"))
auditRun['state]="completed" auditRun['callback]=nil
aiSimCallback("session" "late") when(auditRun['callback] error("Late callback revived run"))
unless(rexMatchp("run_not_found" aiSimStatus("absent")) error("Unknown status"))
unless(rexMatchp("run_not_running" aiSimStop("run")) error("Terminal stop accepted"))
''')
