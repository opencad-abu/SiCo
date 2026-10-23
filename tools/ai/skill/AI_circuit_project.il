; Project observations and session-bound recovery. Writes require a durable claim.
unless(boundp('aiProjectIdentity) aiProjectIdentity=nil)
unless(boundp('aiProjectClaims) aiProjectClaims=makeTable("aiProjectClaims" nil))
unless(boundp('aiProjectClaimCount) aiProjectClaimCount=0)
procedure(aiProjectRevision() "20260911.ai.project.v1")
procedure(aiProjectSession(apsCandidate)
  unless(aiProjectIdentity aiProjectIdentity=apsCandidate)
  aiCircuitObject(list(list("ok" t) list("session_id" aiProjectIdentity)
    list("pid" ipcGetPid()) list("working_directory" getWorkingDir())
    list("tool_version" getVersion()) list("backend_revision" aiProjectRevision())
    list("forced_cds_lib" ddGetForcedLib()) list("system_temp_directory" getTempDir())
    list("configured_temp_directory" getShellEnvVar("TMPDIR")))))
procedure(aiProjectRequire(aprIdentity)
  unless(and(aiProjectIdentity aprIdentity==aiProjectIdentity)
    error("circuit project session changed; no dispatch")) t)
procedure(aiProjectAssertClaim(apaIdentity apaKey apaDigest)
  aiProjectRequire(apaIdentity)
  unless(equal(aiProjectClaims[apaKey] apaDigest) error("operation claim changed; no dispatch")) t)
procedure(aiProjectClaim(apcIdentity apcKey apcDigest)
  prog((apcPrevious)
    aiProjectRequire(apcIdentity)
    apcPrevious=aiProjectClaims[apcKey]
    when(apcPrevious
      return(aiCircuitError("operation_already_claimed" "Query retained operation; never redispatch")))
    when(aiRcRequests[apcKey]
      return(aiCircuitError("unjournalled_request" "Native request predates journal; inspect it directly")))
    when(aiProjectClaimCount>=256 return(aiCircuitError("operation_limit" "256 claims retained")))
    aiProjectClaims[apcKey]=apcDigest aiProjectClaimCount=aiProjectClaimCount+1
    return(aiCircuitObject(list(list("ok" t))))) )
procedure(aiProjectTarget(aptTarget)
  prog((aptLib aptCell aptView aptParent aptObj aptModified aptType aptOpen)
    aptLib=car(aptTarget) aptCell=cadr(aptTarget) aptView=caddr(aptTarget)
    aptObj=ddGetObj(aptLib aptCell aptView)
    aptParent=or(ddGetObj(aptLib aptCell) ddGetObj(aptLib))
    when(aptObj aptType=aiCircuitViewType(aptLib aptCell aptView))
    foreach(aptCv dbGetOpenCellViews()
      when(aiCircuitCvMatches(aptCv aptTarget)
        aptOpen=t aptType=aptCv~>cellViewType when(dbIsCellViewModified(aptCv) aptModified=t)))
    when(isCallable('maeGetSessions)
      foreach(aptSession aiCircuitSessions(list(aptLib aptCell aptView))
        aptOpen=t when(maeIsSetupModified(?session aptSession) aptModified=t)))
    return(list(aptTarget aiCircuitBool(aptObj) aptType
      aiCircuitBool(and(aptObj ddIsObjReadable(aptObj)))
      aiCircuitBool(and(aptParent ddIsObjWritable(aptParent)))
      aiCircuitBool(aptOpen) aiCircuitBool(aptModified)))))
procedure(aiProjectObserve(apoTargets apoLibs apoMode)
  prog((apoRows apoLibraries apoObj)
    foreach(apoTarget apoTargets apoRows=cons(aiProjectTarget(apoTarget) apoRows))
    foreach(apoLib apoLibs
      apoObj=ddGetObj(apoLib)
      apoLibraries=cons(list(apoLib when(apoObj ddGetObjReadPath(apoObj))) apoLibraries))
    return(aiCircuitObject(list(list("ok" t)
      list("gui_available" aiCircuitBool(aiCircuitGuiP()))
      list("libraries" reverse(apoLibraries)) list("targets" reverse(apoRows))
      list("missing_apis" aiCircuitMissing(append(
        list('schCreateInst 'schCreatePin 'schCreateWire 'schCheck 'dbSave
          'schCreateSymbolShape 'schCreateInstBox 'hdbOpen 'hdbSave
          'maeOpenSetup 'maeCreateTest 'maeSetAnalysis 'maeRunSimulation)
        if(apoMode=="foreground" list('deOpenCellView) nil)))))))) )
procedure(aiProjectReceipt(aprIdentity aprKey aprDigest aprKind)
  prog((aprRecord aprEntry aprReply aprTarget aprObserved aprRun aprChecked aprLibrary)
    aiProjectRequire(aprIdentity)
    unless(equal(aiProjectClaims[aprKey] aprDigest)
      return(aiCircuitError("claim_missing_or_changed" "No matching native claim; status unknown")))
    aprRecord=aiRcRequests[aprKey]
    when(aprRecord
      aprEntry=aprRecord['entry]
      cond((aprKind=="run_simulation_setup"
          aprRun=aprRecord['run]
          when(aprRun
            aprEntry=aiSimulationSetups[aiSimulationRuns[aprRun]['setup]]
            aprReply=aiSimStatus(aprRun)
            aprChecked=errset(progn(
              unless(aiSimSession(aprEntry)==aiSimulationRuns[aprRun]['session]
                error("exact run session changed")) t) nil)))
        (aprKind=="create_circuit_library"
          when(aprEntry
            aprReply=aiLibraryReply(aprEntry)
            aprLibrary=errset(aiLibraryObservation(aprEntry['lib]) nil)))
        (aprKind=="create_simulation_setup"
          when(aprEntry aprReply=aiSimReply(aprEntry)))
        (aprKind=="apply_cdf_update"
          when(aprEntry aprReply=aiCdfUpdateReply(aprEntry)))
        (aprKind=="create_circuit_from_plan"
          when(aprEntry aprReply=aiCrReply(aprEntry)))
        (aprKind=="edit_circuit"
          when(aprEntry aprReply=aiCrReply(aprEntry)))
        (aprKind=="create_circuit_config"
          when(aprEntry aprReply=aiConfigReply(aprEntry)))
        (member(aprKind list("draw_symbol" "create_template_symbol"))
          when(aprEntry aprReply=aiTsReply(aprEntry)))))
    when(and(aprEntry aprKind!="create_circuit_library")
      aprTarget=cond((member(aprKind list("create_simulation_setup" "run_simulation_setup" "create_circuit_from_plan" "edit_circuit"))
          car(aprEntry['payload]))
        (member(aprKind list("create_circuit_config" "apply_cdf_update")) aprEntry['target])
        (t list(aprEntry['lib] aprEntry['cell] "symbol")))
      aprObserved=errset(aiProjectTarget(aprTarget) nil))
    return(aiCircuitObject(list(list("ok" t) list("receipt_json" aprReply)
      list("target_observation" car(aprObserved))
      list("library_observation" car(aprLibrary))
      list("run_context_checked" aiCircuitBool(and(aprRun aprChecked)))
      list("live_integrity_verified" 'jsonFalse)
      list("automatic_resume_allowed" 'jsonFalse))))) )

; Retain library setup alongside the existing request journal; loading creates no data.
unless(boundp('aiLibraryPrepares) aiLibraryPrepares=makeTable("aiLibraryPrepares" nil))
procedure(aiLibraryTechPath(altLib)
  prog((altTech altFile)
    altTech=techGetTechFile(altLib)
    unless(altTech error("Library has no readable technology database"))
    altFile=techGetTechFileDdId(altTech)
    unless(altFile error("Technology database path unavailable"))
    return(simplifyFilename(ddGetObjReadPath(altFile)))))
procedure(aiLibraryMapping(almWorkspace)
  ; ddGetForcedLib()/ddGetUpdatedLib() report "" when unset on some versions,
  ; and SKILL treats "" as present. Decide on string content, not truthiness.
  prog((almValue)
    almValue=ddGetForcedLib()
    when(stringp(almValue) unless(strlen(almValue)>0 almValue=nil))
    when(almValue==nil
      almValue=ddGetUpdatedLib()
      when(stringp(almValue) unless(strlen(almValue)>0 almValue=nil)))
    when(almValue==nil almValue=strcat(almWorkspace "/cds.lib"))
    return(simplifyFilename(almValue))))
procedure(aiLibraryViewTypes(avtLib)
  ; Bounded distinct view types; the user's re-attach decision binds to this list.
  prog((avtName avtTypes avtCells avtCell avtCellName avtView avtViewName avtFile avtType avtTruncated)
    avtName=avtLib~>name avtTypes=nil avtCells=0
    ; ~>cells/~>views can hand back objects, not only names; adapt like the target lister.
    foreach(avtCell car(errset(avtLib~>cells nil))
      if(avtCells>=256 then avtTruncated=t return())
      avtCells=avtCells+1
      avtCellName=if(stringp(avtCell) avtCell avtCell~>name)
      let((avtViews avtView)
        avtViews=0
        foreach(avtView car(errset(ddGetObj(avtName avtCellName)~>views nil))
          if(avtViews>=64 then avtTruncated=t return())
          avtViews=avtViews+1
          avtViewName=if(stringp(avtView) avtView avtView~>name)
          avtFile=ddGetObj(avtName avtCellName avtViewName "*")
          avtType=car(errset(ddMapGetFileViewType(avtFile) t))
          when(stringp(avtType)
            unless(member(avtType avtTypes) avtTypes=cons(avtType avtTypes))))))
    when(avtTruncated avtTypes=cons("truncated" avtTypes))
    ; Build with cons/reverse like the target lister; tconc here nests the passed list.
    return(reverse(avtTypes))))
procedure(aiLibraryObservation(aloName)
  prog((aloLib)
    aloLib=ddGetObj(aloName)
    return(list(aloName when(aloLib simplifyFilename(ddGetObjReadPath(aloLib)))
      when(aloLib car(errset(aiLibraryTechPath(aloLib) nil)))))))
procedure(aiLibraryFacts(alfName alfSelected alfWorkspace alfReattach)
  prog((alfLib alfSource alfTech alfTechLib alfTechPath alfPath alfMapping
        alfCurrentTech alfViews)
    unless(and(aiCircuitTextP(alfName) strlen(alfName)<=96
      rexMatchp("^[A-Za-z_][A-Za-z0-9_.-]*$" alfName)
      stringp(alfWorkspace) substring(alfWorkspace 1 1)=="/"
      simplifyFilename(getWorkingDir())==alfWorkspace isDir(alfWorkspace))
      error("Library project or name is invalid"))
    when(alfReattach
      unless(aiCircuitTextP(alfSelected)
        error("Reattach requires a selected technology")))
    when(aiCircuitTextP(alfSelected)
      alfSource=ddGetObj(alfSelected)
      unless(alfSource error("Selected process library unavailable"))
      alfTech=techGetTechFile(alfSource)
      unless(alfTech error("Selected process has no readable technology database"))
      alfTechLib=ddGetObjLib(techGetTechFileDdId(alfTech))
      alfTechPath=aiLibraryTechPath(alfSource))
    alfLib=ddGetObj(alfName)
    if(alfLib then
      alfPath=simplifyFilename(ddGetObjReadPath(alfLib))
      ; A user-approved re-attach prepares an existing writable library whose
      ; technology differs; execution re-attaches and verifies it.
      if(alfReattach && ddIsObjWritable(alfLib) && alfLib!=alfSource && alfLib!=alfTechLib then
        alfCurrentTech=car(errset(aiLibraryTechPath(alfLib) nil))
        unless(stringp(alfCurrentTech) && alfCurrentTech!=alfTechPath
          alfCurrentTech=nil)
        when(alfCurrentTech alfViews=aiLibraryViewTypes(alfLib)))
      unless(alfCurrentTech
        if(alfSelected then
          unless(and(ddIsObjWritable(alfLib) aiLibraryTechPath(alfLib)==alfTechPath
            alfLib!=alfSource alfLib!=alfTechLib)
            error("Target library is a process library, unwritable or uses another technology"))
          unless(ddIsObjWritable(alfLib)
            error("Target library is a process library or unwritable"))))
    else
      alfPath=strcat(alfWorkspace "/" alfName)
      when(or(isFile(alfPath) isDir(alfPath)) error("Unregistered target path already exists"))
      alfMapping=aiLibraryMapping(alfWorkspace)
      unless(and(alfMapping==strcat(alfWorkspace "/cds.lib")
        isWritable(alfWorkspace) or(!isFile(alfMapping) isWritable(alfMapping)))
        error("Project cds.lib must be local and writable for library creation")))
    return(list(alfPath aiCircuitBool(alfLib)
      when(alfTechLib alfTechLib~>name) alfTechPath
      when(alfTechLib simplifyFilename(ddGetObjReadPath(alfTechLib))) alfMapping
      when(alfSource simplifyFilename(ddGetObjReadPath(alfSource)))
      alfCurrentTech alfViews))))
procedure(aiLibraryCheck(alcEntry)
  unless(and(alcEntry aiCircuitTasks[alcEntry['task]]==alcEntry['mode]
    equal(alcEntry['facts] aiLibraryFacts(alcEntry['lib] alcEntry['selected] alcEntry['workspace]
      alcEntry['reattach])))
    error("Library preparation changed; no write")) t)
procedure(aiLibraryBinding(albEntry)
  list(albEntry['task] albEntry['mode] list(albEntry['lib] "library" "library")
    "library" albEntry['prepare] car(albEntry['facts]) albEntry['workspace]))
procedure(aiLibraryRearmHint(alhName alhSelected)
  ; Discoverability hint for the reviewed re-attach option; never writes.
  prog((alhLib alhSource alhCurrent alhTarget)
    alhLib=ddGetObj(alhName)
    alhSource=ddGetObj(alhSelected)
    unless(alhLib && alhSource return(""))
    unless(ddIsObjWritable(alhLib) return(""))
    alhCurrent=car(errset(aiLibraryTechPath(alhLib) nil))
    alhTarget=car(errset(aiLibraryTechPath(alhSource) nil))
    unless(stringp(alhCurrent) && stringp(alhTarget) && alhCurrent!=alhTarget return(""))
    return("; an existing writable library with another technology can be re-attached only after the user explicitly chooses it (reattach_technology=true)")))
procedure(aiLibraryPrepare(alpTask alpId alpName alpSelected alpWorkspace alpReattach)
  prog((alpRef alpRecord alpEntry alpFacts alpResult alpIndex)
    alpRef=strcat("library-prepare:" alpId)
    alpRecord=aiRcReserve(alpRef list(alpTask alpName alpSelected alpWorkspace alpReattach))
    when(alpRecord['reply] return(alpRecord['reply]))
    alpResult=errset(progn(
      unless(aiCircuitTasks[alpTask] error("Circuit task required"))
      unless(alpName
        alpIndex=0 alpName=strcat("circuit_" alpId)
        while(or(ddGetObj(alpName) isFile(strcat(alpWorkspace "/" alpName))
          isDir(strcat(alpWorkspace "/" alpName)))
          alpIndex=alpIndex+1
          when(alpIndex>128 error("No available project library name"))
          alpName=sprintf(nil "circuit_%s_%d" alpId alpIndex)))
      alpFacts=aiLibraryFacts(alpName alpSelected alpWorkspace alpReattach)
      alpEntry=makeTable("aiLibraryPrepare" nil)
      alpEntry['prepare]=alpRef alpEntry['task]=alpTask alpEntry['mode]=aiCircuitTasks[alpTask]
      alpEntry['lib]=alpName alpEntry['selected]=alpSelected alpEntry['workspace]=alpWorkspace
      alpEntry['reattach]=when(nth(7 alpFacts) t)
      alpEntry['facts]=alpFacts aiLibraryPrepares[alpRef]=alpEntry t) t)
    alpRecord['reply]=if(alpResult
      aiCircuitObject(list(list("ok" t) list("prepare_ref" alpRef)
        list("library" alpName) list("library_path" car(alpFacts))
        list("technology_library" nth(2 alpFacts)) list("technology_path" nth(3 alpFacts))
        list("current_technology_path"
          if(nth(7 alpFacts) nth(7 alpFacts) "not_applicable"))
        list("library_view_types"
          if(nth(7 alpFacts) if(nth(8 alpFacts) nth(8 alpFacts) "empty") "not_scanned"))
        list("reattach_required" aiCircuitBool(nth(7 alpFacts)))
        list("library_ready" cadr(alpFacts)) list("user_input_required" 'jsonFalse)
        list("next_action" if(nth(7 alpFacts) "create_circuit_library"
          if(eq(cadr(alpFacts) t) "preflight_circuit_project" "create_circuit_library")))))
      aiCircuitError("library_preparation_failed"
        strcat("Cannot prepare the target library: check project library mapping, write access and selected technology compatibility"
          aiLibraryRearmHint(alpName alpSelected))))
    return(alpRecord['reply])))
procedure(aiLibraryReply(alrEntry)
  aiCircuitObject(list(list("ok" aiCircuitBool(!alrEntry['error]))
    list("library_ref" alrEntry['ref]) list("library" alrEntry['lib])
    list("library_path" car(alrEntry['facts]))
    list("technology_library" nth(2 alrEntry['facts]))
    list("technology_path" nth(3 alrEntry['facts]))
    list("previous_technology_path"
      if(nth(7 alrEntry['facts]) nth(7 alrEntry['facts]) "not_applicable"))
    list("created" aiCircuitBool(alrEntry['created])) list("saved" aiCircuitBool(alrEntry['saved]))
    list("reattached" aiCircuitBool(alrEntry['reattached]))
    list("stage" alrEntry['stage]) list("error" alrEntry['error]))))
procedure(aiLibraryCreate(alxPrepare alxId)
  prog((alxKey alxRecord alxPrepared alxEntry alxResult alxLib
        alxExpected alxObserved)
    alxKey=strcat("library-create:" alxId)
    unless(and(boundp('aiProjectDispatchKey) aiProjectDispatchKey==alxKey
      aiProjectClaims[alxKey] aiProjectDispatchDigest==aiProjectClaims[alxKey])
      error("Library creation requires durable operation dispatch"))
    alxRecord=aiRcReserve(alxKey list(alxPrepare))
    when(alxRecord['entry] return(aiLibraryReply(alxRecord['entry])))
    alxPrepared=aiLibraryPrepares[alxPrepare]
    aiLibraryCheck(alxPrepared)
    alxEntry=makeTable("aiLibraryCreated" nil)
    foreach(alxField list('task 'mode 'lib 'selected 'workspace 'facts 'reattach)
      alxEntry[alxField]=alxPrepared[alxField])
    alxEntry['ref]=strcat("library:" alxId) alxEntry['stage]="validated"
    alxRecord['entry]=alxEntry
    alxResult=errset(progn(
      alxLib=ddGetObj(alxEntry['lib])
      if(alxEntry['reattach] then
        unless(alxLib error("Prepared library vanished before re-attach"))
        alxEntry['stage]="reattaching_technology"
        unless(techBindTechFile(alxLib nth(2 alxEntry['facts]))
          error("Technology re-attachment failed"))
        alxEntry['reattached]=t
      else
        unless(alxLib
          alxEntry['stage]="creating_library"
          alxLib=ddCreateLib(alxEntry['lib] car(alxEntry['facts]))
          unless(alxLib error("Library creation failed"))
          alxEntry['created]=t)
        when(nth(2 alxEntry['facts])
          alxEntry['stage]="attaching_technology"
          unless(techBindTechFile(alxLib nth(2 alxEntry['facts]))
            error("Technology attachment failed"))))
      alxEntry['stage]="verifying"
      alxExpected=list(alxEntry['lib] car(alxEntry['facts]) nth(3 alxEntry['facts]))
      alxObserved=aiLibraryObservation(alxEntry['lib])
      unless(if(nth(2 alxEntry['facts])
        equal(alxObserved alxExpected)
        equal(list(car(alxObserved) cadr(alxObserved))
          list(car(alxExpected) cadr(alxExpected))))
        error("Library readback differs"))
      alxEntry['saved]=t alxEntry['stage]="ready" t) t)
    unless(alxResult alxEntry['error]="Library setup incomplete; inspect the retained library before any further write")
    return(aiLibraryReply(alxEntry))))
