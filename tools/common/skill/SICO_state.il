; SKILL delegates state selection, activation admission and permissions to Python.
; Source installs use the development adapter; runtime installs require its ELF.
unless(and(boundp('sicoStateVersion)
           sicoStateVersion=="20260924.sico.state.v4")

procedure(SICO_stateInitialize()
  unless(boundp('sicoStateLaunchDirectory)
    let((working logical)
      working=getWorkingDir()
      logical=getShellEnvVar("PWD")
      when(and(logical strlen(logical)>0 isDir(logical)
               simplifyFilename(logical)==simplifyFilename(working))
        working=logical)
      sicoStateLaunchDirectory=simplifyFilename(working t)))
  t)

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  SICO_stateInitialize())

procedure(SICO_stateQuote(value)
  let(((output "'") index character)
    for(index 1 strlen(value)
      character=substring(value index 1)
      output=strcat(output if(character=="'" then "'\"'\"'" else character)))
    strcat(output "'")))

procedure(SICO_stateCommand(scope)
  prog((product entry python pythonRoot invocation command libraryPath)
    product=SICO_installationRoot()
    python=SICO_environmentValue("SICO_PYTHON" list("CAD_PYTHON"))
    unless(python
      pythonRoot=or(SICO_environmentValue("SICO_PYTHON_ROOT" list("CAD_PYTHON_ROOT"))
                    "/software/pkgs/python/3.9.13")
      unless(and(strlen(pythonRoot)>0 substring(pythonRoot 1 1)=="/" isDir(pythonRoot))
        error("SiCo state requires an absolute Python installation root\n"))
      python=strcat(pythonRoot "/bin/python3"))
    unless(and(strlen(python)>0 substring(python 1 1)=="/" isExecutable(python))
      error("SiCo state requires the configured Python interpreter\n"))
    libraryPath=SICO_stateQuote(or(SICO_environmentValue("SICO_SYSTEM_LD_LIBRARY_PATH"
      list("CAD_SYSTEM_LD_LIBRARY_PATH")) ""))
    entry=strcat(product "/tools/common/bin/sico-state")
    if(isExecutable(entry) then
      invocation=SICO_stateQuote(entry)
    else
      unless(SICO_installationSourceP()
        when(isDir(strcat(product "/tools/common/context"))
          error("Compiled SiCo state entry is unavailable\n")))
      entry=strcat(product "/tools/common/python/sico-state")
      unless(isFile(entry) error("SiCo state entry is unavailable\n"))
      invocation=strcat(SICO_stateQuote(python) " -E -s " SICO_stateQuote(entry)))
    command=strcat("env -u PYTHONHOME -u PYTHONPATH -u LD_PRELOAD -u LD_AUDIT "
      "PYTHONDONTWRITEBYTECODE=1 SICO_HOME=" SICO_stateQuote(product)
      " SICO_PYTHON=" SICO_stateQuote(python) " LD_LIBRARY_PATH=" libraryPath
      " SICO_SYSTEM_LD_LIBRARY_PATH=" libraryPath
      " " invocation " --launch "
      SICO_stateQuote(sicoStateLaunchDirectory) " --scope " SICO_stateQuote(scope)
      " --error-format skill")
    when(boundp('sicoStateRoot)
      command=strcat(command " --state " SICO_stateQuote(sicoStateRoot)))
    return(command)))

procedure(SICO_stateResponse(scope)
  prog((cid chunk output parsed)
    SICO_stateInitialize()
    unless(member(scope list("project" "ai")) error("Invalid SiCo state scope\n"))
    cid=ipcBeginProcess(SICO_stateCommand(scope) "" nil 'SICO_stateError)
    unless(cid error("Cannot start SiCo state validation\n"))
    output=""
    while(setq(chunk ipcReadProcess(cid 10))
      output=strcat(output chunk)
      when(strlen(output)>65536
        ipcKillProcess(cid)
        error("SiCo state response exceeds limit\n")))
    ipcWait(cid 1 10)
    when(ipcIsAliveProcess(cid)
      ipcKillProcess(cid)
      error("SiCo state validation timed out\n"))
    parsed=car(errset(readstring(output) nil))
    unless(ipcGetExitStatus(cid)==0
      when(and(listp(parsed) length(parsed)==2
               car(parsed)=="sico.state.error.v1" stringp(cadr(parsed)))
        error("SiCo project state was rejected: %s\n" cadr(parsed)))
      error("SiCo project state validation failed (exit %L); check sico-state installation\n"
            ipcGetExitStatus(cid)))
    unless(and(listp(parsed) length(parsed)==4
               car(parsed)=="sico.state.environment.v1"
               stringp(cadr(parsed)) stringp(caddr(parsed)) stringp(cadddr(parsed)))
      error("Invalid SiCo state response\n"))
    when(and(boundp('sicoStateRoot) sicoStateRoot!=cadr(parsed))
      error("SiCo state identity changed during the session\n"))
    when(and(not(boundp('sicoStateRoot))
             car(last(parseString(cadr(parsed) "/")))==".cad")
      warn("Legacy .cad project retained; activate migration before using .sico\n"))
    sicoStateRoot=cadr(parsed)
    return(parsed)))

procedure(SICO_stateError(cid data)
  warn("SiCo state: %s" data))

procedure(SICO_stateDirectory(@optional (scope "project"))
  caddr(SICO_stateResponse(scope)))

procedure(SICO_stateLaunchDirectory()
  SICO_stateInitialize()
  sicoStateLaunchDirectory)

procedure(SICO_stateEnvironment(@optional (scope "project"))
  cadddr(SICO_stateResponse(scope)))

procedure(SICO_stateRevision() "20260924.sico.state.v4")
sicoStateVersion="20260924.sico.state.v4"
)
