; Standalone DSPF Analyzer launcher for the SiCo menu.

unless(and(boundp('rceDspfLauncherVersion)
             rceDspfLauncherVersion=="20260814.dspf.launcher.v5"
             boundp('rceDspfExitCleanupRegistered)
             rceDspfExitCleanupRegistered)

unless(boundp('rceDspfLauncherProcesses)
  rceDspfLauncherProcesses=makeTable("rceDspfLauncherProcesses" nil)
)

unless(boundp('rceDspfExitCleanupRegistered)
  rceDspfExitCleanupRegistered=nil
)

procedure(rceDspfLaunchEnvironment(standalone)
  let((environment parentPid)
    parentPid=ipcGetPid()
    if(and(integerp(parentPid) parentPid>1) then
      environment=SICO_guiPythonEnvironment(
        "RCE_ORIG_LD_LIBRARY_PATH"
        if(standalone then
          "-u RCE_DSPF_LAYOUT_LIB -u RCE_DSPF_LAYOUT_CELL "
          "-u RCE_DSPF_LAYOUT_VIEW "
        else ""))
      when(environment
        strcat(environment
          "RCE_DSPF_PARENT_PID="
          SICO_shellQuote(sprintf(nil "%d" parentPid)) " "))
    else
      warn("RCE could not determine the owning Virtuoso PID.\n")
      nil)
  )
)

procedure(rceDspfLauncherSelectionPath(selection)
  let((directory fileName)
    when(and(selection stringp(car(selection)) stringp(cadr(selection))
               strlen(cadr(selection))>0)
      directory=if(strlen(car(selection))>0 then car(selection) else ".")
      fileName=cadr(selection)
      SICO_absolutePath(strcat(directory "/" fileName))
    )
  )
)

procedure(rceDspfLauncherDataHandler(cid data)
  nil
)

procedure(rceDspfLauncherErrHandler(cid data)
  when(and(data strlen(data)>0)
    warn("RCE DSPF analyzer: %s" data))
)

procedure(rceDspfLauncherPostFunc(cid exitStatus)
  let((source)
    source=rceDspfLauncherProcesses[cid]
    remove(cid rceDspfLauncherProcesses)
    if(exitStatus==0 then
      info("<INFO> RCE DSPF analyzer exited normally: %s\n" or(source ""))
    else
      warn("RCE DSPF analyzer exited with status %L: %s\n"
           exitStatus or(source "")))
  )
)

procedure(rceDspfLauncherStopAll()
  let((cid cids)
    foreach(cid rceDspfLauncherProcesses
      cids=cons(cid cids))
    foreach(cid cids
      remove(cid rceDspfLauncherProcesses)
      when(ipcIsAliveProcess(cid)
        errset(ipcKillProcess(cid) t)))
    nil
  )
)

procedure(rceDspfExitCleanup()
  when(isCallable('rceDspfAnalyzerStop)
    errset(rceDspfAnalyzerStop() t))
  errset(rceDspfLauncherStopAll() t)
  t
)

procedure(rceDspfRegisterExitCleanup()
  unless(rceDspfExitCleanupRegistered
    regExitBefore('rceDspfExitCleanup)
    rceDspfExitCleanupRegistered=t)
  rceDspfExitCleanupRegistered
)

procedure(rceDspfStandaloneStart(source)
  prog((path python entry environment command cid)
    path=SICO_absolutePath(source)
    unless(and(strlen(path)>0 isFile(path) fileLength(path)>0)
      warn("RCE DSPF analyzer source is missing or empty: %L\n" path)
      return(nil))
    python=or(SICO_envValue("RCE_ANALYZER_PYTHON") RCE_pythonExe())
    entry=RCE_pythonEntry()
    environment=rceDspfLaunchEnvironment(t)
    unless(environment return(nil))
    command=strcat(
      environment
      SICO_backendCommand("rce" python entry)
      " dspf-gui " SICO_shellQuote(path))
    cid=ipcBeginProcess(command "" 'rceDspfLauncherDataHandler
      'rceDspfLauncherErrHandler 'rceDspfLauncherPostFunc "")
    unless(cid
      warn("RCE could not start the DSPF analyzer for %s.\n" path)
      return(nil))
    rceDspfLauncherProcesses[cid]=path
    info("<INFO> RCE DSPF analyzer started for %s.\n" path)
    return(cid)
  )
)

procedure(rceDspfAnalyzerChooseFile()
  let((selection source)
    selection=hiDisplayFileDialog(
      ?dialogName 'rceDspfOpenDialog
      ?workingDir pwd()
      ?filter "DSPF/SPF Files (*.dspf *.spf);;All Files (*)"
      ?mode 'existingFile
      ?modal t
      ?caption "Open DSPF/SPF"
      ?acceptMode 'open)
    source=rceDspfLauncherSelectionPath(selection)
    when(source
      rceDspfStandaloneStart(source))
  )
)

procedure(rceDspfLauncherRevision()
  "20260814.dspf.launcher.v5"
)

unless(getShellEnvVar("VIRTUOSO_CONTEXT_BUILD")=="1"
  rceDspfRegisterExitCleanup()
)

procedure(rceDspfInitializeRuntime()
  rceDspfRegisterExitCleanup()
)
rceDspfLauncherVersion="20260814.dspf.launcher.v5"
)
