; Asynchronous adapters for existing headless CAD workflows.

unless(and(boundp('aiWorkflowVersion)
             aiWorkflowVersion=="20261020.sico.flow.v7"
             boundp('aiWorkflowState)
             aiWorkflowState['exitCleanupRegistered])

unless(boundp('aiWorkflowState)
  aiWorkflowState=makeTable("aiWorkflowState" nil))
unless(boundp('aiWorkflowJobs)
  aiWorkflowJobs=makeTable("aiWorkflowJobs" nil))
unless(boundp('aiWorkflowByCid)
  aiWorkflowByCid=makeTable("aiWorkflowByCid" nil))
unless(boundp('aiWorkflowRunning)
  aiWorkflowRunning=makeTable("aiWorkflowRunning" nil))
unless(boundp('aiWorkflowLaunchDir)
  unless(and(boundp('cadContextBuilding) cadContextBuilding)
    aiWorkflowLaunchDir=SICO_stateLaunchDirectory()))

procedure(aiWorkflowNextId()
  let((owner sequence)
    owner=ipcGetPid()
    unless(integerp(owner) owner=0)
    sequence=or(aiWorkflowState['sequence] 0)+1
    aiWorkflowState['sequence]=sequence
    sprintf(nil "cadflow-%d-%d" owner sequence)))

procedure(aiWorkflowCreateRuntimeDir()
  prog((attempt root template directory base)
    root=SICO_stateDirectory()
    template=strcat(root "/cadflowXXXX")
    for(attempt 1 32
      directory=makeTempFileName(template)
      base=if(stringp(directory) then
             car(last(parseString(directory "/"))) else "")
      when(and(stringp(directory)
                 substring(directory 1 strlen(root)+1)==strcat(root "/")
                 rexMatchp("^cadflow[a-zA-Z0-9]+$" base)
                 system(strcat("/bin/mkdir -m 700 -- "
                               aiWorkflowShellQuote(directory)))==0
                 isDir(directory))
        return(directory)))
    return(nil)))

procedure(aiWorkflowWriteLauncher(path command)
  let((port script written closed)
    script=strcat(
      "#!/bin/sh\n/bin/rm -f -- \"$0\"\nbackend_pid=\nstopping=0\n"
      "snapshot_tree() {\n"
      "  /bin/ps -eo pid=,ppid= 2>/dev/null | /bin/awk -v root=\"$backend_pid\" '\n"
      "    { child[NR]=$1; sibling[NR]=head[$2]; head[$2]=NR }\n"
      "    function walk(parent, cursor, pid) { for (cursor=head[parent]; cursor; "
      "cursor=sibling[cursor]) { pid=child[cursor]; if (!seen[pid]++) { print pid; "
      "walk(pid) } } }\n"
      "    END { if (root ~ /^[0-9]+$/) { seen[root]=1; walk(root); print root } }'\n}\n"
      "terminate_tree() {\n  [ \"$stopping\" -eq 0 ] || return\n  stopping=1\n"
      "  [ -n \"$backend_pid\" ] || return\n  targets=$(snapshot_tree)\n"
      "  for pid in $targets; do kill -TERM \"$pid\" 2>/dev/null || :; done\n"
      "  count=0\n  while [ \"$count\" -lt 10 ]; do\n    alive=0\n"
      "    for pid in $targets; do kill -0 \"$pid\" 2>/dev/null && alive=1; done\n"
      "    [ \"$alive\" -ne 0 ] || break\n    /bin/sleep 0.1\n"
      "    count=$((count + 1))\n  done\n  targets=\"$targets $(snapshot_tree)\"\n"
      "  for pid in $targets; do kill -KILL \"$pid\" 2>/dev/null || :; done\n}\n"
      "trap 'terminate_tree' TERM HUP INT\n" command " &\nbackend_pid=$!\n"
      "while :; do\n  wait \"$backend_pid\"\n  backend_status=$?\n"
      "  kill -0 \"$backend_pid\" 2>/dev/null || break\ndone\n"
      "trap - TERM HUP INT\nexit \"$backend_status\"\n")
    port=outfile(path "w")
    when(port
      written=car(errset(fprintf(port "%s" script) nil))
      closed=close(port)
      and(written closed))))

procedure(aiWorkflowLogLimit() 1048576)

procedure(aiWorkflowLogMarker()
  "\n[AI Assistant] flow log truncated at 1048576 bytes.\n")

procedure(aiWorkflowAppendLog(cid data)
  let((jobId job marker current remaining payload truncated port written)
    jobId=aiWorkflowByCid[cid]
    job=aiWorkflowJobs[jobId]
    when(and(job stringp(data) strlen(data)>0 !job['logTruncated])
      marker=aiWorkflowLogMarker()
      current=if(isFile(job['log]) then fileLength(job['log]) else 0)
      remaining=aiWorkflowLogLimit()-strlen(marker)-current
      truncated=strlen(data)>remaining
      payload=if(truncated then
        strcat(if(remaining>0 then substring(data 1 remaining) else "") marker)
      else data)
      port=outfile(job['log] "a")
      when(port
        written=car(errset(fprintf(port "%s" payload) nil))
        close(port)
        when(written
          job['logBytes]=if(isFile(job['log]) then
            fileLength(job['log]) else current+strlen(payload))
          when(truncated job['logTruncated]=t))))
    nil))

procedure(aiWorkflowCleanupJobArtifacts(job)
  let((field path)
    when(job
      foreach(field list('launcher 'log)
        path=job[field]
        when(and(stringp(path) isFile(path))
          errset(deleteFile(path) nil)))
      path=job['runtime]
      when(and(stringp(path) isDir(path))
        errset(deleteDir(path) nil)))
    t))

procedure(aiWorkflowJobCount()
  let(((count 0) jobId)
    foreach(jobId aiWorkflowJobs count=count+1)
    count))

procedure(aiWorkflowEvictOldestTerminal()
  let((jobId job sequence oldestId oldestSequence)
    foreach(jobId aiWorkflowJobs
      job=aiWorkflowJobs[jobId]
      when(and(job member(job['state] list("completed" "failed")))
        sequence=or(job['sequence] 0)
        when(or(!oldestId sequence<oldestSequence)
          oldestId=jobId
          oldestSequence=sequence)))
    when(oldestId
      aiWorkflowCleanupJobArtifacts(aiWorkflowJobs[oldestId])
      remove(oldestId aiWorkflowJobs)
      t)))

procedure(aiWorkflowReserveJobSlot()
  prog((count)
    count=aiWorkflowJobCount()
    while(count>=128
      unless(aiWorkflowEvictOldestTerminal()
        return(nil))
      count=count-1)
    return(t)))

procedure(aiWorkflowCommand(flow path mode dryRun stopAfter python entry)
  let((subcommand originalName command)
    subcommand=case(flow
      ("gds" "stream-gds")
      ("cdl" "export-cdl")
      (t mode))
    originalName=case(flow
      ("rce" "RCE_ORIG_LD_LIBRARY_PATH")
      ("drc" "DRC_ORIG_LD_LIBRARY_PATH")
      (("lvs" "gds" "cdl") "LVS_ORIG_LD_LIBRARY_PATH")
      ("lef" "LEF_ORIG_LD_LIBRARY_PATH"))
    command=strcat(
      "exec env -u PYTHONHOME -u PYTHONPATH -u LD_PRELOAD -u LD_AUDIT "
      aiWorkflowTempEnvironment()
      originalName "="
      aiWorkflowShellQuote(or(aiWorkflowEnv("LD_LIBRARY_PATH") "")) " "
      "LD_LIBRARY_PATH="
      aiWorkflowShellQuote(aiWorkflowLibraryPath()) " "
      SICO_commandInvocation(aiWorkflowCommandName(flow) python entry) " "
      subcommand " " aiWorkflowShellQuote(path))
    when(and(member(flow list("gds" "cdl")) mode=="generate")
      command=strcat(command " --generate-only"))
    when(dryRun command=strcat(command " --dry-run"))
    when(stopAfter
      command=strcat(command " --stop-after " aiWorkflowShellQuote(stopAfter)))
    command))

procedure(aiWorkflowLaunch(flow path mode dryRun stopAfter command)
  prog((key existing jobId runtimeDir logFile launcherFile logPort ipcCommand cid job)
    key=strcat(flow "|" path)
    existing=aiWorkflowRunning[key]
    when(existing
      return(aiWorkflowError("already_running"
        sprintf(nil "%s is already running for this path as %s" flow existing))))
    unless(aiWorkflowReserveJobSlot()
      return(aiWorkflowError("job_limit"
        "128 workflow jobs are still running; wait for one to finish")))
    jobId=aiWorkflowNextId()
    runtimeDir=aiWorkflowCreateRuntimeDir()
    unless(runtimeDir
      return(aiWorkflowError("launch_failed"
        "could not create a private workflow runtime directory")))
    logFile=strcat(runtimeDir "/flow.log")
    launcherFile=strcat(runtimeDir "/launch.sh")
    job=makeTable(strcat("aiWorkflowJob-" jobId) nil)
    job['id]=jobId job['flow]=flow job['path]=path job['mode]=mode
    job['dryRun]=dryRun job['stopAfter]=stopAfter job['log]=logFile
    job['launcher]=launcherFile job['runtime]=runtimeDir job['key]=key
    unless(aiWorkflowWriteLauncher(launcherFile command)
      aiWorkflowCleanupJobArtifacts(job)
      return(aiWorkflowError("launch_failed"
        "could not write the workflow launcher")))
    logPort=outfile(logFile "w")
    unless(logPort
      aiWorkflowCleanupJobArtifacts(job)
      return(aiWorkflowError("launch_failed" "could not create the workflow log")))
    close(logPort)
    job['logBytes]=0 job['logTruncated]=nil
    ipcCommand=strcat("exec /bin/sh " aiWorkflowShellQuote(launcherFile))
    cid=ipcBeginProcess(ipcCommand "" 'aiWorkflowDataHandler
      'aiWorkflowErrHandler 'aiWorkflowPostFunc "")
    unless(cid
      aiWorkflowCleanupJobArtifacts(job)
      return(aiWorkflowError("launch_failed" "ipcBeginProcess failed")))
    job['cid]=cid job['state]="running"
    job['exitStatus]=nil job['sequence]=aiWorkflowState['sequence]
    aiWorkflowJobs[jobId]=job
    aiWorkflowByCid[cid]=jobId
    aiWorkflowRunning[key]=jobId
    ipcActivateBatch(cid)
    return(sprintf(nil
      "{\"ok\":true,\"job_id\":\"%s\",\"state\":\"running\",\"log\":\"%s\"}"
      aiWorkflowJsonEscape(jobId) aiWorkflowJsonEscape(logFile)))))

procedure(aiWorkflowDataHandler(cid data) aiWorkflowAppendLog(cid data))
procedure(aiWorkflowErrHandler(cid data) aiWorkflowAppendLog(cid data))

procedure(aiWorkflowPostFunc(cid exitStatus)
  let((jobId job)
    jobId=aiWorkflowByCid[cid]
    job=aiWorkflowJobs[jobId]
    when(job
      job['exitStatus]=exitStatus
      if(job['terminating] then
        job['state]="failed"
      else
        job['state]=if(exitStatus==0 then "completed" else "failed"))
      when(and(stringp(job['launcher]) isFile(job['launcher]))
        errset(deleteFile(job['launcher]) nil))
      remove(job['key] aiWorkflowRunning))
    remove(cid aiWorkflowByCid)
    t))

procedure(aiWorkflowStart(flow path mode dryRun stopAfter)
  prog((normalizedStop diagnostic resolved toolsRoot python entry command)
    normalizedStop=if(and(stringp(stopAfter) strlen(stopAfter)==0) then nil else stopAfter)
    diagnostic=aiWorkflowValidate(flow mode dryRun normalizedStop)
    when(diagnostic
      return(aiWorkflowError("invalid_params" diagnostic)))
    resolved=aiWorkflowInputPath(path)
    unless(resolved
      return(aiWorkflowError("invalid_path"
        "path must be an absolute, non-empty, accessible file")))
    toolsRoot=aiWorkflowToolsRoot()
    unless(toolsRoot
      return(aiWorkflowError("tools_unavailable" "CAD tools root is unavailable")))
    python=aiWorkflowPython(flow)
    entry=aiWorkflowEntry(flow toolsRoot)
    unless(and(aiWorkflowExecutableP(python) entry)
      return(aiWorkflowError("runtime_unavailable"
        "the selected Python or workflow entry is unavailable")))
    command=aiWorkflowCommand(flow resolved mode dryRun normalizedStop python entry)
    return(aiWorkflowLaunch(flow resolved mode dryRun normalizedStop command))))

procedure(aiWorkflowAnalyzeDspf(path)
  prog((resolved toolsRoot python entry parentPid command)
    resolved=aiWorkflowInputPath(path)
    unless(resolved
      return(aiWorkflowError("invalid_path"
        "path must be an absolute, non-empty, accessible DSPF/SPF file")))
    parentPid=ipcGetPid()
    unless(and(integerp(parentPid) parentPid>1)
      return(aiWorkflowError("owner_unavailable"
        "the owning Virtuoso PID is unavailable")))
    toolsRoot=aiWorkflowToolsRoot()
    python=aiWorkflowPython("dspf")
    entry=aiWorkflowEntry("dspf" toolsRoot)
    unless(and(toolsRoot aiWorkflowExecutableP(python) entry)
      return(aiWorkflowError("runtime_unavailable"
        "the DSPF analyzer Python or entry is unavailable")))
    command=strcat(
      "exec env -u PYTHONHOME -u PYTHONPATH -u LD_PRELOAD -u LD_AUDIT "
      "-u QT_QPA_PLATFORM_PLUGIN_PATH "
      "-u QT_PLUGIN_PATH -u QT_DIR -u QT_SELECT -u QML2_IMPORT_PATH "
      "-u RCE_DSPF_LAYOUT_LIB -u RCE_DSPF_LAYOUT_CELL -u RCE_DSPF_LAYOUT_VIEW "
      aiWorkflowTempEnvironment()
      "RCE_DSPF_PARENT_PID=" aiWorkflowShellQuote(sprintf(nil "%d" parentPid)) " "
      "LD_LIBRARY_PATH="
      aiWorkflowShellQuote(aiWorkflowLibraryPath()) " "
      SICO_commandInvocation("rce" python entry)
      " dspf-gui " aiWorkflowShellQuote(resolved))
    return(aiWorkflowLaunch("dspf" resolved "run" nil nil command))))

procedure(aiWorkflowStatus(jobId)
  prog((job exitValue stopValue)
    unless(aiWorkflowSafeTextP(jobId 128)
      return(aiWorkflowError("invalid_params" "jobId is invalid")))
    job=aiWorkflowJobs[jobId]
    unless(job
      return(aiWorkflowError("job_not_found" "workflow job was not found")))
    exitValue=if(integerp(job['exitStatus]) then
      sprintf(nil "%d" job['exitStatus]) else "null")
    stopValue=if(job['stopAfter] then
      sprintf(nil "\"%s\"" aiWorkflowJsonEscape(job['stopAfter])) else "null")
    return(sprintf(nil
      "{\"ok\":true,\"job_id\":\"%s\",\"flow\":\"%s\",\"path\":\"%s\",\"mode\":\"%s\",\"dry_run\":%s,\"stop_after\":%s,\"state\":\"%s\",\"exit_status\":%s,\"log\":\"%s\",\"log_bytes\":%d,\"log_truncated\":%s}"
      aiWorkflowJsonEscape(job['id]) aiWorkflowJsonEscape(job['flow])
      aiWorkflowJsonEscape(job['path]) aiWorkflowJsonEscape(job['mode])
      if(job['dryRun] then "true" else "false") stopValue
      aiWorkflowJsonEscape(job['state]) exitValue aiWorkflowJsonEscape(job['log])
      or(job['logBytes] 0) if(job['logTruncated] then "true" else "false")))))

procedure(aiWorkflowTerminate(cid)
  let(((terminated t))
    when(and(cid ipcIsAliveProcess(cid))
      errset(ipcSignalProcess(cid 'TERM) nil)
      errset(ipcCloseProcess(cid) nil)
      ipcWait(cid 30 2)
      when(ipcIsAliveProcess(cid)
        errset(ipcKillProcess(cid) nil)
        ipcWait(cid 30 2))
      when(ipcIsAliveProcess(cid) terminated=nil))
    terminated))

procedure(aiWorkflowExitCleanup()
  let((cid cids jobId job)
    foreach(cid aiWorkflowByCid cids=cons(cid cids))
    foreach(cid cids
      jobId=aiWorkflowByCid[cid]
      job=aiWorkflowJobs[jobId]
      when(job
        job['terminating]=t
        job['state]="failed"
        job['exitStatus]=-15)
      errset(aiWorkflowTerminate(cid) nil)
      when(job remove(job['key] aiWorkflowRunning))
      remove(cid aiWorkflowByCid))
    foreach(jobId aiWorkflowJobs
      aiWorkflowCleanupJobArtifacts(aiWorkflowJobs[jobId]))
    t))

procedure(aiWorkflowRegisterExitCleanup()
  unless(aiWorkflowState['exitCleanupRegistered]
    regExitBefore('aiWorkflowExitCleanup)
    aiWorkflowState['exitCleanupRegistered]=t)
  aiWorkflowState['exitCleanupRegistered])

procedure(aiWorkflowRevision()
  "20261020.sico.flow.v7")

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  aiWorkflowRegisterExitCleanup())
aiWorkflowVersion="20261020.sico.flow.v7"
)
