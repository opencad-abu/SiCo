; One-click, local Python Agent launch. No model task runs on the SKILL thread.

unless(and(boundp('sicoLauncherVersion)
           sicoLauncherVersion=="20261020.sico.launcher.v7")

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
    python=SICO_installedPython(product)
    unless(python
      root=or(SICO_environmentValue("SICO_PYTHON_ROOT" list("CAD_PYTHON_ROOT"))
              "/software/pkgs/python/3.9.13")
      python=or(SICO_environmentValue("SICO_PYTHON" list("CAD_PYTHON"))
                strcat(root "/bin/python3")))
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
      value=getShellEnvVar(name)
      inherited=strcat(inherited " SICO_VIRTUOSO_" name "=" sicoQuote(or(value ""))
        " SICO_VIRTUOSO_" name "_SET=" if(value then "1" else "0")))
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

/* SiCo 主窗口占左半屏；拉起后把 CIW 收到右下四分之一屏。
   hiGetScreenSize() 返回 ((像素宽 像素高)(毫米宽 毫米高))，兼容扁平返回。 */
procedure(sicoPlaceCiw()
  let((ciw pixels width height halfWidth halfHeight box)
    ciw=when(isCallable('hiGetCIWindow) car(errset(hiGetCIWindow() nil)))
    when(ciw
      pixels=when(isCallable('hiGetScreenSize) car(errset(hiGetScreenSize() nil)))
      when(listp(car(pixels)) pixels=car(pixels))
      when(and(pixels fixp(car(pixels)) fixp(cadr(pixels))
          car(pixels)>=640 cadr(pixels)>=600)
        width=car(pixels) height=cadr(pixels)
        halfWidth=width/2 halfHeight=height/2
        box=list(list(halfWidth 0) list(width halfHeight))
        errset(hiResizeWindow(ciw box) nil)
        errset(hiMoveWindow(ciw car(box)) nil)))
    ciw))

/* Only a Virtuoso started by SiCo requests this layout; normal SiCo loads keep
   the user's CIW position. The generated loader runs this after all definitions. */
procedure(sicoPlaceLaunchedCiw()
  when(equal(sicoEnv("SICO_VIRTUOSO_LAUNCH_CIW_LAYOUT") "lower-right-quarter")
    setShellEnvVar("SICO_VIRTUOSO_LAUNCH_CIW_LAYOUT" "")
    sicoPlaceCiw())
  t)

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
      sicoPlaceCiw()
      printf("[AI] Starting Silicon Copilot for the captured target.\n"))
    return(cid)
  ))

procedure(sicoStopDesktop(cid)
  when(sicoBindings[cid]
    sicoBindings[cid]['stopping]=t
    ipcWriteProcess(cid "@shutdown\n")))

procedure(sicoRequestShutdown(cid)
  when(tablep(sicoBindings[cid])
    sicoBindings[cid]['stopping]=t
    errset(ipcWriteProcess(cid "@shutdown\n") nil)))

procedure(sicoExitCleanup()
  when(boundp('sicoBindings)
    let((cids cid)
      foreach(cid sicoBindings cids=cons(cid cids))
      foreach(cid cids
        when(and(tablep(sicoBindings[cid]) sicoBindings[cid]['desktopSession])
          sicoRequestShutdown(cid)))
      foreach(cid cids
        when(and(tablep(sicoBindings[cid]) !sicoBindings[cid]['desktopSession])
          sicoRequestShutdown(cid)))
      t)))

procedure(sicoRegisterExitCleanup()
  unless(boundp('sicoExitCleanupRegistered)
    when(isCallable('regExitBefore)
      regExitBefore('sicoExitCleanup)
      sicoExitCleanupRegistered=t))
  sicoExitCleanupRegistered)

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  sicoRegisterExitCleanup())

procedure(sicoLauncherRevision()
  "20261020.sico.launcher.v7")

sicoLauncherVersion="20261020.sico.launcher.v7"
)
