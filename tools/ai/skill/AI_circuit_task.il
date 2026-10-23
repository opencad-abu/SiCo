; Task mode is a user decision, never inferred from DISPLAY or CIW availability.
unless(boundp('aiCircuitTasks) aiCircuitTasks=makeTable("aiCircuitTasks" nil))
unless(boundp('aiRcCircuits) aiRcCircuits=makeTable("aiRcCircuits" nil))
unless(boundp('aiRcRuns) aiRcRuns=makeTable("aiRcRuns" nil))
unless(boundp('aiRcRequests) aiRcRequests=makeTable("aiRcRequests" nil))
unless(boundp('aiRcRequestCount) aiRcRequestCount=0)
unless(boundp('aiCircuitTaskCount) aiCircuitTaskCount=0)

procedure(aiCircuitBeginTask(abtId abtMode abtConfirmed)
  prog((abtRef abtTask)
    unless(and(aiCircuitTextP(abtId) member(abtMode list("foreground" "background"))
      eq(abtConfirmed t))
      return(aiCircuitError("mode_confirmation_required"
        "Ask the user to choose foreground/background at task start")))
    abtRef=strcat("task:" abtId) abtTask=aiCircuitTasks[abtRef]
    when(and(abtTask abtTask!=abtMode)
      return(aiCircuitError("task_mode_conflict" "Task already has a different confirmed mode")))
    when(and(abtMode=="foreground" !aiCircuitGuiP())
      return(aiCircuitError("gui_unavailable" "Confirmed foreground mode requires GUI; no fallback")))
    unless(abtTask
      when(aiCircuitTaskCount>=128 return(aiCircuitError("task_limit" "128 task records retained")))
      aiCircuitTasks[abtRef]=abtMode aiCircuitTaskCount=aiCircuitTaskCount+1)
    return(aiCircuitObject(list(list("ok" t) list("task_ref" abtRef)
      list("presentation" abtMode) list("choice_confirmed" t))))))

procedure(aiRcReply(arrEntry)
  aiCircuitObject(list(list("ok" aiCircuitBool(!arrEntry['error]))
    list("circuit_ref" arrEntry['ref]) list("task_ref" arrEntry['task])
    list("target" list(arrEntry['lib] arrEntry['cell] "schematic"))
    list("presentation" arrEntry['mode]) list("stage" arrEntry['stage])
    list("saved" aiCircuitBool(arrEntry['saved])) list("check" arrEntry['check])
    list("preview_digest" arrEntry['digest]) list("maestro_saved" aiCircuitBool(arrEntry['maestroSaved]))
    list("maestro_session" arrEntry['session]) list("error" arrEntry['error]))))

procedure(aiRcReserve(arrKey arrSignature)
  prog((arrPrevious arrEntry)
    when(and(boundp('aiProjectClaims) aiProjectClaims[arrKey])
      unless(and(boundp('aiProjectDispatchKey) aiProjectDispatchKey==arrKey
        boundp('aiProjectDispatchDigest) aiProjectDispatchDigest==aiProjectClaims[arrKey])
        error("journalled request requires its claimed dispatch; query recovery status")))
    arrPrevious=aiRcRequests[arrKey]
    when(arrPrevious
      unless(equal(arrPrevious['signature] arrSignature) error("request_id conflict"))
      return(arrPrevious))
    when(aiRcRequestCount>=256 error("256 recipe request records retained"))
    arrEntry=makeTable("aiRcRequest" nil) arrEntry['signature]=arrSignature
    aiRcRequests[arrKey]=arrEntry aiRcRequestCount=aiRcRequestCount+1
    return(arrEntry)))

procedure(aiRcRequireEntry(areRef)
  prog((areEntry)
    areEntry=aiRcCircuits[areRef]
    unless(and(areEntry areEntry['saved] !areEntry['error] !areEntry['released]) error("RC circuit is not saved/qualified or released"))
    unless(aiCircuitTasks[areEntry['task]]==areEntry['mode] error("task mode changed"))
    when(areEntry['mode]=="foreground"
      unless(and(member(areEntry['window] hiGetWindowList('all))
        geGetWindowCellView(areEntry['window])==areEntry['cv]
        geGetEditCellView(areEntry['window])==areEntry['cv]
        aiCircuitCvMatches(areEntry['cv] list(areEntry['lib] areEntry['cell] "schematic")))
        error("schematic window/target changed")))
    return(areEntry)))

procedure(aiRcRequireSession(arsEntry)
  unless(and(arsEntry['maestroSaved]
    member(arsEntry['session] maeGetSessions())
    aiCircuitSessionMatches(arsEntry['session] list(arsEntry['lib] arsEntry['cell] "maestro"))
    !axlIsSessionReadOnly(arsEntry['session])) error("Maestro session changed"))
  when(arsEntry['mode]=="foreground"
    unless(and(member(arsEntry['maestroWindow] hiGetWindowList('all))
      axlGetWindowSession(arsEntry['maestroWindow])==arsEntry['session]) error("Maestro window changed")))
  arsEntry['session])

procedure(aiRcRelease(arlRef)
  prog((arlEntry arlResult arlSession arlProgress)
    arlEntry=aiRcCircuits[arlRef]
    unless(arlEntry return(aiCircuitError("circuit_not_found" "Unknown circuit reference")))
    when(arlEntry['released] return(aiRcReply(arlEntry)))
    arlResult=errset(progn(
      foreach(arlRun aiRcRuns
        when(and(aiRcRuns[arlRun]['circuit]==arlRef
          member(aiRcRuns[arlRun]['state] list("starting" "running" "stop_requested" "start_unknown" "context_lost")))
          error("run still active or state unknown")))
      when(and(arlEntry['mode]=="background" arlEntry['session])
        arlSession=aiRcRequireSession(arlEntry)
        when(maeIsSetupModified(?session arlSession) error("background setup modified"))
        arlProgress=axlGetRunStatus(arlSession)
        unless(and(listp(arlProgress) length(arlProgress)==2
          numberp(car(arlProgress)) numberp(cadr(arlProgress)) car(arlProgress)==cadr(arlProgress))
          error("simulation active or state unknown"))
        unless(maeCloseSession(?session arlSession ?simulation "continue") error("session close failed")))
      arlEntry['session]=nil arlEntry['released]=t arlEntry['stage]="released"
      t) t)
    unless(arlResult return(aiCircuitError("release_failed" "Resources retained; inspect run/modification state")))
    return(aiRcReply(arlEntry))))
