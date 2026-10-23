; New setup creation and task/session lifecycle; never overwrite or close user windows.
unless(boundp('aiSimulationSetups) aiSimulationSetups=makeTable("aiSimulationSetups" nil))
unless(boundp('aiSimulationRuns) aiSimulationRuns=makeTable("aiSimulationRuns" nil))
procedure(aiSimReply(asrEntry)
  let((asrPairs)
    asrPairs=list(list("ok" aiCircuitBool(!asrEntry['error])) list("setup_ref" asrEntry['ref])
      list("context_ref" asrEntry['context]) list("task_ref" asrEntry['task])
      list("recipe_digest" asrEntry['digest]) list("target" car(asrEntry['payload]))
      list("tests" foreach(mapcar asrTest nth(3 asrEntry['payload]) car(asrTest)))
      list("temperature_c" nth(2 asrEntry['payload])) list("global_variables" aiSimGlobalValues(asrEntry['payload]))
      list("corners" nth(4 asrEntry['payload])) list("sweeps" nth(5 asrEntry['payload]))
      list("expected_test_points" aiSimExpected(asrEntry['payload]))
      list("config_inputs" foreach(mapcar asrTest setof(asrT nth(3 asrEntry['payload]) aiSimConfigRef(asrT))
        list(car(asrTest) aiSimConfigRef(asrTest) cadr(asrTest))))
      list("config_validation" when(and(asrEntry['sources] exists(asrT nth(3 asrEntry['payload]) aiSimConfigRef(asrT))) aiSimConfigSummary(asrEntry)))
      list("source_scope" "direct schematics; Config bounded schematic hierarchy to Spectre stops")
      list("test_configuration" foreach(mapcar asrTest nth(3 asrEntry['payload])
        list(car(asrTest) cadr(asrTest) caddr(asrTest) nth(3 asrTest)
          foreach(mapcar asrAna nth(5 asrTest) car(asrAna))
          foreach(mapcar asrOut nth(6 asrTest) car(asrOut)))))
      list("presentation" asrEntry['mode]) list("stage" asrEntry['stage])
      list("saved" aiCircuitBool(asrEntry['saved])) list("readback_verified" aiCircuitBool(asrEntry['verified]))
      list("released" aiCircuitBool(asrEntry['released]))
      list("window_open" aiCircuitBool(and(asrEntry['window] member(asrEntry['window] hiGetWindowList('all)))))
      list("error" asrEntry['error]) list("spec_qualified" 'jsonFalse))
    when(asrEntry['outputCaptured]
      asrPairs=append(asrPairs list(
        list("output" or(asrEntry['output] ""))
        list("output_truncated" aiCircuitBool(asrEntry['outputTruncated]))
        list("output_capture" "skill_ports"))))
    aiCircuitObject(asrPairs)))
procedure(aiSimConfigure(ascEntry)
  prog((ascSession ascPayload ascEnv ascName)
    ascSession=ascEntry['session] ascPayload=ascEntry['payload]
    foreach(ascTest nth(3 ascPayload)
      ascName=car(ascTest) ascEntry['stage]=strcat("test:" ascName)
      unless(maeCreateTest(ascName ?lib car(cadr(ascTest)) ?cell cadr(cadr(ascTest)) ?view caddr(cadr(ascTest))
        ?simulator caddr(ascTest) ?session ascSession) error("test creation failed"))
      foreach(ascVar nth(3 ascTest)
        unless(maeSetVar(car(ascVar) cadr(ascVar) ?typeName "test" ?typeValue list(ascName) ?session ascSession)
          error("test variable failed")))
      ascEnv=nil
      ; Empty models inherit the ADE session modelFiles (set by init_pdk/libInit);
      ; only an explicit recipe list becomes a test-level content identity.
      when(nth(4 ascTest) ascEnv=append(ascEnv list(list("modelFiles" nth(4 ascTest)))))
      ; Config owns hierarchy lists. ADE resets its inactive schematic lists on reopen.
      unless(aiSimConfigRef(ascTest)
        ascEnv=append(ascEnv list(list("switchViewList" nth(7 ascTest)) list("stopViewList" nth(8 ascTest)))))
      when(ascEnv
        unless(maeSetEnvOption(ascName ?options ascEnv ?session ascSession) error("environment failed")))
      unless(maeSetSimOption(ascName ?options append(list(list("temp" nth(2 ascPayload))) aiSimSimOptions(ascTest))
        ?session ascSession) error("test simulator options failed"))
      foreach(ascAnalysis nth(5 ascTest)
        unless(maeSetAnalysis(ascName car(ascAnalysis) ?enable t ?options cadr(ascAnalysis) ?session ascSession)
          error("analysis failed")))
      foreach(ascOutput nth(6 ascTest)
        if(cadr(ascOutput)=="signal" then
          unless(maeAddOutput(car(ascOutput) ascName ?outputType or(nth(5 ascOutput) "net") ?signalName caddr(ascOutput)
            ?plot nth(3 ascOutput) ?save nth(4 ascOutput) ?session ascSession) error("signal output failed"))
        else unless(maeAddOutput(car(ascOutput) ascName ?outputType "point" ?expr caddr(ascOutput)
          ?plot nth(3 ascOutput) ?save nth(4 ascOutput) ?session ascSession) error("expression output failed")))))
    foreach(ascVar cadr(ascPayload)
      unless(maeSetVar(car(ascVar) cadr(ascVar) ?session ascSession) error("global variable failed"))
      foreach(ascTest nth(3 ascPayload)
        unless(assoc(car(ascVar) nth(3 ascTest))
          unless(maeSetVar(car(ascVar) "" ?typeName "test" ?typeValue list(car(ascTest)) ?session ascSession)
            error("global variable test reference failed")))
        ; enabled=t enables the local override; nil uses the Global value.
        unless(axlSetDesignVariablePerTest(axlGetMainSetupDB(ascSession) car(ascVar) car(ascTest)
          ?enabled if(assoc(car(ascVar) nth(3 ascTest)) t nil)) error("global variable test binding failed"))))
    ; Point Sweeps must remain selected even for one scalar nominal point: clearing
    ; it suppresses global design variables in the generated point netlist.
    unless(axlSetAllSweepsEnabled(axlGetMainSetupDB(ascSession) 1) error("point variables enable failed"))
    when(cadr(ascPayload)
      unless(and(maeEnableGlobalVar(?enable t ?session ascSession)
        maeEnableGlobalVar(?enable t ?varList foreach(mapcar ascVar cadr(ascPayload) car(ascVar))
          ?session ascSession)) error("global variables enable failed")))
    aiSimConfigureDimensions(ascEntry)
    return(t)))
procedure(aiSimContext(ascEntry)
  let((ascContext)
    ascContext=makeTable("aiSimulationResultContext" nil)
    ascContext['ref]=strcat("simulation-context:" ascEntry['ref]) ascContext['state]="open"
    ascContext['target]=car(ascEntry['payload]) ascContext['type]="maestro"
    ascContext['presentation]=ascEntry['mode] ascContext['access]="read"
    ascContext['ownership]="managed_simulation" ascContext['session]=ascEntry['session]
    ascContext['window]=ascEntry['window]
    aiCircuitContexts[ascContext['ref]]=ascContext ascEntry['context]=ascContext['ref] t))
procedure(aiSimRememberOutput(asmEntry asmOutput)
  asmEntry['output]=car(asmOutput)
  asmEntry['outputTruncated]=cadr(asmOutput)
  asmEntry['outputCaptured]=t)
; Everything here is source/project inspection; ADE is opened only after it
; completes. Keep its raw errors out of the CIW without capturing ADE ports.
procedure(aiSimPreflight(aspEntry aspModels)
  prog((aspTarget aspCv)
    aspTarget=car(aspEntry['payload])
    aspEntry['workPath]=aiSimWork(aspEntry)
    aiSimFileCheck(aspEntry aspModels)
    when(apply('ddGetObj aspTarget) error("Maestro target already exists"))
    foreach(aspCv dbGetOpenCellViews()
      when(aiCircuitCvMatches(aspCv aspTarget)
        error("target already open, including unsaved view")))
    aspEntry['sources]=aiSimSources(aspEntry)
    t))
procedure(aiSimCreate(ascTask ascRequest ascDigest ascPayload ascModels)
  prog((ascRecord ascEntry ascResult ascTarget ascSession ascSessions ascPreflight ascOutput)
    unless(member(aiCircuitTasks[ascTask] list("foreground" "background"))
      return(aiCircuitError("task_not_found" "Confirmed task mode required")))
    ascResult=errset(aiRcReserve(strcat("simulation-create:" ascRequest) list(ascTask ascDigest ascPayload ascModels)) nil)
    unless(ascResult return(aiCircuitError("request_conflict_or_limit" "Creation request conflict/limit")))
    ascRecord=car(ascResult) when(ascRecord['entry] return(aiSimReply(ascRecord['entry])))
    ascEntry=makeTable("aiSimulationSetup" nil) ascEntry['ref]=strcat("simulation:" ascRequest)
    ascEntry['task]=ascTask ascEntry['mode]=aiCircuitTasks[ascTask] ascEntry['payload]=ascPayload
    ascEntry['digest]=ascDigest ascEntry['models]=ascModels ascEntry['stage]="preflight"
    ascRecord['entry]=ascEntry aiSimulationSetups[ascEntry['ref]]=ascEntry ascTarget=car(ascPayload)
    ; Session lookup can touch ADE; keep it outside the request-local capture.
    ascSessions=errset(aiCircuitSessions(ascTarget) nil)
    unless(ascSessions
      ascEntry['error]=strcat("Failed at preflight: " aiSimFailText()
        "; target not opened for writing; correct inputs and use a new request.")
      return(aiSimReply(ascEntry)))
    when(car(ascSessions)
      ascEntry['error]="Failed at preflight: Maestro target already open; target not opened for writing; correct inputs and use a new request."
      return(aiSimReply(ascEntry)))
    ascPreflight=aiEvalCaptureCall('aiSimPreflight list(ascEntry ascModels))
    ascOutput=aiBoundResultText(cadr(ascPreflight))
    unless(car(ascPreflight)
      ascEntry['stage]="preflight"
      ascEntry['error]=strcat("Failed at preflight: " caddr(ascPreflight)
        "; target not opened for writing; correct inputs and use a new request.")
      aiSimRememberOutput(ascEntry ascOutput)
      return(aiSimReply(ascEntry)))
    if(cadr(ascOutput) then aiSimRememberOutput(ascEntry ascOutput)
      when(strlen(car(ascOutput)) > 0 aiSimRememberOutput(ascEntry ascOutput)))
    ascResult=errset(progn(
      ascEntry['stage]="open_setup"
      ascSession=maeOpenSetup(car(ascTarget) cadr(ascTarget) caddr(ascTarget) ?application "Assembler" ?mode "a")
      unless(and(ascSession !axlIsSessionReadOnly(ascSession)) error("new writable setup unavailable"))
      ascEntry['session]=ascSession
      when(maeGetSetup(?typeName "tests" ?session ascSession) error("new setup unexpectedly contains tests"))
      aiSimConfigure(ascEntry) ascEntry['stage]="readback" aiSimReadback(ascEntry)
      ascEntry['stage]="save" unless(maeSaveSetup(?session ascSession) error("setup save failed"))
      ascEntry['saved]=t ascEntry['stage]="reopen"
      unless(maeCloseSession(?session ascSession) error("bootstrap close failed"))
      ascEntry['session]=nil
      if(ascEntry['mode]=="foreground" then
        ascEntry['window]=deOpenCellView(car(ascTarget) cadr(ascTarget) caddr(ascTarget) "maestro" nil "a")
        unless(ascEntry['window] error("foreground open failed"))
        aiCircuitPlaceRightHalf(ascEntry['window])
        aiCircuitRaiseWindow(ascEntry['window])
        ascSession=axlGetWindowSession(ascEntry['window])
      else ascSession=maeOpenSetup(car(ascTarget) cadr(ascTarget) caddr(ascTarget) ?application "Assembler" ?mode "a"))
      unless(ascSession error("saved setup reopen failed")) ascEntry['session]=ascSession
      aiSimReadback(ascEntry)
      ; GUI loading may normalize a setup we have just created. Check it before this final save.
      when(maeIsSetupModified(?session ascSession)
        unless(maeSaveSetup(?session ascSession) error("normalized setup save failed")))
      ascEntry['snapshot]=aiSimSnapshot(ascSession)
      aiSimAssertEqual(aiSimSources(ascEntry) ascEntry['sources] "source unchanged")
      aiSimValidate(ascEntry) aiSimContext(ascEntry)
      ascEntry['verified]=t ascEntry['stage]="setup_saved" t) t)
    unless(ascResult ascEntry['error]=strcat("Failed at " ascEntry['stage] ": " aiSimFailText()
      if(ascEntry['stage]=="preflight" "; target not opened for writing; correct inputs and use a new request."
        "; setup creation attempted; partial target/session may remain; inspect before retrying.")))
    when(and(ascEntry['session] isCallable('sicoNativeOutput))
      sicoNativeOutput(ascEntry['window] ascTarget t ascEntry['verified]))
    return(aiSimReply(ascEntry))))
procedure(aiSimModels(asmRef)
  prog((asmEntry)
    asmEntry=aiSimulationSetups[asmRef]
    unless(asmEntry return(aiCircuitError("setup_not_found" "Unknown setup reference")))
    return(aiCircuitObject(list(list("ok" t) list("models" asmEntry['models]))))))
procedure(aiSimInspect(asiRef asiModels)
  prog((asiEntry asiResult)
    asiEntry=aiSimulationSetups[asiRef]
    unless(asiEntry return(aiCircuitError("setup_not_found" "Unknown setup reference")))
    when(or(asiEntry['error] asiEntry['released]) return(aiSimReply(asiEntry)))
    asiResult=errset(progn(aiSimFileCheck(asiEntry asiModels) aiSimValidate(asiEntry)) t)
    unless(asiResult return(aiCircuitError("simulation_setup_changed" strcat("Setup retained: " aiSimFailText()))))
    return(aiSimReply(asiEntry))))
procedure(aiSimRelease(aslRef)
  prog((aslEntry aslResult)
    aslEntry=aiSimulationSetups[aslRef]
    unless(aslEntry return(aiCircuitError("setup_not_found" "Unknown setup reference")))
    when(aslEntry['released] return(aiSimReply(aslEntry)))
    when(aslEntry['error] return(aiCircuitError("partial_setup_retained" "Inspect failed setup/session before recovery")))
    aslResult=errset(progn(
      aiSimValidate(aslEntry) aiSimNoActive(aslRef) aiSimIdle(aslEntry['session])
      when(aslEntry['mode]=="background"
        unless(maeCloseSession(?session aslEntry['session] ?simulation "continue") error("session close failed")))
      when(aslEntry['context] aiCircuitContexts[aslEntry['context]]['state]="released")
      aslEntry['released]=t aslEntry['session]=nil t) t)
    unless(aslResult return(aiCircuitError("release_failed" "Session retained; check user edits/run state")))
    return(aiSimReply(aslEntry))))
