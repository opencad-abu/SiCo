; Exact reference history ownership, async completion and terminal state retention.
procedure(aiOpampRunReply(aorRun)
  aiCircuitObject(list(list("ok" t) list("run_ref" aorRun['ref]) list("setup_ref" aorRun['setup])
    list("expected_test_points" or(aorRun['expectedPoints] 2)) list("state" aorRun['state]) list("history" aorRun['history]) list("progress" aorRun['progress])
    list("messages" aorRun['messages]) list("error" aorRun['error]) list("spec_qualified" 'jsonFalse))))
procedure(aiOpampCallback(aocSession aocRunId)
  foreach(aocKey aiOpampRuns
    when(and(aiOpampRuns[aocKey]['session]==aocSession
      member(aiOpampRuns[aocKey]['state] list("starting" "running" "stop_requested")))
      aiOpampRuns[aocKey]['callback]=t aiOpampRuns[aocKey]['runId]=aocRunId))
  t)
procedure(aiOpampIdle(aoiSession)
  prog((aoiProgress aoiSettled aoiRun aoiMissing)
    aoiProgress=axlGetRunStatus(aoiSession) aoiMissing=0
    ; Cadence keeps cancelled points in the cumulative total. Account only for
    ; our own histories with a terminal callback and their retained final counts.
    foreach(aoiKey aiOpampRuns
      aoiRun=aiOpampRuns[aoiKey]
      when(and(aoiRun['session]==aoiSession aoiRun['callback]
        member(aoiRun['state] list("completed" "stopped" "failed")))
        aoiSettled=aoiRun['progress]
        unless(and(listp(aoiSettled) length(aoiSettled)==2 numberp(car(aoiSettled))
          numberp(cadr(aoiSettled)) 0<=car(aoiSettled) car(aoiSettled)<=cadr(aoiSettled))
          error("terminal history counts unavailable"))
        aoiMissing=aoiMissing+cadr(aoiSettled)-car(aoiSettled)))
    unless(and(listp(aoiProgress) length(aoiProgress)==2 numberp(car(aoiProgress))
      numberp(cadr(aoiProgress)) cadr(aoiProgress)-car(aoiProgress)==aoiMissing) error("run active/unknown"))
    return(t)))
procedure(aiOpampNoActive(aonRef)
  foreach(aonKey aiOpampRuns
    when(and(aiOpampRuns[aonKey]['setup]==aonRef
      member(aiOpampRuns[aonKey]['state] list("starting" "running" "stop_requested" "start_unknown" "context_lost")))
      error("reference has active/unknown run")))
  t)
procedure(aiOpampRun(aorRef aorRequest aorRoot)
  prog((aorEntry aorRecord aorRun aorResult aorSession)
    aorEntry=aiOpampEntries[aorRef]
    unless(aorEntry return(aiCircuitError("setup_not_found" "Unknown reference setup")))
    aorResult=errset(aiRcReserve(strcat("opamp-run:" aorRequest) list(aorRef aorRoot)) nil)
    unless(aorResult return(aiCircuitError("request_conflict_or_limit" "Run request conflict/limit")))
    aorRecord=car(aorResult)
    when(aorRecord['run] return(aiOpampStatus(aorRecord['run])))
    aorRun=makeTable("aiOpampRun" nil) aorRun['ref]=strcat("opamp-run:" aorRequest)
    aorRun['setup]=aorRef aorRun['state]="preflight"
    aiOpampRuns[aorRun['ref]]=aorRun aorRecord['run]=aorRun['ref]
    aorResult=errset(progn(
      unless(aorEntry['root]==aorRoot error("qualified model root differs"))
      aiOpampValidate(aorEntry) aiOpampNoActive(aorRef)
      aorSession=aiOpampSession(aorEntry) aiOpampIdle(aorSession)
      aorRun['matrix]=aorEntry['matrix]
      aorRun['expectedPoints]=if(aorEntry['matrix] 6*length(car(aorEntry['matrix]))*length(cadr(aorEntry['matrix])) 2)
      aorRun['session]=aorSession aorRun['state]="starting" aorRun['startAttempted]=t
      aorRun['history]=maeRunSimulation(?session aorSession ?runMode "Single Run, Sweeps and Corners"
        ?callback "aiOpampCallback" ?waitUntilDone nil)
      unless(and(stringp(aorRun['history]) strlen(aorRun['history])>0) error("no history returned"))
      aorRun['state]="running" t) t)
    unless(aorResult aorRun['state]=if(aorRun['startAttempted] "start_unknown" "failed")
      aorRun['error]="Reference validation/start failed; inspect CIW; do not repeat unknown launch")
    return(aiOpampRunReply(aorRun))))
procedure(aiOpampStatus(aosRef)
  prog((aosRun aosResult)
    aosRun=aiOpampRuns[aosRef]
    unless(aosRun return(aiCircuitError("run_not_found" "Unknown reference run")))
    when(member(aosRun['state] list("running" "stop_requested"))
      aosResult=errset(progn(
        unless(aiOpampSession(aiOpampEntries[aosRun['setup]])==aosRun['session] error("session changed"))
        aosRun['progress]=axlGetRunStatus(aosRun['session] ?historyName aosRun['history])
        when(aosRun['callback]
          aosRun['messages]=maeGetSimulationMessages(?session aosRun['session] ?msgType "ERROR")
          if(aosRun['state]=="stop_requested" then aosRun['state]="stopped"
          else if(and(equal(aosRun['progress] list(or(aosRun['expectedPoints] 2) or(aosRun['expectedPoints] 2))) or(!aosRun['messages] aosRun['messages]=="")) then
            aosRun['state]="completed"
          else aosRun['state]="failed" aosRun['error]="Expected all requested error-free test points")))
        t) nil)
      unless(aosResult aosRun['state]="context_lost" aosRun['error]="Reference session/window cannot be verified"))
    return(aiOpampRunReply(aosRun))))
procedure(aiOpampStop(aosRef)
  prog((aosRun aosResult)
    aosRun=aiOpampRuns[aosRef]
    unless(and(aosRun member(aosRun['state] list("running" "stop_requested")))
      return(aiCircuitError("run_not_running" "Reference run is not running")))
    aosResult=errset(progn(
      unless(aiOpampSession(aiOpampEntries[aosRun['setup]])==aosRun['session] error("session changed"))
      unless(maeStopSimulation(?historyName list(aosRun['history]) ?session aosRun['session]) error("stop failed"))
      aosRun['state]="stop_requested" t) t)
    unless(aosResult return(aiCircuitError("stop_failed" "Reference stop failed; inspect CIW")))
    return(aiOpampRunReply(aosRun))))
procedure(aiOpampRelease(aorRef)
  prog((aorEntry aorResult aorSession)
    aorEntry=aiOpampEntries[aorRef]
    unless(aorEntry return(aiCircuitError("setup_not_found" "Unknown reference setup")))
    when(aorEntry['released] return(aiOpampReply(aorEntry)))
    aorResult=errset(progn(
      aiOpampNoActive(aorRef) aorSession=aiOpampSession(aorEntry) aiOpampIdle(aorSession)
      when(aorEntry['mode]=="background"
        when(maeIsSetupModified(?session aorSession) error("background setup modified"))
        unless(maeCloseSession(?session aorSession ?simulation "continue") error("close failed")))
      aorEntry['session]=nil aorEntry['released]=t aorEntry['stage]="released" t) t)
    unless(aorResult return(aiCircuitError("release_failed" "Reference resources retained; inspect CIW")))
    return(aiOpampReply(aorEntry))))
