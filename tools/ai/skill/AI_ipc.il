; ipcBeginProcess bridge. Virtuoso callbacks execute on the main SKILL thread.

unless(and(boundp('aiIpcVersion)
             aiIpcVersion=="20260911.ai.ipc.v13"
             boundp('aiIpcState)
             aiIpcState['exitCleanupRegistered]
             or(!aiIpcState['cid]
                 member(aiIpcState['controllerProtocol]
                        list("control.pending" "control.v2"))))

unless(boundp('aiIpcState)
  aiIpcState=makeTable("aiIpcState" nil))

procedure(aiIpcClearLiveState()
  when(isCallable('aiLiveModelReset)
    errset(aiLiveModelReset() nil))
  t
)
procedure(aiIpcTerminateProcess(cid)
  let(((terminated t))
    when(and(cid ipcIsAliveProcess(cid))
      errset(ipcSignalProcess(cid 'TERM) nil)
      errset(ipcCloseProcess(cid) nil)
      ipcWait(cid 30 2)
      when(ipcIsAliveProcess(cid)
        errset(ipcKillProcess(cid) nil)
        ipcWait(cid 30 2))
      when(ipcIsAliveProcess(cid)
        terminated=nil
        warn("AI Assistant controller did not terminate; a replacement session will not start yet.\n")))
    terminated
  )
)
; A pre-control-pipe controller cannot honor owner EOF shutdown after a reload.
when(and(aiIpcState['cid]
           !member(aiIpcState['controllerProtocol]
                   list("control.pending" "control.v2")))
  let((legacyCid)
    legacyCid=aiIpcState['cid]
    if(aiIpcTerminateProcess(legacyCid) then
      aiIpcState['cid]=nil
      aiIpcState['buffer]=""
      aiIpcState['queue]=nil
      aiIpcState['busy]=nil
      aiIpcState['responded]=makeTable("aiIpcResponded" nil)
      aiIpcState['workspace]=nil
      aiIpcState['agent]=nil
      aiIpcState['phase]="idle"
      aiIpcState['controllerProtocol]=nil
    else
      aiIpcState['phase]="retiring"
      aiIpcState['controllerProtocol]="control.retiring")))
procedure(aiIpcWrite(cid payload)
  if(ipcWriteProcess(cid strcat(payload "\n")) then
    t
  else
    warn("AI Assistant bridge could not write to controller %L.\n" cid)
    nil)
)

procedure(aiIpcRespond(cid id ok outputPath message)
  unless(aiIpcState['responded][id]
    aiIpcState['responded][id]=t
    if(ok then
      aiIpcWrite(cid sprintf(nil
        "{\"id\":\"%s\",\"ok\":true,\"result\":{\"result_path\":\"%s\"}}"
        aiJsonEscape(id) aiJsonEscape(outputPath)))
    else
      aiIpcWrite(cid sprintf(nil
        "{\"id\":\"%s\",\"ok\":false,\"error\":{\"code\":\"skill_error\",\"message\":\"%s\"}}"
        aiJsonEscape(id) aiJsonEscape(message))))
    aiIpcState['responded]=makeTable("aiIpcResponded" nil)
  )
)

procedure(aiIpcExecute(cid request)
  let((id method source output ok protected diagnostic)
    id=request['id]
    method=request['method]
    source=request['sourcePath]
    output=request['outputPath]
    protected=errset(case(method
      ("eval_skill" aiEvalExpression(source output))
      ("eval_skill_native" aiEvalNativeExpression(source output))
      ("load_skill_file" aiLoadSkillFile(source output))
      ("snapshot_schematic" aiEvalExpression(source output))
      ("capture_circuit_template" aiEvalExpression(source output))
      ("get_context" aiGetContext(output))
      (t nil)) nil)
    ok=and(protected car(protected))
    unless(protected diagnostic=sprintf(nil "SKILL request failed: %L" errset.errset))
    aiIpcRespond(cid id ok output
      or(diagnostic case(method
        ("eval_skill" "SKILL expression failed; inspect the returned error")
        ("eval_skill_native" "Native SKILL expression failed; inspect CIW")
        ("load_skill_file" "SKILL file load failed; inspect the returned error")
        ("snapshot_schematic" "schematic snapshot failed; inspect the returned error")
        ("capture_circuit_template" "circuit template capture failed; inspect the returned error")
        ("get_context" "Virtuoso context collection failed")
        (t "unsupported method"))))
  )
)

procedure(aiIpcDrainQueue(cid)
  unless(aiIpcState['busy]
    aiIpcState['busy]=t
    unwindProtect(
      while(aiIpcState['queue]
        let((request)
          request=car(aiIpcState['queue])
          aiIpcState['queue]=cdr(aiIpcState['queue])
          aiIpcExecute(cid request)))
      aiIpcState['busy]=nil))
)

procedure(aiIpcHandleLine(cid line)
  let((parsed id)
    cond(
      (index(line "\"event\":\"session.ready\"")
        when(and(aiIpcState['phase]=="starting"
                   aiIpcState['controllerProtocol]=="control.pending")
          if(index(line "\"control_protocol\":\"control.v2\"") then
            aiIpcState['controllerProtocol]="control.v2"
            aiIpcState['phase]="running"
          else
            warn("AI Assistant controller does not support persistent session control; check the installed Python entry.\n")
            aiIpcRetire(cid))))
      (index(line "\"event\":\"session.exit\"")
        aiIpcState['phase]="exited"
        aiIpcClearLiveState())
      ((strlen(line)>65536)
        warn("AI Assistant bridge discarded an overlong JSONL message.\n"))
      (index(line "\"event\":\"live_model.status\"")
        if(isCallable('aiLiveModelHandleStatus) then
          unless(aiLiveModelHandleStatus(line)
            warn("AI Assistant bridge rejected live-model status.\n"))
        else
          warn("AI Assistant live-model handler is unavailable.\n")))
      (t
        parsed=aiJsonParseRequest(line)
        id=nth(4 parsed)
        if(car(parsed) then
          aiIpcState['queue]=append1(aiIpcState['queue] cadr(parsed))
          aiIpcDrainQueue(cid)
        else
          if(id then
            aiIpcWrite(cid sprintf(nil
              "{\"id\":\"%s\",\"ok\":false,\"error\":{\"code\":\"invalid_request\",\"message\":\"%s\"}}"
              aiJsonEscape(id) aiJsonEscape(nth(3 parsed))))
          else
            warn("AI Assistant bridge rejected malformed JSONL: %s\n" nth(3 parsed))))))
  )
)

procedure(aiIpcDataHandler(cid data)
  let((buffer length start index line)
    when(or(cid==aiIpcState['cid]
              and(!aiIpcState['cid] aiIpcState['phase]=="starting"))
      buffer=strcat(or(aiIpcState['buffer] "") or(data ""))
      length=strlen(buffer)
      start=1
      for(index 1 length
        when(substring(buffer index 1)=="\n"
          line=substring(buffer start index-start)
          when(strlen(line)>0 aiIpcHandleLine(cid line))
          start=index+1))
      aiIpcState['buffer]=if(start<=length then
        substring(buffer start length-start+1) else "")
      when(strlen(aiIpcState['buffer])>65536
        aiIpcState['buffer]=""
        warn("AI Assistant bridge discarded an unterminated JSONL message.\n")))
  )
)

procedure(aiIpcErrHandler(cid data)
  when(and(data strlen(data)>0
             or(cid==aiIpcState['cid]
                 and(!aiIpcState['cid] aiIpcState['phase]=="starting")))
    warn("AI Assistant session: %s" data))
)

procedure(aiIpcReset(cid)
  when(or(!cid cid==aiIpcState['cid])
    aiIpcState['cid]=nil
    aiIpcState['buffer]=""
    aiIpcState['queue]=nil
    aiIpcState['busy]=nil
    aiIpcState['responded]=makeTable("aiIpcResponded" nil)
    aiIpcState['workspace]=nil
    aiIpcState['agent]=nil
    aiIpcState['phase]="idle"
    aiIpcState['controllerProtocol]=nil
    aiIpcClearLiveState())
)

procedure(aiIpcRetire(cid)
  when(and(cid cid==aiIpcState['cid])
    aiIpcState['phase]="retiring"
    aiIpcState['controllerProtocol]="control.retiring"
    when(aiIpcTerminateProcess(cid)
      aiIpcReset(cid)))
  !aiIpcState['cid]
)

procedure(aiIpcPostFunc(cid exitStatus)
  when(cid==aiIpcState['cid]
    aiIpcReset(cid)
    info("<INFO> AI Assistant terminal exited with status %L.\n" exitStatus))
)

procedure(aiStop()
  let((cid)
    cid=aiIpcState['cid]
    when(cid
      aiIpcState['phase]="stopping"
      if(ipcIsAliveProcess(cid) then
        if(aiIpcState['controllerProtocol]=="control.v2" then
          unless(car(errset(ipcCloseProcess(cid) nil)) aiIpcRetire(cid))
        else
          aiIpcRetire(cid))
      else
        aiIpcReset(cid)))
    cid
  )
)

procedure(aiIpcSessionAliveP()
  let((cid)
    cid=aiIpcState['cid]
    if(and(cid member(aiIpcState['controllerProtocol]
                        list("control.pending" "control.v2"))
             ipcIsAliveProcess(cid)) then
      t
    else
      when(cid aiIpcRetire(cid))
      nil)
  )
)

procedure(aiIpcActivate()
  let((cid)
    when(aiIpcSessionAliveP()
      cid=aiIpcState['cid]
      if(aiIpcWrite(cid "{\"event\":\"terminal.show\"}") then
        cid
      else
        aiIpcRetire(cid)
        nil))
  )
)

procedure(aiExitCleanup()
  when(and(boundp('cadAiLaunchForm) cadAiLaunchForm
             hiIsFormDisplayed(cadAiLaunchForm))
    errset(hiFormClose(cadAiLaunchForm) t))
  errset(aiStop() t)
  t
)

procedure(aiIpcRegisterExitCleanup()
  unless(aiIpcState['exitCleanupRegistered]
    regExitBefore('aiExitCleanup)
    aiIpcState['exitCleanupRegistered]=t)
  aiIpcState['exitCleanupRegistered]
)

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  aiIpcRegisterExitCleanup())

procedure(aiIpcReadyTimeout(generation)
  let((cid)
    cid=aiIpcState['cid]
    when(and(cid generation==aiIpcState['generation]
               aiIpcState['controllerProtocol]=="control.pending")
      warn("AI Assistant controller did not confirm persistent session control.\n")
      aiIpcRetire(cid))
    t
  )
)

procedure(aiIpcStart(command workspace @optional entryRef entryCaptured)
  prog((cid generation)
    when(and(aiIpcState['cid]
               member(aiIpcState['phase] list("stopping" "retiring")))
      cid=aiIpcState['cid]
      if(ipcIsAliveProcess(cid) then
        when(aiIpcState['phase]=="retiring" aiIpcRetire(cid))
      else
        aiIpcReset(cid))
      when(aiIpcState['cid]
        warn("AI Assistant previous session is still stopping; try again shortly.\n")
        return(nil)))
    when(aiIpcSessionAliveP()
      return(aiIpcActivate()))
    when(aiIpcState['cid]
      warn("AI Assistant previous session is still retiring; try again shortly.\n")
      return(nil))
    aiIpcState['buffer]=""
    aiIpcState['queue]=nil
    aiIpcState['busy]=nil
    aiIpcState['responded]=makeTable("aiIpcResponded" nil)
    aiIpcState['workspace]=workspace
    aiIpcState['entryWindowRef]=when(isCallable('aiWindowCapture)
      if(entryCaptured
        then entryRef
        else aiWindowCapture(aiWindowTry('hiGetCurrentWindow nil))))
    aiIpcState['phase]="starting"
    aiIpcState['controllerProtocol]="control.pending"
    generation=or(aiIpcState['generation] 0)+1
    aiIpcState['generation]=generation
    cid=ipcBeginProcess(command "" 'aiIpcDataHandler
      'aiIpcErrHandler 'aiIpcPostFunc "")
    unless(cid
      warn("Virtuoso could not start the AI Assistant session controller.\n")
      aiIpcReset(nil)
      return(nil))
    aiIpcState['cid]=cid
    when(isCallable('hiRegTimer)
      hiRegTimer(sprintf(nil "aiIpcReadyTimeout(%d)" generation)
        if(isCallable('aiControllerReadyDelay) then aiControllerReadyDelay() else 50)))
    return(cid)
  )
)
procedure(aiIpcRevision()
  "20260911.ai.ipc.v13"
)

aiIpcVersion="20260911.ai.ipc.v13"
)
