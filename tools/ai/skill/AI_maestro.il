; Bounded Maestro setup and asynchronous simulation helpers for AI Assistant.

unless(and(boundp('aiMaestroVersion)
             aiMaestroVersion=="20260908.ai.maestro.v2"
             boundp('aiMaestroState) aiMaestroState['jobs]
             aiMaestroState['exitCleanupRegistered])

when(and(boundp('aiMaestroState) isCallable('aiMaestroCleanup))
  unless(car(errset(aiMaestroCleanup() nil))
    error("AI Assistant cannot reload while a Maestro session remains open")))
unless(boundp('aiMaestroState)
  aiMaestroState=makeTable("aiMaestroState" nil))
aiMaestroState['jobs]=makeTable("aiMaestroJobs" nil)
aiMaestroState['counter]=0
procedure(aiMaestroMissingCapabilities(names)
  let((missing)
    foreach(name names unless(isCallable(name) missing=cons(name missing)))
    reverse(missing)))
procedure(aiMaestroViewSession(lib cell view)
  prog((protected sessions details)
    protected=errset(maeGetSessions() nil)
    unless(protected return('queryFailed))
    sessions=car(protected)
    foreach(session sessions
      protected=errset(list(axlGetSessionLibName(session)
        axlGetSessionCellName(session) axlGetSessionViewName(session)) nil)
      unless(protected return('queryFailed))
      details=car(protected)
      unless(and(length(details)==3 stringp(car(details))
                   stringp(cadr(details)) stringp(caddr(details)))
        return('queryFailed))
      when(and(car(details)==lib cadr(details)==cell caddr(details)==view)
        return(session)))
    return(nil)))
procedure(aiMaestroApplySpec(outputName testName specs session)
  let((args)
    args=list(outputName testName)
    foreach(pair specs
      args=append(args
        case(car(pair)
          ("minimum" list(?minimum cadr(pair)))
          ("maximum" list(?maximum cadr(pair)))
          ("gt" list(?gt cadr(pair))) ("lt" list(?lt cadr(pair)))
          ("tolerance" list(?tolerance cadr(pair))))))
    apply('maeSetSpec append(args list(?session session)))))

procedure(aiMaestroCreate(setupLib setupCell setupView policy testName tbLib
             tbCell tbView simulator analyses vars modelFiles stimuli outputs corners)
  prog((session tests entry specs envOptions missing validation tbObj job
         cellObj targetExists occupied closeProtected operationOk step result roundTrip)
    policy=if(null(policy) then "error" else policy)
    validation=aiMaestroValidateCreate(setupLib setupCell setupView policy testName
      tbLib tbCell tbView simulator analyses vars modelFiles stimuli outputs corners)
    when(validation return(aiMaestroError("invalid_arguments" validation)))
    missing=aiMaestroMissingCapabilities(
      list('aiJsonEscape 'ddGetObj 'ddIsObjReadable 'ddIsObjWritable 'maeGetSessions
           'axlGetSessionLibName 'axlGetSessionCellName 'axlGetSessionViewName
           'maeOpenSetup 'maeCreateTest 'maeDeleteTest 'maeSetAnalysis 'maeSetVar
           'maeSetEnvOption 'maeAddOutput 'maeSetSpec 'maeSetCorner 'maeGetSetup
           'maeSaveSetup 'maeCloseSession))
    when(missing return(aiMaestroError("unsupported_runtime"
      sprintf(nil "missing SKILL capabilities: %L" missing))))
    tbObj=car(errset(ddGetObj(tbLib tbCell tbView) nil))
    unless(tbObj return(aiMaestroError("testbench_not_found"
      "testbench cellview does not exist")))
    unless(ddIsObjReadable(tbObj) return(aiMaestroError("testbench_not_readable"
      "testbench cellview is not readable by this Virtuoso process")))
    cellObj=car(errset(ddGetObj(setupLib setupCell) nil))
    unless(cellObj return(aiMaestroError("setup_cell_not_found"
      "setup library/cell does not exist")))
    targetExists=car(errset(ddGetObj(setupLib setupCell setupView) nil))
    when(and(targetExists policy=="error") return(aiMaestroError("setup_exists"
      "target setup view exists; use update explicitly")))
    unless(ddIsObjWritable(or(targetExists cellObj))
      return(aiMaestroError("setup_not_writable"
        "target setup view or its parent cell is not writable")))
    when(aiMaestroActiveJobForViewP(setupLib setupCell setupView)
      return(aiMaestroError("setup_in_use" "target setup has a retained AI session")))
    occupied=aiMaestroViewSession(setupLib setupCell setupView)
    when(occupied return(aiMaestroError(
      if(occupied=='queryFailed then "session_query_failed" else "setup_in_use")
      if(occupied=='queryFailed then "could not verify open Maestro sessions"
       else "target setup is already open in this Virtuoso process"))))
    unless(aiMaestroReserveJobSlot() return(aiMaestroError("job_limit"
      "64 Maestro jobs have retained sessions; close one before creating a setup")))
    step="open_setup"
    result=errset(
      unwindProtect(
        progn(
          session=maeOpenSetup(setupLib setupCell setupView
            ?application "Assembler" ?mode "a")
          unless(session error("open setup failed"))
          step="read_tests" tests=maeGetSetup(?typeName "tests" ?session session)
          when(member(testName tests)
            step="delete_existing_test"
            unless(maeDeleteTest(testName ?session session)
              error("delete existing test failed")))
          step="create_test"
          unless(maeCreateTest(testName ?lib tbLib ?cell tbCell ?view tbView
                    ?simulator simulator ?session session) error("create test failed"))
          foreach(entry analyses
            step=strcat("analysis:" car(entry))
            unless(maeSetAnalysis(testName car(entry) ?enable cadr(entry)
                      ?options caddr(entry) ?session session) error("set analysis failed")))
          foreach(entry vars
            step=strcat("variable:" car(entry))
            unless(maeSetVar(car(entry) cadr(entry) ?session session)
              error("set variable failed")))
          envOptions=nil
          when(modelFiles
            envOptions=append(envOptions list(list("modelFiles" modelFiles))))
          when(stimuli envOptions=append(envOptions list(list("stimulusFile" stimuli))))
          when(envOptions step="environment_options"
            unless(maeSetEnvOption(testName ?options envOptions ?session session)
              error("set environment options failed")))
          foreach(entry outputs
            step=strcat("output:" car(entry))
            if(caddr(entry) then
              unless(maeAddOutput(car(entry) testName ?outputType cadr(entry)
                        ?signalName caddr(entry) ?plot nth(4 entry) ?save nth(5 entry)
                        ?session session) error("add signal output failed"))
            else
              unless(maeAddOutput(car(entry) testName ?outputType cadr(entry)
                        ?expr cadddr(entry) ?plot nth(4 entry) ?save nth(5 entry)
                        ?session session) error("add expression output failed")))
            specs=nth(6 entry)
            when(specs step=strcat("spec:" car(entry))
              unless(aiMaestroApplySpec(car(entry) testName specs session)
                error("set output spec failed"))))
          foreach(entry corners
            step=strcat("corner:" car(entry))
            unless(maeSetCorner(car(entry) ?enabled cadr(entry)
                      ?enableTests caddr(entry) ?disableTests cadddr(entry)
                      ?session session) error("set corner failed"))
            foreach(var nth(4 entry)
              step=strcat("corner_variable:" car(var))
              unless(maeSetVar(car(var) cadr(var) ?typeName "corner"
                        ?typeValue list(car(entry)) ?session session)
                error("set corner variable failed"))))
          step="round_trip" roundTrip=maeGetSetup(?typeName "tests" ?session session)
          unless(member(testName roundTrip) error("test round trip failed"))
          step="save_setup"
          unless(maeSaveSetup(?session session) error("save setup failed"))
          operationOk=t)
        when(session
          closeProtected=errset(maeCloseSession(?session session
            ?forceClose if(operationOk then nil else t)) nil)
          when(and(closeProtected car(closeProtected)) session=nil))) t)
    unless(result
      when(session job=aiMaestroNewJob(session setupLib setupCell setupView "failed")
        job['error]=sprintf(nil "create operation failed at %s" step))
      return(aiMaestroError("maestro_create_failed"
        sprintf(nil "operation failed at %s; use stop_maestro_simulation for a returned job_id" step) if(job then job['id] else nil))))
    unless(and(closeProtected car(closeProtected))
      job=aiMaestroNewJob(session setupLib setupCell setupView "failed")
      job['error]="created setup but could not close its Maestro session"
      return(aiMaestroError("session_close_failed"
        "session retained; call stop_maestro_simulation with this job_id" job['id])))
    return(sprintf(nil
      "{\"ok\":true,\"operation\":\"create\",\"policy\":%s,\"setup\":{\"lib\":%s,\"cell\":%s,\"view\":%s},\"test\":%s}"
      aiMaestroJsonString(policy 16) aiMaestroJsonString(setupLib 256)
      aiMaestroJsonString(setupCell 256) aiMaestroJsonString(setupView 256)
      aiMaestroJsonString(testName 256)))))
procedure(aiMaestroActiveJobForViewP(lib cell view)
  let((job active)
    foreach(jobId aiMaestroState['jobs]
      job=aiMaestroState['jobs][jobId]
      when(and(job job['session]
                 job['lib]==lib job['cell]==cell job['view]==view) active=t))
    active))
procedure(aiMaestroNewJob(session lib cell view state)
  let((job jobId)
    aiMaestroState['counter]=aiMaestroState['counter]+1
    jobId=sprintf(nil "maestro-%d" aiMaestroState['counter]) job=makeTable("aiMaestroJob" nil)
    job['id]=jobId job['state]=state job['session]=session job['messages]=""
    job['lib]=lib job['cell]=cell job['view]=view job['sequence]=aiMaestroState['counter]
    job['createdAt]=getCurrentTime() aiMaestroState['jobs][jobId]=job job))
procedure(aiMaestroReserveJobSlot()
  prog((count job oldest oldestId fresh)
    count=0
    foreach(jobId aiMaestroState['jobs]
      count=count+1 job=aiMaestroState['jobs][jobId]
      when(and(job !job['session]
                 member(job['state] list("completed" "failed" "stopped" "aborted"))
                 or(!oldest job['sequence]<oldest['sequence]))
        oldest=job oldestId=jobId))
    when(count>=64
      unless(oldestId return(nil))
      fresh=makeTable("aiMaestroJobsPruned" nil)
      foreach(jobId aiMaestroState['jobs]
        unless(jobId==oldestId fresh[jobId]=aiMaestroState['jobs][jobId]))
      aiMaestroState['jobs]=fresh)
    return(t)))
procedure(aiMaestroFindJobBySession(session)
  let((job found)
    foreach(jobId aiMaestroState['jobs]
      job=aiMaestroState['jobs][jobId]
      when(and(job job['session]==session
                 member(job['state] list("starting" "running"))) found=job))
    found))
procedure(aiMaestroQueueFinish(job)
  let((queued)
    queued=hiEnqueueCmd(sprintf(nil "aiMaestroFinishPending(%L)" job['id]))
    job['finishQueued]=queued
    unless(queued job['state]="failed"
      job['error]="could not schedule simulation completion cleanup") queued))
procedure(aiMaestroChecksFailedP(checks)
  let((failed)
    foreach(check checks
      when(and(listp(check) length(check)>=3 nth(2 check)=="FAIL") failed=t))
    failed))
procedure(aiMaestroProgressCompleteP(progress)
  and(listp(progress) length(progress)==2
      numberp(car(progress)) numberp(cadr(progress))
      cadr(progress)>0 car(progress)==cadr(progress)))
procedure(aiMaestroFinishJob(job)
  let((session protected checkResult messageResult closeResult checked completed)
    session=job['session] job['completionPending]=nil
    protected=errset(
      unwindProtect(
        progn(
          ; IC6.1.8 provides axlGetRunStatus but not maeCheckSimStatus.
          ; Query the optional API only after opening/running the setup, since
          ; newer releases may load it lazily. Never synthesize per-corner PASS.
          when(isCallable('maeCheckSimStatus)
            checked=errset(maeCheckSimStatus(session job['history]) nil)
            when(checked checkResult=car(checked)))
          if(checkResult then
            job['statusSource]="maeCheckSimStatus"
            completed=!aiMaestroChecksFailedP(checkResult)
          else
            job['statusSource]="axlGetRunStatus"
            job['progress]=axlGetRunStatus(session ?historyName job['history])
            completed=aiMaestroProgressCompleteP(job['progress])
            unless(completed
              job['error]="completion callback received but completed/total points do not confirm completion"))
          messageResult=maeGetSimulationMessages(?session session ?msgType "ERROR")
          job['checks]=checkResult
          job['messages]=aiMaestroBoundText(or(messageResult "") 4096)
          job['state]=if(or(!completed
                              strlen(job['messages])>0) then "failed" else "completed"))
        progn(
          closeResult=errset(maeCloseSession(?session session ?simulation "wait") nil)
          when(and(closeResult car(closeResult)) job['session]=nil))) t)
    unless(protected job['state]="failed"
      job['error]="simulation completion callback failed")
    unless(and(closeResult car(closeResult)) job['state]="failed"
      job['error]="simulation completed but the Maestro session did not close")
    job['completedAt]=getCurrentTime() t))
procedure(aiMaestroRunCallback(session runId)
  let((job)
    job=aiMaestroFindJobBySession(session)
    when(job job['runId]=runId job['completionPending]=t
      when(job['history] aiMaestroQueueFinish(job)))
    t))
procedure(aiMaestroFinishPending(jobId)
  let((job)
    job=aiMaestroState['jobs][jobId]
    when(job job['finishQueued]=nil
      when(and(job['completionPending] job['history] job['session]
                 member(job['state] list("starting" "running")))
        aiMaestroFinishJob(job)))
    t))
procedure(aiMaestroJobJson(job)
  sprintf(nil
    "{\"ok\":true,\"job_id\":%s,\"state\":%s,\"history\":%s,\"run_id\":%s,\"progress\":%s,\"checks\":%s,\"status_source\":%s,\"messages\":%s,\"error\":%s,\"session_open\":%s}"
    aiMaestroJsonString(job['id] 96) aiMaestroJsonString(job['state] 32)
    if(job['history] then aiMaestroJsonString(job['history] 256) else "null")
    aiMaestroValueJson(job['runId] 0) aiMaestroValueJson(job['progress] 0)
    aiMaestroValueJson(job['checks] 0) aiMaestroValueJson(job['statusSource] 0)
    aiMaestroJsonString(or(job['messages] "") 4096)
    if(job['error] then aiMaestroJsonString(job['error] 1024) else "null")
    if(job['session] then "true" else "false")))
procedure(aiMaestroRun(lib cell view runMode)
  prog((missing modeName target session job jobId result history occupied closeResult)
    unless(and(aiMaestroTextP(lib 256) aiMaestroTextP(cell 256)
                 aiMaestroTextP(view 256))
      return(aiMaestroError("invalid_arguments" "lib, cell, and view are required")))
    modeName=case(runMode
      ("single" "Single Run, Sweeps and Corners")
      ("monte_carlo" "Monte Carlo Sampling") (t nil))
    unless(modeName return(aiMaestroError("invalid_run_mode"
      "runMode must be single or monte_carlo")))
    missing=aiMaestroMissingCapabilities(
      list('aiJsonEscape 'ddGetObj 'maeGetSessions 'axlGetSessionLibName
           'axlGetSessionCellName 'axlGetSessionViewName 'maeOpenSetup
           'maeRunSimulation 'maeStopSimulation
           'maeGetSimulationMessages 'axlGetRunStatus 'maeCloseSession
           'hiEnqueueCmd))
    when(missing return(aiMaestroError("unsupported_runtime"
      sprintf(nil "missing SKILL capabilities: %L" missing))))
    target=car(errset(ddGetObj(lib cell view) nil))
    unless(target return(aiMaestroError("setup_not_found"
      "Maestro setup view does not exist")))
    when(aiMaestroActiveJobForViewP(lib cell view)
      return(aiMaestroError("run_active" "this setup already has an active job")))
    occupied=aiMaestroViewSession(lib cell view)
    when(occupied return(aiMaestroError(
      if(occupied=='queryFailed then "session_query_failed" else "setup_in_use")
      if(occupied=='queryFailed then "could not verify open Maestro sessions"
       else "target setup is already open in this Virtuoso process"))))
    unless(aiMaestroReserveJobSlot() return(aiMaestroError("job_limit"
      "64 Maestro jobs are active; wait for one to finish")))
    result=errset(maeOpenSetup(lib cell view ?mode "a") nil)
    unless(and(result car(result)) return(aiMaestroError("open_setup_failed"
      "could not open Maestro setup")))
    session=car(result) job=aiMaestroNewJob(session lib cell view "running")
    jobId=job['id] job['runMode]=runMode
    result=errset(maeRunSimulation(?session session ?runMode modeName
      ?callback "aiMaestroRunCallback") nil)
    unless(and(result stringp(car(result)) strlen(car(result))>0)
      job['state]="failed" job['error]="maeRunSimulation failed"
      closeResult=errset(maeCloseSession(?session session ?forceClose t) nil)
      when(and(closeResult car(closeResult)) job['session]=nil)
      return(aiMaestroError("run_start_failed" "simulation did not start; if session_open, call stop_maestro_simulation with this job_id" jobId)))
    history=car(result) job['history]=history
    when(and(job['completionPending] !job['finishQueued])
      aiMaestroQueueFinish(job))
    return(aiMaestroJobJson(job))))
procedure(aiMaestroStatus(jobId)
  let((job result)
    if(!aiMaestroTextP(jobId 96) then
      aiMaestroError("invalid_job_id" "jobId must be a bounded string")
    else job=aiMaestroState['jobs][jobId]
      if(!job then aiMaestroError("job_not_found" "Maestro job does not exist")
      else
        when(and(job['state]=="running" job['session] job['history])
          result=errset(axlGetRunStatus(job['session] ?historyName job['history]) nil)
          when(result job['progress]=car(result)))
        aiMaestroJobJson(job)))))
procedure(aiMaestroStop(jobId)
  prog((job stopResult closeResult session previousState)
    unless(aiMaestroTextP(jobId 96)
      return(aiMaestroError("invalid_job_id" "jobId must be a bounded string")))
    job=aiMaestroState['jobs][jobId]
    unless(job return(aiMaestroError("job_not_found" "Maestro job does not exist")))
    unless(and(member(job['state] list("starting" "running" "failed")) job['session])
      return(aiMaestroError("job_not_running" "Maestro job is not running")))
    session=job['session] previousState=job['state] job['state]="stopping"
    stopResult=errset(
      if(job['history] then
        maeStopSimulation(?historyName list(job['history]) ?session session)
      else maeStopSimulation(?session session)) nil)
    closeResult=errset(maeCloseSession(?session session ?forceClose t) nil)
    unless(and(closeResult car(closeResult))
      job['state]=previousState
      job['error]="could not force-close Maestro session"
      return(aiMaestroError("stop_failed" job['error])))
    job['session]=nil job['completionPending]=nil job['finishQueued]=nil
    job['state]="stopped" job['completedAt]=getCurrentTime()
    unless(and(stopResult car(stopResult))
      job['messages]="maeStopSimulation returned nil; the session was force-closed")
    return(aiMaestroJobJson(job))))
procedure(aiMaestroCleanup()
  let(((clean t) job closeResult)
    when(and(boundp('aiMaestroState) aiMaestroState['jobs])
      foreach(jobId aiMaestroState['jobs]
        job=aiMaestroState['jobs][jobId]
        when(and(job job['session])
          when(isCallable('maeStopSimulation)
            errset(if(job['history] then
              maeStopSimulation(?historyName list(job['history]) ?session job['session])
            else maeStopSimulation(?session job['session])) nil))
          closeResult=errset(maeCloseSession(?session job['session] ?forceClose t) nil)
          if(and(closeResult car(closeResult)) then
            progn(job['session]=nil
              when(member(job['state] list("starting" "running" "stopping"))
                job['state]="aborted"))
          else clean=nil)))
    clean)))
procedure(aiMaestroRegisterExitCleanup()
  unless(aiMaestroState['exitCleanupRegistered]
    regExitBefore('aiMaestroCleanup) aiMaestroState['exitCleanupRegistered]=t)
  aiMaestroState['exitCleanupRegistered])
unless(and(boundp('cadContextBuilding) cadContextBuilding)
  aiMaestroRegisterExitCleanup())
procedure(aiMaestroRevision() "20260908.ai.maestro.v2")
aiMaestroVersion="20260908.ai.maestro.v2"
)
