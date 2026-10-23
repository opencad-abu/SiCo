; Virtuoso desktop adapter. The existing bindings table is the sole pipe registry;
; no desktop cid or window selection is exposed to project-service admission.
procedure(sicoDesktopRevision() "20261021.sico.desktop.v2")

procedure(sicoDesktopFind(adfBridge)
  prog((adfRow)
    foreach(adfCid sicoBindings
      adfRow=sicoBindings[adfCid]
      when(and(adfRow adfRow['desktopSession] !adfRow['stopping] sicoSessionLive(adfCid)
        adfRow['instanceId]==adfBridge['instanceId]
        adfRow['generation]==adfBridge['generation]
        sicoBindingSamePath(adfRow['cwd] adfBridge['cwd]))
        return(adfCid)))
    return(nil)))

procedure(sicoDesktopAttach(adaCid adaSession @optional adaInput adaSequence)
  unless(and(sicoSessionLive(adaCid)
    ipcWriteProcess(adaCid strcat("@attach " adaSession
      if(adaSequence then strcat(" " adaInput " " adaSequence) else "") "\n")))
    error("Silicon Copilot: desktop %L did not accept attachment to session %s.\n"
      adaCid adaSession))
  t)

procedure(sicoDesktopLaunch(adlSource adlBridge adlTarget adlSession adlService adlRuntime)
  prog((adlCommand adlBinding adlCid)
    adlCommand=sicoLaunchCommand("launch")
    unless(adlCommand error("Silicon Copilot: desktop launcher unavailable.\n"))
    adlCommand=strcat(adlCommand " --session " sicoQuote(adlSession)
      " --service-id " sicoQuote(adlService) " --runtime-id " sicoQuote(adlRuntime))
    adlBinding=makeTable("sicoDesktopBinding" nil)
    foreach(adlField list('window 'cellJson 'adeSession 'testName 'historyName
      'historyHandle 'entry 'cwd 'sharedWindowRef 'sharedWindowCaptured 'sourceRetired)
      adlBinding[adlField]=adlSource[adlField])
    adlBinding['targets]=adlBridge['targets]
    adlCid=sicoConnect(adlCommand adlBridge['instanceId] adlBridge['generation]
      adlBinding['window] adlBinding adlTarget)
    unless(adlCid error("Silicon Copilot: could not start the Ask desktop.\n"))
    sicoBindings[adlCid]['desktopSession]=t
    sicoBindSession(adlBinding['window] adlCid)
    return(adlCid)))

procedure(sicoDesktopWake(adwSource adwBridge adwTarget adwSession adwService adwRuntime
    @optional adwSequence adwHead)
  prog((adwCid)
    foreach(adwId list(adwSession adwService adwRuntime)
      unless(and(stringp(adwId) strlen(adwId)==32 rexMatchp("^[a-f0-9]+$" adwId))
        error("Silicon Copilot: invalid accepted Ask address.\n")))
    unless(and(adwBridge !adwBridge['stopping] boundp('sicoInstanceBridge)
      eq(adwBridge sicoBindings[sicoInstanceBridge]))
      error("Silicon Copilot: captured Ask bridge unavailable.\n"))
    when(and(adwSequence adwHead!=adwTarget) return(nil))
    adwCid=sicoDesktopFind(adwBridge)
    if(adwCid then
      if(adwSequence then sicoDesktopAttach(adwCid adwSession adwTarget adwSequence)
        else sicoDesktopAttach(adwCid adwSession))
      else sicoDesktopLaunch(adwSource adwBridge adwTarget adwSession adwService adwRuntime))
    return(t)))
