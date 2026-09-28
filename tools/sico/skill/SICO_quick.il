/* PyQt quick composer owned by this Virtuoso through a private SKILL IPC pipe.
 * User text crosses SKILL as bounded base64, never as evaluated source. */

unless(and(boundp('sicoQuickVersion)
           sicoQuickVersion=="20260921.sico.quick.v1")

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

procedure(sicoQuickSend(composer kind @optional message)
  when(and(composer composer['child] ipcIsAliveProcess(composer['child]))
    ipcWriteProcess(composer['child]
      sprintf(nil "{\"kind\":\"%s\",\"message\":\"%s\"}\n"
        kind aiJsonEscape(or(message ""))))))

procedure(sicoQuickAsk(binding)
  prog((composer command child cid)
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
    child=ipcBeginProcess(command "" 'sicoQuickData 'sicoError 'sicoQuickChildExit)
    unless(child return(nil))
    composer['child]=child
    sicoQuickChildren[child]=composer
    sicoQuickWindows[binding['window]]=composer
    ipcWriteProcess(child sprintf(nil
      "{\"kind\":\"open\",\"source\":\"%s\",\"destination\":\"%s\"}\n"
      aiJsonEscape(composer['source])
      aiJsonEscape(if(cid then
        sprintf(nil "会话：%s" or(sicoBindings[cid]['sessionId] "正在启动…"))
        else "Silicon Copilot（提交时创建首个会话）"))))
    return(composer)))

procedure(sicoQuickSubmit(composer encoded)
  prog((binding bridge key snapshot payload sent child command)
    when(or(composer['pending] composer['closing]) return(nil))
    unless(and(stringp(encoded) strlen(encoded)>0 strlen(encoded)<=86000
               rexMatchp("^[A-Za-z0-9+/=]+$" encoded))
      sicoQuickSend(composer "rejected" "输入编码无效。文字已保留。") return(nil))
    binding=composer['target]
    snapshot=car(errset(sicoContextJson(binding) nil))
    unless(and(snapshot binding['valid])
      sicoQuickSend(composer "rejected" "来源已变更或关闭。请复制文字后重新打开 Ask。")
      return(nil))
    bridge=sicoEnsureBridge(binding)
    unless(bridge
      sicoQuickSend(composer "rejected" "无法连接工程服务。文字已保留。")
      return(nil))
    command=sicoLaunchCommand("host-submit")
    unless(command
      sicoQuickSend(composer "rejected" "无法启动宿主输入进程。文字已保留。") return(nil))
    sicoQuickCounter=sicoQuickCounter+1
    key=sprintf(nil "q%d_%d" ipcGetPid() sicoQuickCounter)
    sicoBindings[bridge]['targets][key]=binding
    when(isCallable('sicoNativeRecord)
      sicoNativeRecord(bridge key binding snapshot))
    sicoQuickRequests[key]=composer
    composer['pending]=key
    child=ipcBeginProcess(command ""
      'sicoQuickHostData 'sicoError 'sicoQuickHostExit)
    unless(child
      sicoQuickRejectedHost(key)
      return(nil))
    sicoQuickHostChildren[child]=key
    payload=sprintf(nil
      "{\"kind\":\"submit\",\"id\":\"%s\",\"text_b64\":\"%s\",\"context\":{\"contract\":\"virtuoso_context.v1\",\"instance_id\":\"%s\",\"generation\":\"%s\",\"target_id\":\"%s\",\"snapshot\":%s}}\n"
      key encoded sicoBindings[bridge]['instanceId] sicoBindings[bridge]['generation] key snapshot)
    sent=errset(ipcWriteProcess(child payload) nil)
    unless(and(sent car(sent))
      errset(ipcCloseProcess(child) nil)
      sicoQuickUnknownHost(key))
    return(t)))

procedure(sicoQuickAcceptedHost(key)
  let((composer)
    composer=sicoQuickRequests[key]
    when(and(composer composer['pending]==key)
      composer['pending]=nil composer['closing]=t
      sicoQuickSend(composer "accepted")
      sicoQuickRequests[key]=nil)))

procedure(sicoQuickRejectedHost(key)
  let((composer)
    composer=sicoQuickRequests[key]
    when(and(composer composer['pending]==key)
      composer['pending]=nil
      sicoQuickSend(composer "rejected" "宿主输入未接收，文字已保留。")
      sicoQuickRequests[key]=nil)))

procedure(sicoQuickUnknownHost(key)
  let((composer)
    composer=sicoQuickRequests[key]
    when(and(composer composer['pending]==key)
      composer['pending]=nil
      sicoQuickSend(composer "unknown" sprintf(nil
        "宿主输入 %s 结果未知，请查询原请求后再决定是否继续；文字已保留。" key))
      sicoQuickRequests[key]=nil)))

procedure(sicoQuickWake(key sessionId @optional serviceId runtimeId)
  let((composer cid command)
    composer=sicoQuickRequests[key]
    when(and(composer !composer['closing] composer['pending]==key)
      if(and(serviceId runtimeId) then
        ; A wake opens the accepted runtime, without changing another page's activation.
        command=sicoLaunchCommand("service-gui")
        when(command
          command=strcat(command " --session " sicoQuote(sessionId)
            " --service-id " sicoQuote(serviceId) " --runtime-id " sicoQuote(runtimeId))
          ipcBeginProcess(command "" 'sicoError 'sicoError))
      else
        ; Compat receipts from pre-AS-04 host workers; retire at AS-07.
        cid=composer['session]
        when(and(cid sicoSessionLive(cid))
          ipcWriteProcess(cid "@show\n"))))))

procedure(sicoQuickHostLine(key line)
  let((fields)
    fields=parseString(line " ")
    when(cadr(fields)==key
      cond(
        (and(length(fields)==2 car(fields)=="@accepted") sicoQuickAcceptedHost(key))
        (and(length(fields)==2 car(fields)=="@rejected") sicoQuickRejectedHost(key))
        (and(length(fields)==2 car(fields)=="@unknown") sicoQuickUnknownHost(key))
        (and(length(fields)==3 car(fields)=="@wake"
             rexMatchp("^[a-zA-Z0-9_-]+$" caddr(fields)))
          ; Advisory wake failure must not swallow a receipt in the same batch.
          errset(sicoQuickWake(key caddr(fields)) nil))
        (and(length(fields)==5 car(fields)=="@wake"
             rexMatchp("^[a-zA-Z0-9_-]+$" caddr(fields))
             rexMatchp("^[a-f0-9]+$" nth(3 fields)) strlen(nth(3 fields))==32
             rexMatchp("^[a-f0-9]+$" nth(4 fields)) strlen(nth(4 fields))==32)
          errset(sicoQuickWake(key caddr(fields) nth(3 fields) nth(4 fields)) nil))))))

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
    when(and(key sicoQuickRequests[key])
      sicoQuickUnknownHost(key))
    when(status!=0 warn("Silicon Copilot host submission exited with status %L.\n" status))))

procedure(sicoQuickData(child data)
  let((composer buffer size start position line)
    composer=sicoQuickChildren[child]
    when(composer
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
            when(and(strlen(line)>6 substring(line 1 6)=="@text ")
              sicoQuickSubmit(composer substring(line 7)))
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
      sicoQuickSend(composer "accepted")
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
    when(composer
      window=composer['target]['window]
      when(sicoQuickWindows[window]==composer sicoQuickWindows[window]=nil)
      composer['child]=nil)
    sicoQuickChildren[child]=nil
    when(status!=0 warn("Silicon Copilot quick input exited with status %L.\n" status))))

procedure(sicoQuickRevision()
  "20260921.sico.quick.v1")

sicoQuickVersion="20260921.sico.quick.v1"
)
