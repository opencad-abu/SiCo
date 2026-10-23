/* PyQt quick composer owned by this Virtuoso through a private SKILL IPC pipe.
 * User text crosses SKILL as bounded base64, never as evaluated source. */

unless(and(boundp('sicoQuickVersion)
           sicoQuickVersion=="20261021.sico.quick.v7")

unless(boundp('sicoQuickWindows) sicoQuickWindows=makeTable("sicoQuickWindows" nil))
unless(boundp('sicoQuickChildren) sicoQuickChildren=makeTable("sicoQuickChildren" nil))
unless(boundp('sicoQuickRequests) sicoQuickRequests=makeTable("sicoQuickRequests" nil))
unless(boundp('sicoQuickHostChildren) sicoQuickHostChildren=makeTable("sicoQuickHostChildren" nil))
unless(boundp('sicoQuickHostBuffers) sicoQuickHostBuffers=makeTable("sicoQuickHostBuffers" nil))
unless(boundp('sicoQuickCounter) sicoQuickCounter=0)

procedure(sicoQuickSource(binding)
  let((protected cv label session)
    session=binding['adeSession]
    protected=errset(
      if(session then strcat(axlGetSessionLibName(session) " / "
          axlGetSessionCellName(session) " / " axlGetSessionViewName(session))
      else
        cv=geGetWindowCellView(binding['window])
        when(cv strcat(cv->libName " / " cv->cellName " / " cv->viewName))) nil)
    label=or(when(protected car(protected)) "Virtuoso")
    when(binding['testName] label=strcat(label " / Test: " binding['testName]))
    when(binding['historyName] label=strcat(label " / " binding['historyName]))
    label))

procedure(sicoQuickPointP(value)
  and(listp(value) length(value)==2
       numberp(car(value)) numberp(cadr(value))))

procedure(sicoQuickAnchor(binding)
  prog((window pointer bounds screen lower upper)
    window=when(binding binding['window])
    bounds=when(window car(errset(hiGetAbsWindowScreenBBox(window) nil)))
    screen=car(errset(hiGetMaxScreenCoords() nil))
    when(and(sicoQuickPointP(screen)
             listp(bounds) length(bounds)==2
             sicoQuickPointP(car(bounds)) sicoQuickPointP(cadr(bounds)))
      lower=car(bounds) upper=cadr(bounds)
      pointer=car(errset(hiGetScreenPoint(window) nil))
      when(sicoQuickPointP(pointer)
        return(list("pointer"
          car(lower)+car(pointer)
          cadr(screen)-cadr(upper)+cadr(pointer))))
      return(list("window"
        fix((car(lower)+car(upper))/2)
        cadr(screen)-fix((cadr(lower)+cadr(upper))/2))))
    nil))

procedure(sicoQuickAnchorJson(binding)
  let((anchor)
    anchor=sicoQuickAnchor(binding)
    if(anchor then
      sprintf(nil "{\"kind\":\"%s\",\"x\":%d,\"y\":%d}"
        car(anchor) cadr(anchor) caddr(anchor))
      else "null")))

procedure(sicoQuickSend(composer kind @optional message)
  when(and(composer composer['child] ipcIsAliveProcess(composer['child]))
    ipcWriteProcess(composer['child]
      sprintf(nil "{\"kind\":\"%s\",\"message\":\"%s\"}\n"
        kind aiJsonEscape(or(message ""))))))

procedure(sicoQuickAsk(binding)
  prog((composer command child cid anchor recovered windowRef contextJson)
    recovered=sicoAskRestoreUnknown()
    when(recovered return(recovered))
    when(and(boundp('sicoLaunchWorkingDir) sicoLaunchWorkingDir !binding['cwd])
      binding['cwd]=sicoLaunchWorkingDir)
    anchor=sicoQuickAnchorJson(binding)
    composer=sicoQuickWindows[binding['window]]
    when(and(composer !composer['closing] ipcIsAliveProcess(composer['child]))
      sicoQuickSend(composer "show") return(composer))
    command=sicoLaunchCommand("quick-input")
    unless(command return(nil))
    composer=makeTable("sicoQuickComposer" nil)
    composer['target]=binding
    composer['previousSession]=sicoWindowSessions[binding['window]]
    cid=sicoSessionForWindow(binding['window])
    composer['session]=cid
    composer['source]=sicoQuickSource(binding)
    windowRef=if(isCallable('aiWindowCapture)
      or(car(errset(aiWindowCapture(binding['window]) nil)) "") "")
    contextJson=if(isCallable('sicoContextJson)
      or(car(errset(sicoContextJson(binding) nil)) "null") "null")
    child=ipcBeginProcess(command "" 'sicoQuickData 'sicoError 'sicoQuickChildExit)
    unless(child return(nil))
    composer['child]=child
    sicoQuickChildren[child]=composer
    sicoQuickWindows[binding['window]]=composer
    ipcWriteProcess(child sprintf(nil
      "{\"kind\":\"open\",\"source\":\"%s\",\"destination\":\"%s\",\"screen_point\":%s,\"window_ref\":\"%s\",\"context\":%s}\n"
      aiJsonEscape(composer['source])
      aiJsonEscape(if(cid then
        sprintf(nil "会话：%s" or(sicoBindings[cid]['sessionId] "正在启动…"))
        else "Silicon Copilot（提交时创建首个会话）"))
      anchor aiJsonEscape(windowRef) contextJson))
    return(composer)))

procedure(sicoQuickObjects(composer request)
  prog((fields op id ref kind offset limit query value)
    fields=parseString(request " ")
    when(!fields return(nil))
    op=car(fields) id=cadr(fields)
    unless(and(stringp(id) rexMatchp("^[A-Za-z0-9_-]+$" id))
      return(nil))
    cond(
      (and(op=="selection" length(fields)==5)
        ref=nth(2 fields) offset=atoi(nth(3 fields)) limit=atoi(nth(4 fields))
        value=if(and(isCallable('aiSelectionInspect)
                     fixp(offset) offset>=0 offset<=4096
                     fixp(limit) limit>=1 limit<=50)
          aiSelectionInspect(ref offset limit)
          aiPdkError("invalid_arguments" "Invalid Ask selection request")))
      (and(op=="objects" length(fields)==7)
        ref=nth(2 fields) kind=nth(3 fields)
        offset=atoi(nth(4 fields)) limit=atoi(nth(5 fields))
        query=if(isCallable('aiCopilotHex) aiCopilotHex(nth(6 fields)) "")
        value=if(and(isCallable('aiSelectionList)
                     fixp(offset) offset>=0 offset<=4096
                     fixp(limit) limit>=1 limit<=50)
          aiSelectionList(ref kind query offset limit)
          aiPdkError("invalid_arguments" "Invalid Ask object request")))
      (and(op=="windows" length(fields)==4)
        offset=atoi(nth(2 fields)) limit=atoi(nth(3 fields))
        value=if(and(isCallable('aiWindowList)
                     fixp(offset) offset>=0 offset<=4096
                     fixp(limit) limit>=1 limit<=200)
          aiWindowList(offset limit)
          aiPdkError("invalid_arguments" "Invalid Ask window request")))
      (t value=aiPdkError("invalid_arguments" "Invalid Ask object operation")))
    when(stringp(value)
      sicoQuickSend(composer "objects"
        sprintf(nil "{\"id\":\"%s\",\"value\":%s}" id value)))))

procedure(sicoQuickSubmit(composer encoded)
  prog((binding bridge key snapshot payload command)
    when(or(composer['pending] composer['closing]) return(nil))
    unless(and(stringp(encoded) strlen(encoded)>0 strlen(encoded)<=86000
               rexMatchp("^[A-Za-z0-9+/=]+$" encoded))
      sicoQuickSend(composer "rejected" "输入编码无效。文字已保留。") return(nil))
    binding=composer['target]
    snapshot=car(errset(sicoContextJson(binding) nil))
    unless(and(snapshot binding['valid])
      sicoQuickSend(composer "rejected"
        if(binding['contextCode]=="context_unavailable"
          "来源暂时无法核验，文字已保留；请稍后重新提交。"
          "来源已变更或关闭。请复制文字后重新打开 Ask。"))
      return(nil))
    bridge=sicoEnsureBridge(binding)
    unless(bridge
      sicoQuickSend(composer "rejected" "无法连接工程服务。文字已保留。")
      return(nil))
    command=sicoLaunchCommand("host-submit")
    unless(command
      sicoQuickSend(composer "rejected" "无法启动宿主输入进程。文字已保留。") return(nil))
    unless(sicoAskAvailable()
      sicoQuickSend(composer "rejected" "queue_full：Ask 提交队列已满，文字已保留。")
      return(nil))
    when(sicoQuickCounter>=2147483647
      sicoQuickSend(composer "rejected" "Ask 序号已用尽，请保留草稿并重新启动实例。")
      return(nil))
    sicoQuickCounter=sicoQuickCounter+1
    key=sprintf(nil "q%d_%d" ipcGetPid() sicoQuickCounter)
    sicoBindings[bridge]['targets][key]=binding
    when(isCallable('sicoNativeRecord)
      sicoNativeRecord(bridge key binding snapshot))
    sicoQuickRequests[key]=composer
    composer['pending]=key composer['bridge]=sicoBindings[bridge]
    payload=sprintf(nil
      "{\"kind\":\"submit\",\"id\":\"%s\",\"text_b64\":\"%s\",\"ask_sequence\":%d,\"context\":{\"contract\":\"virtuoso_context.v1\",\"instance_id\":\"%s\",\"generation\":\"%s\",\"target_id\":\"%s\",\"snapshot\":%s}}"
      key encoded sicoQuickCounter sicoBindings[bridge]['instanceId] sicoBindings[bridge]['generation] key snapshot)
    return(sicoAskEnqueue(key payload command))))

procedure(sicoQuickAcceptedHost(key)
  let((composer)
    composer=sicoQuickRequests[key]
    when(and(composer composer['pending]==key)
      composer['pending]=nil composer['closing]=t
      errset(sicoQuickSend(composer "accepted") t)
      sicoQuickRequests[key]=nil
      sicoAskSettled(key))))

procedure(sicoQuickRejectedHost(key @optional code)
  let((composer)
    composer=sicoQuickRequests[key]
    when(and(composer composer['pending]==key)
      composer['pending]=nil composer['unknown]=nil
      errset(sicoQuickSend(composer "rejected"
        if(code=="queue_full" then "queue_full：Ask 队列已满或原请求待核对，文字已保留。"
          else "宿主输入未接收，文字已保留。")) t)
      sicoQuickRequests[key]=nil
      sicoAskSettled(key))))

procedure(sicoQuickUnknownHost(key)
  let((composer)
    composer=sicoQuickRequests[key]
    when(and(composer composer['pending]==key)
      composer['unknown]=t
      unless(sicoAskFlight==key
        composer['pending]=nil sicoQuickRequests[key]=nil)
      errset(sicoQuickSend(composer "unknown" sprintf(nil
        "宿主输入 %s 结果未知，请查询原请求后再决定是否继续；文字已保留。" key)) t)
      )))

procedure(sicoQuickWake(key sessionId @optional serviceId runtimeId sequence head)
  let((composer cid)
    composer=sicoQuickRequests[key]
    when(and(composer !composer['closing] composer['pending]==key)
      if(and(serviceId runtimeId) then
        if(sequence then
          sicoDesktopWake(composer['target] composer['bridge]
            key sessionId serviceId runtimeId sequence head)
        else sicoDesktopWake(composer['target] composer['bridge]
          key sessionId serviceId runtimeId))
      else
        ; Compat receipts from pre-AS-04 workers; retire at AS-07.
        cid=composer['session]
        when(and(cid sicoSessionLive(cid)) ipcWriteProcess(cid "@show\n"))))))

procedure(sicoQuickHostLine(key line)
  let((fields)
    fields=parseString(line " ")
    when(cadr(fields)==key
      cond(
        (and(length(fields)==2 car(fields)=="@accepted") sicoQuickAcceptedHost(key))
        (and(length(fields)==2 car(fields)=="@rejected") sicoQuickRejectedHost(key))
        (and(length(fields)==3 car(fields)=="@rejected"
             rexMatchp("^[a-z_]+$" caddr(fields)))
          sicoQuickRejectedHost(key caddr(fields)))
        (and(length(fields)==2 car(fields)=="@unknown") sicoQuickUnknownHost(key))
        (and(length(fields)==3 car(fields)=="@wake"
             rexMatchp("^[a-zA-Z0-9_-]+$" caddr(fields)))
          ; Report wake errors, but still consume the durable receipt in this batch.
          errset(sicoQuickWake(key caddr(fields)) t))
        (and(length(fields)==7 car(fields)=="@wake"
             rexMatchp("^[a-zA-Z0-9_-]+$" caddr(fields))
             rexMatchp("^[a-f0-9]+$" nth(3 fields)) strlen(nth(3 fields))==32
             rexMatchp("^[a-f0-9]+$" nth(4 fields)) strlen(nth(4 fields))==32
             rexMatchp("^[0-9]+$" nth(5 fields))
             rexMatchp("^[a-zA-Z0-9_-]+$" nth(6 fields)))
          errset(sicoQuickWake(key caddr(fields) nth(3 fields) nth(4 fields)
            nth(5 fields) nth(6 fields)) t))
        (and(length(fields)==5 car(fields)=="@wake"
             rexMatchp("^[a-zA-Z0-9_-]+$" caddr(fields))
             rexMatchp("^[a-f0-9]+$" nth(3 fields)) strlen(nth(3 fields))==32
             rexMatchp("^[a-f0-9]+$" nth(4 fields)) strlen(nth(4 fields))==32)
          errset(sicoQuickWake(key caddr(fields) nth(3 fields) nth(4 fields)) t))))))

procedure(sicoQuickHostData(child data)
  prog((key buffer size start position line)
    key=sicoQuickHostChildren[child]
    when(and(key stringp(data))
      buffer=strcat(or(sicoQuickHostBuffers[child] "") data)
      size=strlen(buffer) start=1
      when(size>4096
        sicoQuickHostChildren[child]=nil sicoQuickHostBuffers[child]=nil
        sicoQuickUnknownHost(key) return(nil))
      for(position 1 size
        when(substring(buffer position 1)=="\n"
          when(position>start
            line=substring(buffer start position-start)
            sicoQuickHostLine(key line))
          start=position+1))
      sicoQuickHostBuffers[child]=if(start<=size then substring(buffer start) else ""))))

procedure(sicoQuickHostExit(child status)
  let((key)
    key=sicoQuickHostChildren[child]
    sicoQuickHostChildren[child]=nil
    sicoQuickHostBuffers[child]=nil
    /* No line is a receipt.  An exited worker leaves the durable outcome
     * unknown so the original input ID can be queried before any retry. */
    when(and(key sicoQuickRequests[key]
             or(!sicoQuickRequests[key]['queryChild]
                sicoQuickRequests[key]['queryChild]==child))
      sicoQuickRequests[key]['queryChild]=nil
      sicoQuickUnknownHost(key))
    when(status!=0 warn("Silicon Copilot host submission exited with status %L.\n" status))))

procedure(sicoQuickData(child data)
  let((composer buffer size start position line)
    composer=sicoQuickChildren[child]
    when(and(composer composer['child]==child)
      buffer=strcat(or(composer['buffer] "") or(data ""))
      size=strlen(buffer) start=1
      if(size>90000 then
        composer['buffer]=""
        sicoQuickSend(composer "rejected" "输入超过传输上限。文字已保留。")
      else
        for(position 1 size
          when(substring(buffer position 1)=="\n"
            line=substring(buffer start position-start)
            when(line=="@closed" composer['closing]=t)
            when(and(line=="@query" composer['pending])
              sicoAskQuery(composer['pending]))
            when(and(strlen(line)>6 substring(line 1 6)=="@text ")
              sicoQuickSubmit(composer substring(line 7)))
            when(and(strlen(line)>9 substring(line 1 9)=="@objects ")
              sicoQuickObjects(composer substring(line 10)))
            start=position+1))
        composer['buffer]=if(start<=size then substring(buffer start) else "")))))

; Legacy desktop receipts only serve submissions already pending before v4 reload.
; Retire with AS-07 once no old desktop-owned request can remain in this process.
procedure(sicoQuickHostPending(key)
  exists(child sicoQuickHostChildren sicoQuickHostChildren[child]==key))

procedure(sicoQuickAccepted(cid key)
  let((composer)
    composer=sicoQuickRequests[key]
    when(and(composer !sicoQuickHostPending(key)
             composer['session]==cid composer['pending]==key)
      /* A late acceptance cannot overwrite the user's subsequent New session. */
      when(or(sicoWindowSessions[composer['target]['window]]==composer['previousSession]
              sicoWindowSessions[composer['target]['window]]==cid)
        sicoBindSession(composer['target]['window] cid))
      composer['pending]=nil composer['closing]=t
      errset(sicoQuickSend(composer "accepted") t)
      sicoQuickRequests[key]=nil)))

procedure(sicoQuickRejected(cid key)
  prog((composer)
    when(sicoQuickHostPending(key) return(nil))
    composer=sicoQuickRequests[key]
    when(and(composer composer['session]==cid composer['pending]==key)
      composer['pending]=nil
      sicoQuickSend(composer "rejected" "会话未接收该请求。文字已保留。")
      sicoQuickRequests[key]=nil)
    when(and(sicoBindings[cid] sicoBindings[cid]['targets])
      sicoBindings[cid]['targets][key]=nil)))

procedure(sicoQuickExit(cid)
  foreach(key sicoQuickRequests
    when(and(sicoQuickRequests[key] sicoQuickRequests[key]['session]==cid)
      sicoQuickRejected(cid key))))

procedure(sicoQuickChildExit(child status)
  let((composer window)
    composer=sicoQuickChildren[child]
    when(and(composer composer['child]==child)
      window=composer['target]['window]
      when(sicoQuickWindows[window]==composer sicoQuickWindows[window]=nil)
      composer['child]=nil)
    sicoQuickChildren[child]=nil
    when(status!=0 warn("Silicon Copilot quick input exited with status %L.\n" status))))

procedure(sicoQuickRevision()
  "20261021.sico.quick.v7")

sicoQuickVersion="20261021.sico.quick.v7"
)
