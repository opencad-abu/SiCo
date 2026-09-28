; Construct discovery and batch execution commands.

unless(and(boundp('sicoCADlsfCommandsModulesVersion)
             sicoCADlsfCommandsModulesVersion=="20260924.flow.environment.v3")
procedure(SICO_lsfPythonExe()
  let((pythonRoot)
    pythonRoot=SICO_envValue("SICO_PYTHON_ROOT")
    or(SICO_envValue("SICO_LSF_PYTHON")
       SICO_envValue("SICO_PYTHON")
       when(pythonRoot
         simplifyFilename(strcat(pythonRoot "/bin/python3") t))
       "python3")
  )
)

procedure(SICO_lsfDiscoveryScript()
  let((toolsRoot)
    toolsRoot=SICO_toolsRoot()
    or(SICO_nativeEntry("sico-lsf") SICO_envValue("SICO_LSF_PYTHON_ENTRY")
       when(toolsRoot
         simplifyFilename(strcat(toolsRoot "/common/python/sico-lsf") t)))
  )
)

procedure(SICO_lsfDiscoveryPath(kind)
  let((path)
    path=SICO_tempPath(strcat("cad-lsf-" kind "XXXX"))
    when(path makeTempFileName(path))
  )
)

procedure(SICO_lsfDiscoveryCommand(mode queue outputPath)
  let((command entry environment sessionPid)
    entry=SICO_lsfDiscoveryScript()
    environment=SICO_tempEnvironment()
    when(and(entry isFile(entry) environment outputPath)
      command=strcat(
        "exec env -u PYTHONHOME -u PYTHONPATH " environment
        "LC_ALL=C LD_LIBRARY_PATH="
        SICO_shellQuote(SICO_pythonLibraryPath()) " "
        SICO_backendCommand("sico-lsf" SICO_lsfPythonExe() entry) " " mode
        " --format tsv --output " SICO_shellQuote(outputPath))
      sessionPid=ipcGetPid()
      when(and(integerp(sessionPid) sessionPid>1)
        command=strcat(command " --session-pid " sprintf(nil "%d" sessionPid)))
      when(and(queue strlen(queue)>0)
        command=strcat(command " --queue " SICO_shellQuote(queue)))
      command
    )
  )
)

procedure(SICO_lsfNormalizeCpu(cpu)
  let((value)
    value=or(cpu "1")
    if(rexMatchp("^[1-9][0-9]*$" value) then value else "1")
  )
)

procedure(SICO_lsfSelectedHost(host)
  if(and(stringp(host) host!=SICO_lsfAutoHost()) then host else "")
)

procedure(SICO_lsfMaxCpu(values)
  let((maximum value numeric)
    maximum=1
    foreach(value values
      numeric=atoi(SICO_lsfNormalizeCpu(value))
      when(numeric>maximum
        maximum=numeric)
    )
    sprintf(nil "%d" maximum)
  )
)

procedure(SICO_lsfQueueInteractiveP(queue)
  ; LSF publishes the per-queue interactive capability in its scheduling
  ; policies: ONLY_INTERACTIVE queues require "bsub -Is", NO_INTERACTIVE
  ; queues reject it and accept only "bsub -K".  Every other queue accepts the
  ; interactive form, which streams the job output to the submitting session.
  let((bqueues cached)
    unless(boundp('sicoLsfQueueInteractiveCache)
      sicoLsfQueueInteractiveCache=makeTable("sicoLsfQueueInteractive" nil))
    cached=sicoLsfQueueInteractiveCache[queue]
    unless(cached
      bqueues=or(SICO_envValue("SICO_LSF_BQUEUES") "bqueues")
      cached=if(system(strcat(SICO_shellQuote(bqueues) " -l "
                              SICO_shellQuote(queue)
                              " 2>/dev/null | /bin/grep -q NO_INTERACTIVE"))==0
        then
          "no"
        else
          "yes"
      )
      sicoLsfQueueInteractiveCache[queue]=cached
    )
    cached=="yes"
  )
)

procedure(SICO_lsfWrapCommand(command runType queue host cpu
                             @optional jobName logFile)
  let((bsub interactive wrapped)
    if(runType!="LSF Farm" then
      command
    else
      if(not(and(stringp(queue) strlen(queue)>0)) then
        progn(
          warn("<WARN> LSF::Refusing submission without a verified Queue.\n")
          nil)
      else
        bsub=or(SICO_envValue("SICO_LSF_BSUB") "bsub")
        interactive=SICO_lsfQueueInteractiveP(queue)
        ; A queue that rejects interactive jobs cannot stream the job output,
        ; so the job appends it to the flow log the submitting session tails.
        when(and(!interactive logFile strlen(logFile)>0)
          command=strcat(command " >> " SICO_shellQuote(logFile) " 2>&1"))
        wrapped=strcat(
          SICO_shellQuote(bsub) if(interactive then " -Is" else " -K"))
        when(and(jobName strlen(jobName)>0)
          wrapped=strcat(wrapped " -J " SICO_shellQuote(jobName))
        )
        wrapped=strcat(wrapped " -q " SICO_shellQuote(queue))
        host=SICO_lsfSelectedHost(host)
        when(strlen(host)>0
          wrapped=strcat(wrapped " -m " SICO_shellQuote(host))
        )
        strcat(wrapped
          " -n " SICO_shellQuote(SICO_lsfNormalizeCpu(cpu))
          " /bin/sh -c " SICO_shellQuote(command))
      )
    )
  )
)

procedure(SICO_lsfCancelCommand(runType jobName)
  let((bkill)
    if(and(runType=="LSF Farm" jobName strlen(jobName)>0) then
      bkill=or(SICO_envValue("SICO_LSF_BKILL") "bkill")
      strcat(SICO_shellQuote(bkill) " -J " SICO_shellQuote(jobName))
    else
      ""
    )
  )
)

sicoCADlsfCommandsModulesVersion="20260924.flow.environment.v3"
)
