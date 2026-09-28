;------------------------------------------------------------------------------
; PyQt5 LSF Load Monitor launcher and verified flow selector handoff.
;------------------------------------------------------------------------------

unless(and(boundp('sicoLsfMonitorVersion)
             sicoLsfMonitorVersion=="20260924.sico.monitor.environment.v3")

unless(boundp('cadLsfMonitorProcesses)
  cadLsfMonitorProcesses=makeTable("cadLsfMonitorProcesses" nil)
)

unless(boundp('cadLsfMonitorExitCleanupRegistered)
  cadLsfMonitorExitCleanupRegistered=nil
)

unless(boundp('cadLsfMonitorIconCache)
  cadLsfMonitorIconCache=nil
)

unless(boundp('cadLsfMonitorIconCachePath)
  cadLsfMonitorIconCachePath=""
)

procedure(SICO_lsfMonitorRevision()
  "20260924.sico.monitor.environment.v3"
)

procedure(SICO_lsfMonitorButtonIcon()
  let((path)
    path=SICO_iconPath(SICO_installationRoot() "actions" "monitor.png")
    when(and(path isFile(path))
      unless(and(cadLsfMonitorIconCache
                 cadLsfMonitorIconCachePath==path
                 hiIsIcon(cadLsfMonitorIconCache))
        cadLsfMonitorIconCachePath=path
        cadLsfMonitorIconCache=hiLoadIconFile(path 20 20))
      cadLsfMonitorIconCache)
  )
)

procedure(SICO_lsfMonitorDataHandler(cid data)
  let((state)
    state=cadLsfMonitorProcesses[cid]
    when(state state['output]=strcat(or(state['output] "") data))
  )
)

procedure(SICO_lsfMonitorErrHandler(cid data)
  let((state)
    state=cadLsfMonitorProcesses[cid]
    when(state state['errors]=strcat(or(state['errors] "") data))
    when(and(data strlen(data)>0)
      warn("LSF Load Monitor: %s" data))
  )
)

procedure(SICO_lsfMonitorReadSelection(path)
  let((document host hostCount queue queueCount record valid)
    document=SICO_lsfProtocolRecords(
      path list("QUEUE" "HOST") list("applied"))
    valid=car(document)
    queueCount=0
    hostCount=0
    foreach(record cadr(document)
      case(car(record)
        ("QUEUE"
          queueCount=queueCount+1
          queue=cadr(record))
        ("HOST"
          hostCount=hostCount+1
          host=cadr(record))))
    when(or(queueCount!=1 hostCount>1)
      valid=nil)
    list(valid queue host)
  )
)

procedure(SICO_lsfMonitorPostFunc(cid exitStatus)
  let((form result state validRequest)
    state=cadLsfMonitorProcesses[cid]
    remove(cid cadLsfMonitorProcesses)
    when(state
      if(state['mode]=='selector then
        progn(
          form=state['form]
          result=SICO_lsfMonitorReadSelection(state['resultFile])
          SICO_lsfDeleteFile(state['resultFile])
          validRequest=and(!state['cancelled]
            state['cid]==cid
            SICO_lsfFormValidP(form)
            get(form 'cadLsfMonitorCid)==cid
            get(form 'cadLsfMonitorRequestToken)==state['token]
            form~>runType~>value=="LSF Farm")
          when(validRequest
            putpropq(form nil cadLsfMonitorCid)
            when(and(exitStatus==0 car(result))
              SICO_lsfSetPreferredValues(form cadr(result) caddr(result))
              SICO_lsfStartQueueDiscovery(form)))
        )
      else
        if(exitStatus==0 then
          info("<INFO> LSF Load Monitor exited normally: %s\n"
               or(state['initialQueue] ""))
        else
          unless(state['cancelled]
            warn("LSF Load Monitor exited with status %L: %s\n"
                 exitStatus or(state['initialQueue] ""))))
      )
    )
  )
)

procedure(SICO_lsfMonitorCancelCid(cid)
  let((form state)
    state=when(cid cadLsfMonitorProcesses[cid])
    when(state
      state['cancelled]=t
      form=state['form]
      when(ipcIsAliveProcess(cid)
        errset(ipcKillProcess(cid) nil))
      SICO_lsfDeleteFile(state['resultFile])
      when(and(SICO_lsfFormValidP(form) get(form 'cadLsfMonitorCid)==cid)
        putpropq(form nil cadLsfMonitorCid)))
    nil
  )
)

procedure(SICO_lsfMonitorCancelForm(form)
  when(SICO_lsfFormValidP(form)
    SICO_lsfMonitorCancelCid(get(form 'cadLsfMonitorCid))
    putpropq(form nil cadLsfMonitorCid)
    putpropq(form add1(or(get(form 'cadLsfMonitorRequestToken) 0))
      cadLsfMonitorRequestToken))
  t
)

procedure(SICO_lsfMonitorStopAll()
  let((cid cids state)
    foreach(cid cadLsfMonitorProcesses cids=cons(cid cids))
    foreach(cid cids
      state=cadLsfMonitorProcesses[cid]
      when(and(state state['form] SICO_lsfFormValidP(state['form]))
        putpropq(state['form] nil cadLsfMonitorCid)
        putpropq(state['form]
          add1(or(get(state['form] 'cadLsfMonitorRequestToken) 0))
          cadLsfMonitorRequestToken))
      SICO_lsfMonitorCancelCid(cid))
    nil
  )
)

procedure(SICO_lsfMonitorRegisterExitCleanup()
  unless(cadLsfMonitorExitCleanupRegistered
    regExitBefore('SICO_lsfMonitorStopAll)
    cadLsfMonitorExitCleanupRegistered=t)
  cadLsfMonitorExitCleanupRegistered
)

procedure(SICO_lsfMonitorLaunchCommand(@optional queue outputPath)
  prog((args bkill entry parentPid python)
    unless(and(isCallable('SICO_guiPythonLaunchCommand)
               isCallable('SICO_lsfPythonExe)
               isCallable('SICO_lsfDiscoveryScript))
      warn("LSF Load Monitor shared dependencies are not loaded.\n")
      return(nil))
    parentPid=ipcGetPid()
    unless(and(integerp(parentPid) parentPid>1)
      warn("Cannot determine the owning Virtuoso process ID.\n")
      return(nil))
    python=SICO_lsfPythonExe()
    entry=SICO_lsfDiscoveryScript()
    unless(and(python entry isFile(entry))
      warn("LSF Load Monitor Python entry is unavailable.\n")
      return(nil))
    args=SICO_backendCommand("sico-lsf" python entry)
    bkill=SICO_envValue("SICO_LSF_BKILL")
    when(and(stringp(bkill) strlen(bkill)>0)
      args=strcat(args " --bkill " SICO_shellQuote(bkill)))
    args=strcat(args
      " monitor --parent-pid " sprintf(nil "%d" parentPid))
    when(and(stringp(queue) strlen(queue)>0)
      args=strcat(args " --queue " SICO_shellQuote(queue)))
    when(and(stringp(outputPath) strlen(outputPath)>0)
      args=strcat(args " --output " SICO_shellQuote(outputPath)))
    return(SICO_guiPythonLaunchCommand(
      args "SICO_LSF_MONITOR_ORIG_LD_LIBRARY_PATH"))
  )
)

procedure(SICO_lsfMonitorStart(@optional queue)
  prog((cid command state)
    command=SICO_lsfMonitorLaunchCommand(queue)
    unless(command return(nil))
    cid=ipcBeginProcess(command "" 'SICO_lsfMonitorDataHandler
      'SICO_lsfMonitorErrHandler 'SICO_lsfMonitorPostFunc "")
    unless(cid
      warn("Cannot start LSF Load Monitor.\n")
      return(nil))
    state=makeTable("cadLsfMonitorReadOnly" nil)
    state['mode]='readOnly
    state['cid]=cid
    state['initialQueue]=or(queue "")
    state['cancelled]=nil
    state['output]=""
    state['errors]=""
    cadLsfMonitorProcesses[cid]=state
    info("<INFO> LSF Load Monitor started.\n")
    return(cid)
  )
)

procedure(SICO_lsfMonitorStartSelector(form)
  prog((cid command host path queue state token)
    unless(SICO_lsfFormValidP(form) return(nil))
    SICO_lsfMonitorCancelForm(form)
    unless(form~>runType~>value=="LSF Farm"
      warn("LSF Load Monitor selection requires LSF Farm.\n")
      return(nil))
    queue=or(form~>queueName~>value "")
    host=or(form~>srvName~>value "")
    path=SICO_lsfDiscoveryPath("monitor")
    command=SICO_lsfMonitorLaunchCommand(queue path)
    unless(and(path command)
      SICO_lsfDeleteFile(path)
      return(nil))
    token=add1(or(get(form 'cadLsfMonitorRequestToken) 0))
    putpropq(form token cadLsfMonitorRequestToken)
    cid=ipcBeginProcess(command "" 'SICO_lsfMonitorDataHandler
      'SICO_lsfMonitorErrHandler 'SICO_lsfMonitorPostFunc "")
    unless(cid
      SICO_lsfDeleteFile(path)
      warn("Cannot start LSF Load Monitor selector.\n")
      return(nil))
    state=makeTable("cadLsfMonitorSelector" nil)
    state['mode]='selector
    state['form]=form
    state['cid]=cid
    state['token]=token
    state['resultFile]=path
    state['initialQueue]=queue
    state['initialHost]=host
    state['cancelled]=nil
    state['output]=""
    state['errors]=""
    cadLsfMonitorProcesses[cid]=state
    putpropq(form cid cadLsfMonitorCid)
    info("<INFO> LSF Load Monitor selector started.\n")
    return(cid)
  )
)

unless(getShellEnvVar("VIRTUOSO_CONTEXT_BUILD")=="1"
  SICO_lsfMonitorRegisterExitCleanup()
)

procedure(SICO_lsfMonitorInitializeRuntime()
  SICO_lsfMonitorRegisterExitCleanup()
)
sicoLsfMonitorVersion="20260924.sico.monitor.environment.v3"
)
