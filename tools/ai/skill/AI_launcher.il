; Virtuoso launcher for the external Python/PyQt5/QTermWidget terminal.

unless(and(boundp('aiLauncherVersion)
             aiLauncherVersion=="20261020.sico.launcher.v21")

unless(boundp('aiLaunchWorkingDir)
  unless(and(boundp('cadContextBuilding) cadContextBuilding)
    aiLaunchWorkingDir=SICO_stateLaunchDirectory()))

when(and(boundp('cadAiLaunchForm) cadAiLaunchForm)
  when(hiIsFormDisplayed(cadAiLaunchForm) hiFormClose(cadAiLaunchForm))
  cadAiLaunchForm=nil
)

procedure(aiShellQuote(value)
  let((text output index character)
    text=if(value then sprintf(nil "%s" value) else "")
    output="'"
    for(index 1 strlen(text)
      character=substring(text index 1)
      output=strcat(output if(character=="'" then "'\"'\"'" else character)))
    strcat(output "'")
  )
)

procedure(aiEnvValue(name)
  let((value)
    value=getShellEnvVar(name)
    when(and(value strlen(value)>0) value)
  )
)

procedure(aiProductEnvironment(currentName legacyNames)
  ; Retired names are presence-only diagnostics, including empty old values.
  SICO_environmentValue(currentName legacyNames)
)

procedure(aiCanonicalPath(path)
  let((resolved)
    when(and(stringp(path) strlen(path)>0
             substring(path 1 1)=="/")
      ; The default resolves symbolic links.  Passing t would preserve the
      ; link and make two equivalent interpreter entries compare unequal.
      resolved=errset(simplifyFilename(path) nil)
      when(and(resolved stringp(car(resolved))) car(resolved))))
)

procedure(aiPythonVersionOK(path)
  let((probe command result)
    command=strcat(
      "env -i PATH=/usr/bin:/bin PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 "
      aiShellQuote(path) " -s -c "
      aiShellQuote("import sys; raise SystemExit(0 if tuple(sys.version_info[:3]) == (3, 9, 13) else 1)")
      " >/dev/null 2>&1")
    result=errset(system(command) nil)
    and(result numberp(car(result)) car(result)==0)
  )
)

procedure(aiProductionPythonP(path)
  let((pythonReal)
    pythonReal=aiCanonicalPath(path)
    and(pythonReal
        isExecutable(path)
        aiPythonVersionOK(path)))
)

procedure(aiControllerReadyDelay()
  if(aiEnvValue("PROJ_AI_LSF_EXE") then 3000 else 50)
)

procedure(aiPythonExecutable()
  let((installed configured configuredRoot selected)
    installed=SICO_installedPython()
    configured=when(not(installed)
      aiProductEnvironment("SICO_PYTHON" list("CAD_PYTHON")))
    configuredRoot=when(and(not(installed) not(configured))
      aiProductEnvironment("SICO_PYTHON_ROOT" list("CAD_PYTHON_ROOT")))
    selected=or(installed configured
      when(and(configuredRoot stringp(configuredRoot))
        strcat(configuredRoot "/bin/python3")))
    when(and(selected aiProductionPythonP(selected)) selected)
  )
)

procedure(aiPythonEntry()
  let((root native)
    root=if(and(boundp('aiRoot) aiRoot) then aiRoot
      else when(isCallable('aiResolveRoot) aiResolveRoot()))
    when(root native=strcat(root "/bin/sico-ai"))
    or(aiProductEnvironment("SICO_AI_PYTHON_ENTRY"
          list("CAD_AI_PYTHON_ENTRY" "CAD_CODEX_PYTHON_ENTRY"))
        when(and(native isFile(native) isExecutable(native)) native)
        when(and(root isDir(root))
          strcat(root "/python/sico-ai")))
  )
)

procedure(aiControllerCommand(workspace agent)
  let((python entry invocation libraryPath subcommand inherited name value)
    python=aiPythonExecutable()
    entry=aiPythonEntry()
    invocation=if(and(entry boundp('aiRoot) aiRoot
      entry==strcat(aiRoot "/bin/sico-ai") isExecutable(entry)) then aiShellQuote(entry)
      else when(and(python entry) strcat(aiShellQuote(python) " " aiShellQuote(entry))))
    subcommand=if(aiEnvValue("PROJ_AI_LSF_EXE") then "lsf-session" else "session")
    libraryPath=or(aiProductEnvironment("SICO_AI_PYTHON_LIBRARY_PATH"
                      list("CAD_AI_PYTHON_LIBRARY_PATH" "CAD_CODEX_PYTHON_LIBRARY_PATH"))
                    aiProductEnvironment("SICO_SYSTEM_LD_LIBRARY_PATH"
                      list("CAD_SYSTEM_LD_LIBRARY_PATH")) "")
    ; Capture before the controller's Python/Qt environment is sanitized.
    ; Module shell functions are not portable across LSF shells; exported
    ; module metadata and effective search paths are the reproducible inputs.
    inherited=""
    foreach(name list("PATH" "LD_LIBRARY_PATH" "PYTHONPATH" "MODULEPATH"
                      "LOADEDMODULES" "_LMFILES_" "MODULESHOME" "LMOD_CMD")
      value=getShellEnvVar(name)
      inherited=strcat(inherited " SICO_VIRTUOSO_" name "=" aiShellQuote(or(value ""))
        " SICO_VIRTUOSO_" name "_SET=" if(value then "1" else "0")))
    when(and(python entry isFile(entry)
               member(agent list("codex" "claude")))
      strcat(
        "env -u PYTHONHOME -u PYTHONPATH -u LD_PRELOAD -u LD_AUDIT "
        "-u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH -u QT_SELECT "
        "-u QT_DIR -u QML2_IMPORT_PATH PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 "
        SICO_stateEnvironment("ai")
        inherited " LD_LIBRARY_PATH="
        aiShellQuote(libraryPath) " SICO_PYTHON=" aiShellQuote(python) " " invocation
        " " subcommand " --agent " aiShellQuote(agent)
        " --workspace " aiShellQuote(workspace)))
  )
)

procedure(aiStartAgent(workspace agent @optional entryRef entryCaptured)
  prog((resolved python entry command cid configured configuredRoot displayAgent)
    when(aiIpcSessionAliveP()
      cid=aiIpcActivate()
      when(cid
        info("<INFO> Restoring the active AI Assistant session.\n")
        return(cid)))
    unless(member(agent list("codex" "claude"))
      warn("AI Assistant agent is unsupported: %L.\n" agent)
      return(nil))
    displayAgent=if(agent=="claude" then "Claude" else "Codex")
    unless(and(workspace stringp(workspace) strlen(workspace)>0)
      warn("AI Assistant workspace is empty.\n")
      return(nil))
    resolved=simplifyFilename(workspace t)
    unless(isDir(resolved)
      warn("AI Assistant workspace is not a directory: %s\n" resolved)
      return(nil))
    python=aiPythonExecutable()
    unless(python
      configured=aiProductEnvironment("SICO_PYTHON" list("CAD_PYTHON"))
      configuredRoot=aiProductEnvironment("SICO_PYTHON_ROOT" list("CAD_PYTHON_ROOT"))
      cond(
        (configured
          warn("AI Assistant cannot use SICO_PYTHON; it must be an absolute, executable Python 3.9.13 interpreter: %s\n"
            configured))
        (configuredRoot
          warn("AI Assistant cannot use SICO_PYTHON_ROOT/bin/python3; it must be an executable Python 3.9.13 interpreter: %s/bin/python3\n"
            configuredRoot))
        (t
          warn("AI Assistant requires $SICO_HOME/runtime/python, $SICO_HOME/etc/config/dev-runtime, or SICO_PYTHON/SICO_PYTHON_ROOT for Python 3.9.13.\n")))
      return(nil))
    entry=aiPythonEntry()
    unless(and(entry isFile(entry))
      warn("AI Assistant Python entry is missing: %L. Set SICO_AI_PYTHON_ENTRY or install the AI tool.\n"
        entry)
      return(nil))
    command=aiControllerCommand(resolved agent)
    unless(command
      warn("AI Assistant could not construct the Python controller command.\n")
      return(nil))
    cid=aiIpcStart(command resolved entryRef entryCaptured)
    when(cid
      aiIpcState['agent]=agent
      info("<INFO> Starting %s in %s.\n" displayAgent resolved))
    return(cid)
  )
)

procedure(aiStart(workspace)
  aiStartAgent(workspace "codex")
)

procedure(aiLaunchFormCB(form)
  let((cid agent)
    agent=if(form~>aiAgent~>value=="Claude" then "claude" else "codex")
    cid=aiStartAgent(form~>aiWorkspace~>value agent
      when(boundp('cadAiLaunchWindowRef) cadAiLaunchWindowRef)
      when(boundp('cadAiLaunchWindowCaptured) cadAiLaunchWindowCaptured))
    when(cid
      cadAiLaunchWindowCaptured=nil cadAiLaunchWindowRef=nil
      hiFormClose(form))
    cid
  )
)

procedure(aiCreateLaunchForm()
  let((workspace agent layout)
    workspace=hiCreateFileSelectorField(
      ?name 'aiWorkspace
      ?prompt "Workspace:"
      ?mode 'directoryOnly
      ?defValue aiWorkingDirectory()
      ?editable t)
    agent=hiCreateRadioField(
      ?name 'aiAgent
      ?prompt "Agent:"
      ?choices list("Codex" "Claude")
      ?value "Claude"
      ?defValue "Claude"
      ?itemsPerRow 2)
    layout=hiCreateVerticalBoxLayout(
      'aiLaunchLayout
      ?items list(list(workspace 'stretch 0) list(agent 'stretch 0))
      ?margins list(12 10 12 10)
      ?spacing hiGetDefaultWidgetSpacing(?widgetsAreRelated t)
      ?vert_align 'top)
    hiCreateLayoutForm(
      'cadAiLaunchForm
      strcat(cond(
               (isCallable('SICO_logo) SICO_logo())
               (isCallable('witMenuLogo) witMenuLogo())
               (t "SiCo"))
             "::AI Assistant")
      layout
      ?buttonLayout list('Empty list('Launch 'aiLaunchFormCB) list('Close 'hiFormClose))
      ?dialogStyle 'modeless
      ?initialSize list(680 135)
      ?minSize list(520 125)
      ?sizePolicy 'expanding)
  )
)

procedure(cadDisplayAiAssistant()
  if(aiIpcActivate() then
    info("<INFO> Restoring the active AI Assistant session.\n")
  else
    when(isCallable('aiWindowCapture)
      cadAiLaunchWindowRef=aiWindowCapture(aiWindowTry('hiGetCurrentWindow nil))
      cadAiLaunchWindowCaptured=t)
    unless(and(boundp('cadAiLaunchForm) cadAiLaunchForm)
      cadAiLaunchForm=aiCreateLaunchForm())
    when(cadAiLaunchForm
      cadAiLaunchForm~>aiWorkspace~>value=aiWorkingDirectory()
      hiDisplayForm(cadAiLaunchForm)))
)

procedure(aiLauncherRevision()
  "20261020.sico.launcher.v21"
)

aiLauncherVersion="20261020.sico.launcher.v21"
)
