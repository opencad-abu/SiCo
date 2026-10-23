; Exact-history asynchronous runs for generic simulation recipes.
procedure(aiSimRunReply(asrRun)
  aiCircuitObject(list(list("ok" t) list("run_ref" asrRun['ref]) list("setup_ref" asrRun['setup])
    list("context_ref" asrRun['context]) list("state" asrRun['state]) list("history" asrRun['history])
    list("expected_test_points" asrRun['expected]) list("progress" asrRun['progress])
    list("test_states" asrRun['tests]) list("actual_coordinates" asrRun['coordinates])
    list("messages" asrRun['messages]) list("error" asrRun['error])
    list("spec_qualified" 'jsonFalse))))
procedure(aiSimCallback(ascSession ascRunId)
  foreach(ascKey aiSimulationRuns
    when(and(aiSimulationRuns[ascKey]['session]==ascSession
      member(aiSimulationRuns[ascKey]['state] list("starting" "running" "stop_requested")))
      aiSimulationRuns[ascKey]['callback]=t aiSimulationRuns[ascKey]['runId]=ascRunId)) t)
procedure(aiSimNoActive(asnRef)
  foreach(asnKey aiSimulationRuns
    when(and(aiSimulationRuns[asnKey]['setup]==asnRef
      member(aiSimulationRuns[asnKey]['state] list("starting" "running" "stop_requested" "start_unknown" "context_lost")))
      error("setup has active or unknown run"))) t)
procedure(aiSimIdle(asiSession)
  prog((asiCounts asiMissing asiRun asiFinal)
    asiCounts=axlGetRunStatus(asiSession) asiMissing=0
    foreach(asiKey aiSimulationRuns
      asiRun=aiSimulationRuns[asiKey]
      when(and(asiRun['session]==asiSession asiRun['callback]
        member(asiRun['state] list("completed" "failed" "stopped")))
        asiFinal=asiRun['progress]
        unless(and(listp(asiFinal) length(asiFinal)==2 numberp(car(asiFinal)) numberp(cadr(asiFinal)))
          error("terminal run counts unknown"))
        asiMissing=asiMissing+cadr(asiFinal)-car(asiFinal)))
    unless(and(listp(asiCounts) length(asiCounts)==2 numberp(car(asiCounts)) numberp(cadr(asiCounts))
      cadr(asiCounts)-car(asiCounts)==asiMissing) error("external run active or unknown"))
    return(t)))
procedure(aiSimRun(asrRef asrModels asrRequest)
  prog((asrEntry asrRecord asrRun asrResult asrSession)
    asrEntry=aiSimulationSetups[asrRef]
    unless(asrEntry return(aiCircuitError("setup_not_found" "Unknown setup reference")))
    asrResult=errset(aiRcReserve(strcat("simulation-run:" asrRequest) list(asrRef)) nil)
    unless(asrResult return(aiCircuitError("request_conflict_or_limit" "Run request conflict/limit")))
    asrRecord=car(asrResult) when(asrRecord['run] return(aiSimStatus(asrRecord['run])))
    asrRun=makeTable("aiSimulationRun" nil) asrRun['ref]=strcat("simulation-run:" asrRequest)
    asrRun['setup]=asrRef asrRun['state]="preflight" asrRun['context]=asrEntry['context]
    ; Built-in UI "Nominal" is stored as "nominal" in the history RDB.
    asrRun['expected]=aiSimExpected(asrEntry['payload])
    aiSimulationRuns[asrRun['ref]]=asrRun asrRecord['run]=asrRun['ref]
    asrResult=errset(progn(
      aiSimFileCheck(asrEntry asrModels) aiSimValidate(asrEntry) aiSimNoActive(asrRef)
      asrSession=aiSimSession(asrEntry) aiSimIdle(asrSession)
      asrRun['session]=asrSession asrRun['state]="starting" asrRun['attempted]=t
      asrRun['history]=maeRunSimulation(?session asrSession ?runMode "Single Run, Sweeps and Corners"
        ?callback "aiSimCallback" ?waitUntilDone nil)
      unless(and(stringp(asrRun['history]) strlen(asrRun['history])>0) error("no run history returned"))
      asrRun['state]="running" t) t)
    unless(asrResult asrRun['state]=if(asrRun['attempted] "start_unknown" "failed")
      asrRun['error]=strcat("Run validation/start failed: " aiSimFailText()))
    return(aiSimRunReply(asrRun))))
procedure(aiSimFinished(asfRun)
  prog((asfDb asfKeys asfRows asfExpected asfBad asfH)
    asfH=axlGetHistoryEntry(axlGetMainSetupDB(asfRun['session]) asfRun['history])
    unless(and(asfH asfH!=0) error("exact history unavailable"))
    asfDb=maeReadResDB(?historyName asfRun['history] ?session asfRun['session])
    unless(asfDb error("run RDB unavailable"))
    foreach(asfTest asfDb->tests()
      asfKeys=cons(list(asfTest->name asfTest->cornerName asfTest->pointID) asfKeys)
      asfRows=cons(list(asfTest->name asfTest->cornerName asfTest->pointID symbolToString(asfTest->status)) asfRows)
      unless(member(asfTest->status list('done 'success 'completed)) asfBad=t))
    asfRun['tests]=reverse(asfRows)
    unless(and(length(asfKeys)==length(asfRun['expected])
      forall(asfKey asfRun['expected] length(member(asfKey asfKeys))>0))
      error("history test/point coverage differs from recipe: %L" asfKeys))
    aiSimVerifyCoordinates(asfRun asfDb)
    return(!asfBad)))
procedure(aiSimStatus(assRef)
  prog((assRun assResult assGood assMessage)
    assRun=aiSimulationRuns[assRef]
    unless(assRun return(aiCircuitError("run_not_found" "Unknown run reference")))
    when(member(assRun['state] list("running" "stop_requested"))
      assResult=errset(progn(
        unless(aiSimSession(aiSimulationSetups[assRun['setup]])==assRun['session] error("run session changed"))
        assRun['progress]=axlGetRunStatus(assRun['session] ?historyName assRun['history])
        when(assRun['callback]
          assMessage=maeGetSimulationMessages(?session assRun['session] ?msgType "ERROR")
          when(and(assMessage strlen(assMessage)>4096) assMessage=substring(assMessage 1 4096))
          assRun['messages]=assMessage
          if(assRun['state]=="stop_requested" then assRun['state]="stopped"
          else
            assGood=errset(aiSimFinished(assRun) t)
            unless(assGood assRun['error]=strcat("Exact history validation failed: " aiSimFailText()))
            if(and(car(assGood) equal(assRun['progress] list(length(assRun['expected]) length(assRun['expected])))
              or(!assMessage assMessage=="")) then assRun['state]="completed"
            else assRun['state]="failed"
              unless(assRun['error] assRun['error]="Exact history did not complete all requested tests")))) t) t)
      unless(assResult assRun['state]="context_lost" assRun['error]="Run session/window cannot be verified"))
    return(aiSimRunReply(assRun))))
procedure(aiSimStop(astRef)
  prog((astRun astResult)
    astRun=aiSimulationRuns[astRef]
    unless(and(astRun member(astRun['state] list("running" "stop_requested")))
      return(aiCircuitError("run_not_running" "Run is not running")))
    when(astRun['state]=="stop_requested" return(aiSimRunReply(astRun)))
    astResult=errset(progn(
      unless(aiSimSession(aiSimulationSetups[astRun['setup]])==astRun['session] error("session changed"))
      unless(maeStopSimulation(?historyName list(astRun['history]) ?session astRun['session]) error("stop failed"))
      astRun['state]="stop_requested" t) t)
    unless(astResult return(aiCircuitError("stop_failed" "Cancellation not confirmed; resources retained")))
    return(aiSimRunReply(astRun))))
