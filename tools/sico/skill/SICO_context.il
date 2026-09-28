; Instance-bound context and finite workflow bridge for Silicon Copilot.
; Load AI_protocol.il and AI_eval.il first. No arbitrary SKILL arrives over this channel.

unless(and(boundp('sicoContextVersion)
           sicoContextVersion=="20260921.sico.context.v1")

unless(boundp('sicoBindings) sicoBindings=makeTable("sicoBindings" nil))

procedure(sicoRequestIdP(arpId)
  and(stringp(arpId) strlen(arpId)==32 rexMatchp("^[0-9a-f]+$" arpId)))

procedure(sicoTrace(atcCid atcFields atcEvent @optional atcCode (atcLength 0))
  prog((atcId atcMethod atcTarget)
    unless(isCallable('sicoDiagnostic) return(nil))
    atcId=if(sicoRequestIdP(car(atcFields)) car(atcFields) "")
    atcMethod=or(cadr(atcFields) "get_context")
    unless(member(atcMethod list("get_context" "get_project_context" "read_ade_setup"
      "read_ade_history" "assistant_call" "circuit_call" "project_session" "project_observe"
      "project_receipt" "list_windows" "inspect_window" "get_entry_context")) atcMethod="invalid")
    atcTarget=or(caddr(atcFields) "bound")
    unless(and(strlen(atcTarget)<=160 rexMatchp("^[a-zA-Z0-9_-]+$" atcTarget)) atcTarget="invalid")
    return(car(errset(sicoDiagnostic(sicoBindings[atcCid] atcEvent atcId atcMethod atcTarget
      atcCode length(atcFields) atcLength) nil)))))
; Project identity remains attached to the captured process/directory, not editor lifetime.
procedure(sicoProjectContextJson(apcBinding)
  prog((apcProtected apcValid apcSourceValid apcCwd)
    apcProtected=errset(sicoContextJson(apcBinding) nil)
    apcSourceValid=and(apcProtected apcBinding['valid])
    apcCwd=apcBinding['cwd]
    apcValid=and(stringp(apcCwd) substring(apcCwd 1 1)=="/"
      simplifyFilename(apcCwd)==simplifyFilename(aiWorkingDirectory()))
    apcBinding['projectValid]=apcValid
    return(sprintf(nil
      "{\"valid\":%s,\"source_valid\":%s,\"cwd\":\"%s\",\"cellview\":%s,\"scope\":\"project\",\"version\":\"%s\"}"
      if(apcValid "true" "false") if(apcSourceValid "true" "false")
      aiJsonEscape(or(apcCwd "")) or(apcBinding['cellJson] "null") aiJsonEscape(getVersion(t))))))

procedure(sicoAdeCellJson(session)
  prog((libName cellName viewName)
    libName=axlGetSessionLibName(session)
    cellName=axlGetSessionCellName(session)
    viewName=axlGetSessionViewName(session)
    unless(and(libName cellName viewName) return("null"))
    return(sprintf(nil "{\"lib\":\"%s\",\"cell\":\"%s\",\"view\":\"%s\"}"
      aiJsonEscape(libName) aiJsonEscape(cellName) aiJsonEscape(viewName)))))

procedure(sicoContextJson(binding)
  prog((window cellView liveWindow cellJson valid protected ade historyName historyHandle sharedRef libraryPath libraryName)
    binding['cwd]=or(binding['cwd] aiWorkingDirectory())
    window=binding['window]
    valid=t
    when(window
      protected=errset(hiGetWindowList('all) nil)
      valid=and(protected member(window car(protected)))
      when(valid
        when(isCallable('axlGetWindowSession)
          protected=errset(axlGetWindowSession(window) nil)
          when(protected ade=car(protected)))
        if(ade then
          protected=errset(sicoAdeCellJson(ade) nil)
          cellJson=when(protected car(protected))
          when(or(!cellJson cellJson=="null") valid=nil)
        else
          unless(and(isCallable('hiGetCIWindow) window==hiGetCIWindow())
            if(aiWindowGraphical(window) then
              protected=errset(geGetWindowCellView(window) nil)
              when(protected cellView=car(protected))
              unless(cellView valid=nil)
            else valid=nil)))))
    cellJson=or(cellJson aiCellViewJson(cellView))
    when(and(binding['cellJson] cellJson!=binding['cellJson]) valid=nil)
    unless(binding['cellJson] binding['cellJson]=cellJson)
    when(and(binding['adeSession] ade!=binding['adeSession]) valid=nil)
    when(ade unless(binding['adeSession] binding['adeSession]=ade))
    when(binding['historyHandle]
      protected=errset(axlGetHistoryName(binding['historyHandle]) nil)
      historyName=when(protected car(protected))
      protected=when(ade
        errset(axlGetHistoryEntry(axlGetMainSetupDB(ade) binding['historyName]) nil))
      historyHandle=when(protected car(protected))
      unless(and(historyName==binding['historyName]
                 historyHandle==binding['historyHandle]) valid=nil))
    when(binding['testName]
      protected=when(ade errset(axlGetTests(axlGetMainSetupDB(ade)) nil))
      unless(and(protected member(binding['testName] cadr(car(protected)))) valid=nil))
    when(and(window isCallable('sicoEntryRef))
      sharedRef=sicoEntryRef(binding)
      unless(and(sharedRef aiWindowResolve(sharedRef)) valid=nil))
    libraryName=if(ade aiWindowTry('axlGetSessionLibName list(ade)) when(cellView cellView~>libName))
    when(libraryName libraryPath=car(errset(ddGetObjReadPath(ddGetObj(libraryName)) nil)))
    liveWindow=if(window then sprintf(nil "%L" window) else "")
    binding['valid]=valid
    return(sprintf(nil
      "{\"native_binding\":\"cad_ai_native_binding.v1\",\"valid\":%s,\"cwd\":\"%s\",\"window\":\"%s\",\"cellview\":%s,\"ade_session\":\"%s\",\"version\":\"%s\",\"entry\":\"%s\",\"history\":\"%s\",\"test\":\"%s\",\"selection_scope\":\"%s\",\"capabilities\":%s,\"window_ref\":\"%s\",\"library_path\":\"%s\"}"
      if(valid then "true" else "false") aiJsonEscape(or(binding['cwd] aiWorkingDirectory()))
      aiJsonEscape(liveWindow) cellJson
      aiJsonEscape(if(ade then if(stringp(ade) then ade else sprintf(nil "%L" ade)) else ""))
      aiJsonEscape(getVersion(t)) aiJsonEscape(or(binding['entry] "connect"))
      aiJsonEscape(or(binding['historyName] ""))
      aiJsonEscape(or(binding['testName] ""))
      if(binding['historyName] then "history" else
        if(binding['testName] then "test" else if(ade then "ade_session" else "window")))
      strcat("[\"get_context\",\"get_project_context\""
        if(and(ade isCallable('sicoAdeSetupJson) isCallable('sicoAdeHistoryJson))
          ",\"read_ade_setup\",\"read_ade_history\"" "")
        if(and(isCallable('sicoEntryRead) isCallable('aiWindowList)
               isCallable('aiWindowInspect) isCallable('aiEntryContext))
          ",\"list_windows\",\"inspect_window\",\"get_entry_context\"" "")
        if(and(isCallable('aiEntryProjectRead) isCallable('aiProjectRevision))
          ",\"project_session\",\"project_observe\",\"project_receipt\"" "")
        if(isCallable('aiCopilotDispatch) ",\"circuit_call\"" "")
        if(isCallable('aiEvalExpression) ",\"assistant_call\"" "") "]")
      aiJsonEscape(or(sharedRef "")) aiJsonEscape(or(libraryPath ""))))
  )
)

; Original Assistant MCP operations for Copilot use the same captured source.
; Python performs lexical/definition preflight before this bounded hex payload
; reaches Virtuoso; this procedure only invokes the existing evaluator and
; returns its bounded JSON result.  It never changes aiIpcState or starts a
; second controller.
procedure(sicoAssistantReply(aarCid aarFields)
  let((aarTarget aarMethod aarEncoded aarSource aarSourcePath aarOutput
       aarProtected aarOk aarRaw aarCwd aarPath aarReply aarError aarStarted aarReturned)
    aarProtected=errset(progn(
      unless(length(aarFields)==5 error("Invalid Assistant message"))
      aarTarget=sicoSessionTarget(aarCid caddr(aarFields))
      unless(aarTarget error("Unknown task source"))
      when(isCallable('aiCopilotSourceCheck) aiCopilotSourceCheck(aarTarget))
      aarMethod=nth(3 aarFields)
      unless(member(aarMethod list("eval_skill" "eval_skill_native" "load_skill_file"))
        error("Unsupported Assistant method"))
      aarEncoded=nth(4 aarFields)
      unless(stringp(aarEncoded) error("Assistant payload is not text"))
      if(aarMethod=="load_skill_file" then
        aarPath=simplifyFilename(aiCopilotHex(aarEncoded))
        aarCwd=simplifyFilename(or(aarTarget['cwd] aiWorkingDirectory()) t)
        unless(and(isFile(aarPath) fileLength(aarPath)<=8388608
                   or(rexMatchp("\\.il$" aarPath) rexMatchp("\\.ils$" aarPath)))
          error("Assistant source file is unavailable"))
        unless(and(strlen(aarPath)>strlen(aarCwd)
                   substring(aarPath 1 strlen(aarCwd))==aarCwd
                   substring(aarPath strlen(aarCwd)+1 1)=="/")
          error("Assistant source file is outside the captured workspace"))
        aarSource=nil
      else
        aarSource=aiCopilotHex(aarEncoded)
        unless(and(stringp(aarSource) strlen(aarSource)>0 strlen(aarSource)<=65536)
          error("Assistant source exceeds the size limit")))
      aarOutput=makeTempFileName("/tmp/ai-agent-assistant-result")
      unless(aarMethod=="load_skill_file"
        aarSourcePath=makeTempFileName("/tmp/ai-agent-assistant-source")
        unless(aiWriteTextFile(aarSourcePath aarSource)
          error("Could not spool Assistant source")))
      sicoTrace(aarCid aarFields "skill.source_spooled" aarMethod
        if(aarSource strlen(aarSource) fileLength(aarPath)))
      sicoTrace(aarCid aarFields "skill.evaluation_started" aarMethod)
      aarStarted=t
      aarOk=case(aarMethod
        ("load_skill_file" aiLoadSkillFile(aarPath aarOutput))
        ("eval_skill_native" aiEvalNativeExpression(aarSourcePath aarOutput))
        (t aiEvalExpression(aarSourcePath aarOutput)))
      aarReturned=t
      sicoTrace(aarCid aarFields "skill.evaluation_finished" if(aarOk "ok" "evaluator_failed"))
      aarRaw=aiReadTextFile(aarOutput 180000)
      when(isFile(aarOutput) deleteFile(aarOutput))
      ; A failed evaluation still writes a structured error. Preserve it.
      unless(and(stringp(aarRaw) strlen(aarRaw)>0)
        error("Assistant evaluator did not produce a bounded result"))
      aarRaw) t)
    aarError=sprintf(nil "%L" errset.errset)
    unless(aarProtected
      sicoTrace(aarCid aarFields
        if(aarStarted "skill.evaluation_failed" "skill.request_rejected")
        if(aarReturned "evaluator_result_missing" if(aarStarted "evaluator_exception" "assistant_failed"))))
    when(and(aarSourcePath isFile(aarSourcePath))
      errset(deleteFile(aarSourcePath) nil))
    when(and(aarOutput isFile(aarOutput)) errset(deleteFile(aarOutput) nil))
    aarReply=cond(
      (aarProtected
        sprintf(nil "{\"id\":\"%s\",\"ok\":true,\"result\":%s}\n"
          car(aarFields) car(aarProtected)))
      (t
        sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"assistant_failed\",\"error\":\"%s\"}\n"
          car(aarFields) aiJsonEscape(or(aarError "Assistant operation failed")))))
    aarReply))

procedure(sicoIpcWrite(aiwCid aiwReply) ipcWriteProcess(aiwCid aiwReply))

procedure(sicoWriteReply(awrCid awrReply awrFields)
  prog((awrWritten)
    sicoTrace(awrCid awrFields "skill.reply_prepared" nil strlen(awrReply))
    sicoTrace(awrCid awrFields "skill.reply_write_started")
    awrWritten=car(errset(sicoIpcWrite(awrCid awrReply) nil))
    sicoTrace(awrCid awrFields
      if(awrWritten "skill.reply_write_succeeded" "skill.reply_write_failed")
      if(awrWritten "ok" "ipc_write_failed"))
    return(awrWritten)))

procedure(sicoReadReply(binding line)
  let((payload fields requestId method protected code)
    fields=parseString(line " ")
    requestId=car(fields) method=or(cadr(fields) "get_context")
    when(and(binding length(fields)<=2 stringp(requestId) strlen(requestId)==32
             rexMatchp("^[0-9a-f]+$" requestId)
             member(method list("get_context" "get_project_context" "read_ade_setup" "read_ade_history")))
      protected=errset(if(method=="get_project_context"
        sicoProjectContextJson(binding) sicoContextJson(binding)) nil)
      code="stale_context"
      when(protected
        if(member(method list("get_context" "get_project_context")) then payload=car(protected)
        else when(binding['valid]
          code="unavailable"
          when(and(binding['adeSession] isCallable('sicoAdeDispatch))
            code="read_failed"
            payload=car(errset(sicoAdeDispatch(binding method) nil))))))
      when(and(payload strlen(payload)>60000) payload=nil code="result_too_large")
      if(payload then
        sprintf(nil "{\"id\":\"%s\",\"ok\":true,\"result\":%s}\n"
          requestId payload)
      else
        sprintf(nil
          "{\"id\":\"%s\",\"ok\":false,\"code\":\"%s\",\"error\":\"Bound target read failed\"}\n"
          requestId code)))
  )
)

procedure(sicoRequestBody(cid line)
  prog((reply fields target protected payload code)
    fields=parseString(line " ")
    unless(and(stringp(car(fields)) strlen(car(fields))==32
      rexMatchp("^[0-9a-f]+$" car(fields))) return(nil))
    target=sicoBindings[cid]
    when(cadr(fields)=="assistant_call"
      reply=car(errset(sicoAssistantReply(cid fields) nil))
      reply=or(reply sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"assistant_failed\",\"error\":\"Assistant operation failed\"}\n" car(fields)))
      return(reply))
    when(cadr(fields)=="circuit_call"
      reply=sicoCircuitReply(cid fields)
      return(reply))
    when(member(cadr(fields) list("project_session" "project_observe" "project_receipt"))
      reply=sicoProjectReply(cid fields)
      return(reply))
    if(and(length(fields)==7 member(cadr(fields) list("list_windows" "inspect_window" "get_entry_context"))) then
      target=sicoSessionTarget(cid caddr(fields))
      code="stale_context"
      protected=errset(progn(
        unless(target error("Unknown task source"))
        if(or(cadr(fields)=="list_windows" nth(3 fields)!="-") then
          sicoProjectContextJson(target)
          unless(target['projectValid] error("Stale task project"))
        else
          sicoContextJson(target)
          unless(target['valid] error("Stale task source")))
        code="read_failed"
        sicoEntryRead(target cadr(fields) cdddr(fields))) nil)
      payload=when(protected car(protected))
      when(and(payload strlen(payload)>60000) payload=nil code="result_too_large")
      reply=if(payload then
        sprintf(nil "{\"id\":\"%s\",\"ok\":true,\"result\":%s}\n" car(fields) payload)
      else sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"%s\"}\n" car(fields) code))
    else
    when(and(length(fields)==3 isCallable('sicoSessionTarget))
      target=sicoSessionTarget(cid caddr(fields))
      line=strcat(car(fields) " " cadr(fields)))
    reply=if(target then sicoReadReply(target line) else
      sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"stale_context\",\"error\":\"Target expired\"}\n" car(fields))))
    return(reply)))

procedure(sicoRequest(arqCid arqLine)
  prog((arqFields arqProtected arqReply arqCode)
    arqFields=parseString(arqLine " ")
    unless(sicoRequestIdP(car(arqFields))
      sicoTrace(arqCid nil "skill.request_rejected" "invalid_request_id" strlen(arqLine))
      return(nil))
    sicoTrace(arqCid arqFields "skill.request_parsed" nil strlen(arqLine))
    arqProtected=errset(if(isCallable('sicoNativeRequest)
      sicoNativeRequest(arqCid arqLine) sicoRequestBody(arqCid arqLine)) nil)
    arqReply=when(arqProtected car(arqProtected))
    unless(arqReply
      arqCode=if(arqProtected "invalid_request" "request_failed")
      sicoTrace(arqCid arqFields "skill.request_rejected" arqCode)
      arqReply=sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"%s\",\"error\":\"SKILL request did not produce a reply\"}\n"
        car(arqFields) arqCode))
    sicoWriteReply(arqCid arqReply arqFields)
    return(arqReply)))

procedure(sicoDispatch(agdCid agdLine)
  let((agdBinding)
    agdBinding=sicoBindings[agdCid]
    when(agdBinding
      unwindProtect(
        progn(agdBinding['skillActive]=t sicoRequest(agdCid agdLine))
        agdBinding['skillActive]=nil))))

procedure(sicoDataPart(adpCid adpPart adpComplete)
  prog((adpBinding adpText adpSize adpFields)
    adpBinding=sicoBindings[adpCid]
    unless(adpBinding return(nil))
    when(adpBinding['discarding]
      when(adpComplete adpBinding['discarding]=nil)
      return(nil))
    adpText=strcat(or(adpBinding['buffer] "") adpPart) adpSize=strlen(adpText)
    adpBinding['buffer]=""
    when(adpSize>200000
      adpFields=parseString(substring(adpText 1 min(adpSize 256)) " ")
      adpBinding['discarding]=!adpComplete
      sicoTrace(adpCid adpFields "skill.request_overflow" "request_too_large" adpSize)
      when(sicoRequestIdP(car(adpFields))
        sicoWriteReply(adpCid sprintf(nil
          "{\"id\":\"%s\",\"ok\":false,\"code\":\"request_too_large\",\"error\":\"SKILL frame exceeds 200000 bytes\"}\n"
          car(adpFields)) adpFields))
      return(nil))
    unless(adpComplete adpBinding['buffer]=adpText return(nil))
    when(adpText=="" return(nil))
    if(rexMatchp("^@diagnostics " adpText) then
      when(isCallable('sicoConfigureDiagnostics) sicoConfigureDiagnostics(adpCid adpText))
    else if(rexMatchp("^@" adpText) then
      adpBinding['desktopState]=adpText
      if(isCallable('sicoSessionNotice) then sicoSessionNotice(adpCid adpText)
      else when(rexMatchp("^@shown " adpText)
        adpBinding['shownCount]=1+or(adpBinding['shownCount] 0)))
      printf("[AI] %s\n" adpText)
    else sicoDispatch(adpCid adpText)))
    return(t)))

procedure(sicoData(adtCid adtData)
  prog((adtStart adtPosition adtSize)
    unless(and(sicoBindings[adtCid] stringp(adtData)) return(nil))
    adtStart=1 adtSize=strlen(adtData)
    for(adtPosition 1 adtSize
      when(substring(adtData adtPosition 1)=="\n"
        sicoDataPart(adtCid if(adtPosition>adtStart
          substring(adtData adtStart adtPosition-adtStart) "") t)
        adtStart=adtPosition+1))
    when(adtStart<=adtSize sicoDataPart(adtCid substring(adtData adtStart) nil))
    return(t)))

procedure(sicoError(cid data)
  when(data warn("Silicon Copilot Relay: %s" data))
)

procedure(sicoExit(cid status)
  when(status!=0 warn("Silicon Copilot Relay exited with status %L.\n" status))
  when(isCallable('sicoQuickExit) sicoQuickExit(cid))
  sicoBindings[cid]=nil
)

procedure(sicoConnect(command instanceId generation @optional window captured (targetId "bound"))
  prog((binding cid payload snapshot)
    unless(and(stringp(command) stringp(instanceId) stringp(generation)) return(nil))
    unless(or(window captured)
      when(isCallable('hiGetCurrentWindow) window=hiGetCurrentWindow()))
    binding=or(captured makeTable("sicoBinding" nil))
    binding['window]=window binding['buffer]=""
    binding['instanceId]=instanceId binding['generation]=generation
    binding['cwd]=or(binding['cwd] aiWorkingDirectory())
    snapshot=sicoContextJson(binding)
    cid=ipcBeginProcess(command "" 'sicoData 'sicoError 'sicoExit)
    unless(cid return(nil))
    sicoBindings[cid]=binding
    payload=sprintf(nil
      "{\"context\":{\"contract\":\"virtuoso_context.v1\",\"instance_id\":\"%s\",\"generation\":\"%s\",\"target_id\":\"%s\",\"snapshot\":%s}}\n"
      aiJsonEscape(instanceId) aiJsonEscape(generation) aiJsonEscape(targetId) snapshot)
    when(isCallable('sicoNativeRecord)
      sicoNativeRecord(if(and(boundp('sicoInstanceBridge) sicoInstanceBridge)
        sicoInstanceBridge cid) targetId binding snapshot))
    ipcWriteProcess(cid payload)
    return(cid)
  )
)

procedure(sicoDisconnect(cid)
  when(sicoBindings[cid]
    ipcCloseProcess(cid)
    when(ipcIsAliveProcess(cid) ipcKillProcess(cid))
    sicoBindings[cid]=nil)
)

procedure(sicoContextRevision()
  "20260921.sico.context.v1")

sicoContextVersion="20260921.sico.context.v1"
)
