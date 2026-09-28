; One-click, local Python Agent launch. No model task runs on the SKILL thread.

unless(and(boundp('sicoLauncherVersion)
           sicoLauncherVersion=="20260922.sico.launcher.v2")

unless(boundp('sicoLaunchCounter) sicoLaunchCounter=0)
unless(boundp('sicoInstanceBridge) sicoInstanceBridge=nil)
unless(boundp('sicoBridgeCounter) sicoBridgeCounter=0)
unless(boundp('sicoReconnectPending) sicoReconnectPending=nil)
unless(boundp('sicoLaunchWorkingDir)
  unless(and(boundp('cadContextBuilding) cadContextBuilding)
    let((alias working)
      alias=getShellEnvVar("PWD") working=getWorkingDir()
      sicoLaunchWorkingDir=or(
        when(boundp('aiLaunchWorkingDir) aiLaunchWorkingDir)
        when(and(alias isDir(alias) simplifyFilename(alias)==simplifyFilename(working)) alias)
        working))))

procedure(sicoEnv(name)
  let((value)
    value=getShellEnvVar(name)
    when(and(value strlen(value)>0) value)))

procedure(sicoQuote(value)
  let((output character i)
    output="'"
    for(i 1 strlen(value)
      character=substring(value i 1)
      output=strcat(output if(character=="'" then "'\"'\"'" else character)))
    strcat(output "'")))

procedure(sicoLaunchCommand(@optional (mode "launch"))
  prog((python root entry invocation libraryPath inherited name value product)
    product=SICO_installationRoot(simplifyFilename(strcat(sicoRoot "/../..")))
    root=or(SICO_environmentValue("SICO_PYTHON_ROOT" list("CAD_PYTHON_ROOT")) "/software/pkgs/python/3.9.13")
    python=or(SICO_environmentValue("SICO_PYTHON" list("CAD_PYTHON")) strcat(root "/bin/python3"))
    unless(and(substring(python 1 1)=="/" isExecutable(python))
      warn("Silicon Copilot: SICO_PYTHON must be an absolute executable.\n")
      return(nil))
    entry=strcat(sicoRoot "/bin/sico")
    if(and(isFile(entry) isExecutable(entry)) then
      invocation=sicoQuote(entry)
    else
      entry=strcat(sicoRoot "/python/sico-entry")
      invocation=strcat(sicoQuote(python) " -s " sicoQuote(entry)))
    unless(isFile(entry)
      warn("Silicon Copilot: native/source launcher is missing below: %s\n" sicoRoot) return(nil))
    libraryPath=or(SICO_environmentValue("SICO_AI_PYTHON_LIBRARY_PATH" list("CAD_AI_PYTHON_LIBRARY_PATH"))
                   SICO_environmentValue("SICO_SYSTEM_LD_LIBRARY_PATH" list("CAD_SYSTEM_LD_LIBRARY_PATH")) "")
    ; Capture the source environment before replacing Python/Qt search paths.
    ; These exported values describe this Virtuoso process, not another login shell.
    inherited=""
    foreach(name list("PATH" "LD_LIBRARY_PATH" "PYTHONPATH" "MODULEPATH"
                      "LOADEDMODULES" "_LMFILES_" "MODULESHOME" "LMOD_CMD")
      value=or(getShellEnvVar(name) "")
      inherited=strcat(inherited " SICO_VIRTUOSO_" name "=" sicoQuote(value)))
    return(strcat(
      "env -u PYTHONHOME -u PYTHONPATH -u LD_PRELOAD -u LD_AUDIT "
      "-u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH -u QT_SELECT "
      "-u QT_DIR -u QML2_IMPORT_PATH -u QT_XCB_NO_XI2 -u QT_XCB_NO_XI2_MOUSE "
      "PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 "
      "LD_LIBRARY_PATH=" sicoQuote(libraryPath) inherited
      " SICO_HOME=" sicoQuote(product)
      " SICO_PYTHON=" sicoQuote(python) " " invocation
      " " sicoQuote(mode) " --launch-dir "
      sicoQuote(sicoLaunchWorkingDir)))
  ))

procedure(sicoEnsureBridge(aebSource)
  prog((aebBinding aebCommand aebCid aebGeneration aebLegacy)
    when(sicoInstanceBridge
      when(sicoSessionLive(sicoInstanceBridge) return(sicoInstanceBridge))
      warn("Silicon Copilot: instance bridge ended; inspect receipts, then explicitly reconnect.\n")
      return(nil))
    foreach(aebCandidate sicoBindings
      when(and(sicoBindings[aebCandidate]
               sicoBindings[aebCandidate]['desktopSession]
               sicoSessionLive(aebCandidate)) aebLegacy=t))
    when(aebLegacy
      warn("Silicon Copilot: older desktop relay is still live; inspect receipts and explicitly reconnect.\n")
      return(nil))
    aebCommand=sicoLaunchCommand("bridge")
    unless(aebCommand return(nil))
    aebBinding=makeTable("sicoInstanceBinding" nil)
    foreach(aebKey list('window 'cellJson 'adeSession 'testName 'historyName 'historyHandle 'entry 'cwd)
      aebBinding[aebKey]=aebSource[aebKey])
    aebBinding['targets]=makeTable("sicoInstanceTargets" nil)
    sicoBridgeCounter=sicoBridgeCounter+1
    aebGeneration=sprintf(nil "g%d_%d_%d" ipcGetPid()
      stringToTime(getCurrentTime()) sicoBridgeCounter)
    aebCid=sicoConnect(aebCommand sprintf(nil "v%d" ipcGetPid())
      aebGeneration aebBinding['window] aebBinding)
    when(aebCid
      sicoInstanceBridge=aebCid
      sicoBindings[aebCid]['instanceBridge]=t)
    return(aebCid)))

procedure(sicoReconnectBridge()
  prog((arbBridge)
    arbBridge=sicoInstanceBridge
    when(and(arbBridge sicoBindings[arbBridge] sicoBindings[arbBridge]['skillActive])
      return(nil))
    foreach(arbCid sicoBindings
      when(and(sicoBindings[arbCid] sicoBindings[arbCid]['desktopSession])
        sicoStopDesktop(arbCid)))
    when(arbBridge
      sicoDisconnect(arbBridge)
      ipcWait(arbBridge 5 3)
      when(ipcIsAliveProcess(arbBridge)
        warn("Silicon Copilot: old bridge has not exited; reconnect remains unavailable.\n")
        return(nil)))
    sicoInstanceBridge=nil
    return(t)))

procedure(sicoFinishReconnect()
  prog((afrPending afrCopy afrBridge afrRetry)
    afrPending=sicoReconnectPending
    unless(afrPending return(nil))
    afrBridge=car(afrPending) afrCopy=cadr(afrPending)
    unless(afrBridge==sicoInstanceBridge
      sicoReconnectPending=nil return(nil))
    when(and(afrBridge sicoBindings[afrBridge] sicoBindings[afrBridge]['skillActive])
      afrRetry=or(afrCopy['reconnectRetry] 0)
      if(afrRetry<3 then
        afrCopy['reconnectRetry]=afrRetry+1
        hiRegTimer("sicoFinishReconnect()" nth(afrRetry list(2 5 10)))
      else
        sicoReconnectPending=nil
        warn("Silicon Copilot: reconnect deferred by an active SKILL request; retry explicitly after it finishes.\n"))
      return(nil))
    sicoReconnectPending=nil
    unless(sicoReconnectBridge() return(nil))
    ; This path is reached only by the confirmed user reconnect action.
    return(sicoShow(afrCopy['window] afrCopy t t))))

procedure(sicoReconnectSession(arsCid arsTargetId)
  prog((arsSource arsCopy)
    arsSource=sicoSessionTarget(arsCid arsTargetId)
    unless(and(arsSource sicoBindings[arsCid]['desktopSession]) return(nil))
    arsCopy=makeTable("sicoReconnectTarget" nil)
    foreach(arsKey list('window 'cellJson 'adeSession 'testName 'historyName 'historyHandle 'entry 'cwd)
      arsCopy[arsKey]=arsSource[arsKey])
    unless(sicoReconnectPending
      sicoReconnectPending=list(sicoInstanceBridge arsCopy)
      hiRegTimer("sicoFinishReconnect()" 1))
    return(t)))

procedure(sicoShow(@optional window captured newSession allowLaunch)
  prog((binding cid existing command probe bridge targetId)
    binding=or(captured makeTable("sicoLaunchBinding" nil))
    unless(captured
      unless(window
        when(isCallable('hiGetCurrentWindow) window=hiGetCurrentWindow()))
      binding['window]=window binding['entry]="menu")
    binding['cwd]=sicoLaunchWorkingDir
    ; Read once before spawning. All subsequent requests keep this binding.
    probe=errset(sicoContextJson(binding) nil)
    unless(and(probe binding['valid])
      warn("Silicon Copilot: open from a schematic, layout, ADE window, or CIW; the captured target is unavailable.\n") return(nil))
    unless(newSession existing=sicoSessionForWindow(binding['window]))
    when(existing
      ipcWriteProcess(existing "@show\n")
      return(existing))
    ; Internal callers can restore an existing desktop without launching one.
    ; Menus/Ask/new-session and the confirmed reconnect explicitly permit launch.
    unless(or(allowLaunch newSession !captured) return(nil))
    command=sicoLaunchCommand()
    unless(command return(nil))
    bridge=sicoEnsureBridge(binding)
    unless(bridge return(nil))
    sicoLaunchCounter=sicoLaunchCounter+1
    targetId=sprintf(nil "d%d_%d" ipcGetPid() sicoLaunchCounter)
    binding['targets]=sicoBindings[bridge]['targets]
    binding['targets][targetId]=binding
    cid=sicoConnect(command sicoBindings[bridge]['instanceId]
      sicoBindings[bridge]['generation] binding['window] binding targetId)
    when(cid
      sicoBindings[cid]['desktopSession]=t
      sicoBindSession(binding['window] cid)
      printf("[AI] Starting Silicon Copilot for the captured target.\n"))
    return(cid)
  ))

procedure(sicoStopDesktop(cid)
  when(sicoBindings[cid]
    sicoBindings[cid]['stopping]=t
    ipcWriteProcess(cid "@shutdown\n")))

procedure(sicoLauncherRevision()
  "20260922.sico.launcher.v2")

sicoLauncherVersion="20260922.sico.launcher.v2"
)
