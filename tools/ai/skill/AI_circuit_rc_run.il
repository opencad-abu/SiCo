; RC runs retain explicit history and never close a foreground window/session.
procedure(aiRcRunJson(arjRun)
  aiCircuitObject(list(list("ok" t) list("run_ref" arjRun['ref])
    list("circuit_ref" arjRun['circuit]) list("state" arjRun['state])
    list("history" arjRun['history]) list("run_id" arjRun['runId])
    list("progress" arjRun['progress]) list("messages" arjRun['messages])
    list("error" arjRun['error]) list("presentation" arjRun['mode]))))

procedure(aiRcRunCallback(arbSession arbRunId)
  let((arbRun)
    foreach(arbRef aiRcRuns
      arbRun=aiRcRuns[arbRef]
      when(and(arbRun['session]==arbSession member(arbRun['state] list("starting" "running" "stop_requested")))
        arbRun['callbackDone]=t arbRun['runId]=arbRunId))
    t))

procedure(aiRcRun(arrRef arrRequest)
  prog((arrEntry arrSession arrRecord arrRun arrHistory arrResult arrProgress)
    arrResult=errset(progn(
      arrEntry=aiRcRequireEntry(arrRef)
      arrRecord=aiRcReserve(strcat("run:" arrRequest) list(arrRef))
      t) nil)
    unless(arrResult return(aiCircuitError("invalid_context_or_request" "RC run context/request invalid")))
    when(arrRecord['run] return(aiRcStatus(arrRecord['run])))
    arrRun=makeTable("aiRcRun" nil) arrRun['ref]=strcat("rc-run:" arrRequest)
    arrRun['circuit]=arrRef arrRun['mode]=arrEntry['mode] arrRun['state]="starting"
    aiRcRuns[arrRun['ref]]=arrRun arrRecord['run]=arrRun['ref]
    arrResult=errset(progn(
      aiRcValidateSaved(arrEntry)
      arrSession=aiRcRequireSession(arrEntry)
      when(maeIsSetupModified(?session arrSession) error("Maestro has unsaved user edits"))
      unless(equal(aiRcSetupSnapshot(arrSession) arrEntry['setupSnapshot]) error("Maestro setup changed"))
      foreach(arrOther aiRcRuns
        when(and(arrOther!=arrRun['ref] aiRcRuns[arrOther]['session]==arrSession
          member(aiRcRuns[arrOther]['state] list("starting" "running" "stop_requested" "start_unknown" "context_lost"))) error("recipe run already active or unknown")))
      arrProgress=axlGetRunStatus(arrSession)
      unless(and(listp(arrProgress) length(arrProgress)==2
        numberp(car(arrProgress)) numberp(cadr(arrProgress)) car(arrProgress)==cadr(arrProgress))
        error("external simulation active or status unknown"))
      arrRun['session]=arrSession
      arrRun['v]=arrEntry['v] arrRun['tau]=arrEntry['r]*arrEntry['c]
      arrRun['startAttempted]=t
      arrHistory=maeRunSimulation(?session arrSession
        ?runMode "Single Run, Sweeps and Corners" ?callback "aiRcRunCallback" ?waitUntilDone nil)
      unless(and(stringp(arrHistory) strlen(arrHistory)>0) error("simulation did not return a history"))
      arrRun['history]=arrHistory arrRun['state]="running"
      t) t)
    unless(arrResult arrRun['state]=if(arrRun['startAttempted] "start_unknown" "failed")
      arrRun['error]=sprintf(nil "Run validation/start failed: %L; do not resubmit an unknown launch" errset.errset))
    return(aiRcRunJson(arrRun))))

procedure(aiRcStatus(arsRef)
  prog((arsRun arsEntry arsResult)
    arsRun=aiRcRuns[arsRef]
    unless(arsRun return(aiCircuitError("run_not_found" "Unknown run reference")))
    when(member(arsRun['state] list("running" "stop_requested"))
      arsResult=errset(progn(
        arsEntry=aiRcCircuits[arsRun['circuit]]
        unless(aiRcRequireSession(arsEntry)==arsRun['session] error("run session changed"))
        arsRun['progress]=axlGetRunStatus(arsRun['session] ?historyName arsRun['history])
        when(arsRun['callbackDone]
          arsRun['messages]=maeGetSimulationMessages(?session arsRun['session] ?msgType "ERROR")
          if(arsRun['state]=="stop_requested" then arsRun['state]="stopped"
          else if(and(equal(arsRun['progress] list(1 1))
            or(!arsRun['messages] arsRun['messages]=="")) then arsRun['state]="completed"
          else arsRun['state]="failed" arsRun['error]="Completion not confirmed for exactly one error-free point")))
        t) nil)
      unless(arsResult arsRun['state]="context_lost" arsRun['error]="Run session/window cannot be verified"))
    return(aiRcRunJson(arsRun))))

procedure(aiRcResults(arrRef)
  prog((arrRun arrResult arrDb arrRows arrValues arrExpected arrValue arrDelta arrPass
        arrAllPass arrName arrPoint arrCorner arrOut arrTolerance)
    arrRun=aiRcRuns[arrRef]
    unless(arrRun return(aiCircuitError("run_not_found" "Unknown run reference")))
    when(arrRun['results] return(arrRun['results]))
    aiRcStatus(arrRef)
    unless(arrRun['state]=="completed" return(aiCircuitError("run_not_completed" "Numeric results require completed run")))
    arrResult=errset(progn(
      unless(aiRcRequireSession(aiRcCircuits[arrRun['circuit]])==arrRun['session] error("session changed"))
      arrDb=maeReadResDB(?historyName arrRun['history] ?session arrRun['session])
      unless(arrDb error("history RDB unavailable"))
      unless(length(arrDb->points())==1 error("expected one design point"))
      arrPoint=car(arrDb->points())
      unless(and(arrPoint->id==1 length(arrPoint->corners())==1) error("unexpected point/corner dimensions"))
      arrCorner=car(arrPoint->corners())
      arrValues=arrPoint->outputs(?type 'expr)
      arrAllPass=t arrTolerance=arrRun['v]*0.002
      foreach(arrName list("dc_out_v" "tran_tau_v")
        arrOut=setof(arrCandidate arrValues
          and(arrCandidate->name==arrName arrCandidate->testName=="RC"))
        unless(length(arrOut)==1 error("scalar output missing or duplicate: %s" arrName))
        arrOut=car(arrOut) arrValue=arrOut->value
        unless(and(numberp(arrValue) arrValue==arrValue abs(arrValue)<1e300)
          error("output missing/error/non-scalar: %s" arrName))
        arrExpected=if(arrName=="dc_out_v" then arrRun['v]
          else arrRun['v]*(1-((1-exp(-0.001))/0.001)*exp(-1.0)))
        arrDelta=abs(arrValue-arrExpected) arrPass=arrDelta<=arrTolerance
        unless(arrPass arrAllPass=nil)
        arrRows=cons(list(arrName arrValue arrExpected arrDelta arrTolerance aiCircuitBool(arrPass)
          arrPoint->id arrOut->cornerName "RC" "V") arrRows))
      arrRun['results]=aiCircuitObject(list(list("ok" t) list("run_ref" arrRef)
        list("history" arrRun['history]) list("passed" aiCircuitBool(arrAllPass))
        list("columns" list("output" "value" "expected" "absolute_error" "tolerance"
          "passed" "point" "corner" "test" "unit")) list("rows" reverse(arrRows))
        list("source" "maeReadResDB") list("recipe" "rc_lowpass_v1")))
      t) t)
    unless(arrResult return(aiCircuitError("results_unavailable" "Missing/error/non-scalar/unexpected dimensions; inspect CIW")))
    return(arrRun['results])))

procedure(aiRcStop(astRef)
  prog((astRun astResult)
    astRun=aiRcRuns[astRef]
    unless(astRun return(aiCircuitError("run_not_found" "Unknown run reference")))
    unless(member(astRun['state] list("running" "stop_requested"))
      return(aiCircuitError("run_not_running" "Run is not running")))
    astResult=errset(progn(
      unless(aiRcRequireSession(aiRcCircuits[astRun['circuit]])==astRun['session] error("session changed"))
      unless(maeStopSimulation(?historyName list(astRun['history]) ?session astRun['session])
        error("stop request failed"))
      astRun['state]="stop_requested" t) t)
    unless(astResult return(aiCircuitError("stop_failed" "Could not request cancellation; inspect CIW")))
    return(aiRcRunJson(astRun))))
