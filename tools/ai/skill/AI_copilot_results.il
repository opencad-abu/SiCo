; Read-only shared analysis binding. All result math remains in the common backend.
unless(boundp('aiCopilotResultBindings) aiCopilotResultBindings=makeTable("aiCopilotResultBindings" nil))
unless(boundp('aiCopilotResultPages) aiCopilotResultPages=makeTable("aiCopilotResultPages" nil))
unless(boundp('aiCopilotWavePages) aiCopilotWavePages=makeTable("aiCopilotWavePages" nil))

procedure(aiCopilotBindResults(asbHistory asbSetupRef asbToken)
  prog((asbSetup asbSession asbWindow asbHistoryHandle asbTarget asbPath asbEntry asbRef)
    if(asbSetupRef then
      asbSetup=aiSimulationSetups[asbSetupRef]
      unless(asbSetup error("Original simulation setup missing"))
      aiCopilotTaskCheck(asbSetup['task])
      asbSession=aiResultsSession(asbSetup['context]) asbWindow=asbSetup['window]
    else
      asbSession=aiCopilotSource['adeSession] asbWindow=aiCopilotSource['window])
    unless(and(asbSession member(asbSession maeGetSessions())) error("Captured ADE session required"))
    when(and(aiCopilotSource['adeSession] aiCopilotSource['adeSession]!=asbSession)
      error("Result session differs from captured ADE source"))
    when(and(aiCopilotSource['historyName] aiCopilotSource['historyName]!=asbHistory)
      error("Exact captured History cannot be overridden"))
    asbHistoryHandle=axlGetHistoryEntry(axlGetMainSetupDB(asbSession) asbHistory)
    unless(and(asbHistoryHandle asbHistoryHandle!=0) error("Exact History unavailable"))
    asbTarget=list(axlGetSessionLibName(asbSession) axlGetSessionCellName(asbSession) axlGetSessionViewName(asbSession))
    asbPath=simplifyFilename(ddGetObjReadPath(ddGetObj(car(asbTarget))))
    ; Repeated binding to the same native identity consumes no additional slot.
    foreach(asbKey aiCopilotResultBindings
      asbEntry=aiCopilotResultBindings[asbKey]
      when(and(asbEntry['copilotSource]==aiCopilotSource asbEntry['session]==asbSession
        asbEntry['historyHandle]==asbHistoryHandle asbEntry['history]==asbHistory
        asbEntry['libraryPath]==asbPath asbEntry['test]==aiCopilotSource['testName]) asbRef=asbKey))
    unless(asbRef
      when(length(aiCopilotResultBindings)>=128 error("Result binding limit; start a new native session"))
      asbRef=strcat("copilot-results:" asbToken)
      when(aiCircuitContexts[asbRef] error("Result context collision"))
      asbEntry=makeTable("aiCopilotResultContext" nil)
      asbEntry['ref]=asbRef asbEntry['state]="open" asbEntry['type]="maestro"
      asbEntry['target]=asbTarget asbEntry['session]=asbSession asbEntry['window]=asbWindow
      asbEntry['access]="read" asbEntry['ownership]="borrowed"
      asbEntry['copilotSource]=aiCopilotSource asbEntry['history]=asbHistory
      asbEntry['historyHandle]=asbHistoryHandle asbEntry['libraryPath]=asbPath
      asbEntry['test]=aiCopilotSource['testName]
      aiCopilotResultBindings[asbRef]=asbEntry aiCircuitContexts[asbRef]=asbEntry)
    return(aiCircuitObject(list(list("ok" t) list("context_ref" asbRef)
      list("target" asbTarget) list("history" asbHistory) list("library_path" asbPath)
      list("test_scope" aiCopilotSource['testName]) list("scalar_scope" "whole_history")
      list("ownership" "borrowed") list("automatic_resume_allowed" nil))))))

procedure(aiCopilotResultCheck(ascRef ascHistory ascTest)
  prog((ascEntry ascSession)
    ascEntry=aiCopilotResultBindings[ascRef]
    unless(and(ascEntry ascEntry==aiCircuitContexts[ascRef]
      ascEntry['copilotSource]==aiCopilotSource ascEntry['history]==ascHistory)
      error("Bind the exact History to this task source before capture"))
    ascSession=aiResultsSession(ascRef)
    unless(and(axlGetHistoryEntry(axlGetMainSetupDB(ascSession) ascHistory)==ascEntry['historyHandle]
      simplifyFilename(ddGetObjReadPath(ddGetObj(car(ascEntry['target]))))==ascEntry['libraryPath])
      error("Bound History or library resolution changed"))
    when(and(ascTest ascEntry['test] ascTest!=ascEntry['test]) error("Waveform test differs from captured test"))
    return(t)))

; Pages are frozen private data: source identity is required, live window validity is not.
; Release is also allowed after closure, so interrupted captures cannot exhaust the cache.
procedure(aiCopilotAnalysisPageCall(aspSource aspFunction aspValues)
  prog((aspOwners aspOwner aspRef aspResult)
    aspOwners=if(member(aspFunction list("aiResultsPage" "aiResultsRelease")) aiCopilotResultPages aiCopilotWavePages)
    aspRef=car(aspValues) aspOwner=aspOwners[aspRef]
    when(and(aspOwner aspOwner!=aspSource) error("Capture belongs to another source"))
    if(member(aspFunction list("aiResultsRelease" "aiWaveRelease")) then
      when(aspOwner
        if(aspFunction=="aiResultsRelease" aiResultsRelease(aspRef) aiWaveRelease(aspRef))
        remove(aspRef aspOwners))
      return(aiCircuitObject(list(list("ok" t))))
    else
      unless(aspOwner error("Unknown private capture"))
      aspResult=if(aspFunction=="aiResultsPage"
        aiResultsPage(aspRef cadr(aspValues)) aiWavePage(aspRef cadr(aspValues)))
      return(aspResult))))

procedure(aiCopilotAnalysisCapture(ascFunction ascValues)
  prog((ascOwners ascToken ascTest ascProcedure)
    if(ascFunction=="aiResultsCapture" then
      ascOwners=aiCopilotResultPages ascToken=caddr(ascValues) ascProcedure='aiResultsCapture
    else
      ascOwners=aiCopilotWavePages ascToken=nth(7 ascValues) ascTest=caddr(ascValues)
      ascProcedure=if(ascFunction=="aiWaveCapture" 'aiWaveCapture 'aiAcCapture))
    aiCopilotResultCheck(car(ascValues) cadr(ascValues) ascTest)
    when(or(ascOwners[ascToken] length(ascOwners)>=8
      if(ascFunction=="aiResultsCapture" aiResultsCaptures[ascToken] aiWaveCaptures[ascToken]))
      error("Capture token already used or limit reached"))
    ascOwners[ascToken]=aiCopilotSource
    return(apply(ascProcedure ascValues))))
