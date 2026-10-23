; Recover the supplied reference through public Maestro Save As, never OA file copy.
unless(boundp('aiOpampEntries) aiOpampEntries=makeTable("aiOpampEntries" nil))
unless(boundp('aiOpampRuns) aiOpampRuns=makeTable("aiOpampRuns" nil))
procedure(aiOpampRevision() "20260910.ai.opamp.v3")

procedure(aiOpampSetupSnapshot(aosSession)
  list(
    foreach(mapcar aosKind list("tests" "variables" "corners")
      list(aosKind maeGetSetup(?typeName aosKind ?session aosSession)
        maeGetSetup(?typeName aosKind ?enabled t ?session aosSession)))
    foreach(mapcar aosVar maeGetSetup(?typeName "variables" ?session aosSession)
      list(aosVar maeGetVar(aosVar ?session aosSession)))
    foreach(mapcar aosTest maeGetSetup(?typeName "tests" ?session aosSession)
      let((aosTs)
        aosTs=maeGetTestSession(aosTest ?session aosSession)
        list(aosTest list(asiGetDesignLibName(aosTs) asiGetDesignCellName(aosTs)
          asiGetDesignViewName(aosTs) asiGetSimName(aosTs))
          copy(maeGetEnvOption(aosTest ?session aosSession))
          foreach(mapcar aosVar maeGetSetup(?typeName "variables" ?session aosSession)
            list(aosVar maeGetVar(aosVar ?typeName "test" ?typeValue aosTest ?session aosSession)))
          foreach(mapcar aosAna maeGetEnabledAnalysis(aosTest ?session aosSession)
            list(aosAna copy(maeGetAnalysis(aosTest aosAna ?session aosSession))))
          foreach(mapcar aosOut maeGetTestOutputs(aosTest ?session aosSession)
            list(aosOut->name copy(aosOut->expression) aosOut->signal aosOut->plot aosOut->save aosOut->evalType)))))
    copy(maeGetJobPolicy(?session aosSession))))

procedure(aiOpampDesignSnapshot()
  prog((aodCv aodRows)
    foreach(aodCell list("OpAmp" "OpAmp_AC_top" "OpAmp_TRAN_top")
      foreach(aodView if(aodCell=="OpAmp" list("schematic" "symbol") list("schematic"))
        aodCv=nil
        unwindProtect(progn(
          aodCv=dbOpenCellViewByType("Two_Stage_Opamp" aodCell aodView "" "r")
          unless(and(aodCv !dbIsCellViewModified(aodCv)) error("source design missing/modified"))
          aodRows=cons(list(aodCell aodView aiGateSnapshot(aodCv)) aodRows))
          when(aodCv dbClose(aodCv)))))
    return(aodRows)))

procedure(aiOpampReply(aorEntry)
  aiCircuitObject(list(list("ok" aiCircuitBool(!aorEntry['error])) list("setup_ref" aorEntry['ref])
    list("target" aorEntry['target]) list("source" list("Two_Stage_Opamp" "OpAmp" "maestro_basic"))
    list("presentation" aorEntry['mode]) list("stage" aorEntry['stage])
    list("saved" aiCircuitBool(aorEntry['saved])) list("session" aorEntry['session])
    list("tests" list("AC" "TRAN")) list("model_section" if(aorEntry['matrix] "tt/ff/ss" "mc"))
    list("model_sections" if(aorEntry['matrix] list("tt" "ff" "ss") list("mc")))
    list("matrix" aorEntry['matrix])
    list("model_root" aorEntry['root]) list("reference_designs_read_only" t)
    list("circuit_created" aiCircuitBool(aorEntry['rebuild])) list("circuit_ref" aorEntry['rebuild]) list("spec_qualified" 'jsonFalse) list("error" aorEntry['error]))))

procedure(aiOpampSession(aosEntry)
  unless(and(aosEntry aosEntry['saved] !aosEntry['error] !aosEntry['released]
    aiCircuitTasks[aosEntry['task]]==aosEntry['mode]
    member(aosEntry['session] maeGetSessions())
    aiCircuitSessionMatches(aosEntry['session] aosEntry['target])
    !axlIsSessionReadOnly(aosEntry['session])) error("reference session lost/rebound/released"))
  when(aosEntry['mode]=="foreground"
    unless(and(member(aosEntry['window] hiGetWindowList('all))
      axlGetWindowSession(aosEntry['window])==aosEntry['session]) error("reference GUI window changed")))
  aosEntry['session])

procedure(aiOpampValidate(aovEntry)
  let((aovSession)
    aovSession=aiOpampSession(aovEntry)
    when(maeIsSetupModified(?session aovSession) error("setup has unsaved edits"))
    unless(equal(aiOpampSetupSnapshot(aovSession) aovEntry['setupSnapshot]) error("setup changed"))
    unless(equal(aiOpampDesignSnapshot() aovEntry['designSnapshot]) error("source design changed"))
    when(aovEntry['rebuild] aiRebuildSimulationValidate(aovEntry))
    when(aovEntry['matrix] aiMatrixValidate(aovEntry))
    t))

procedure(aiOpampInspect(aoiRef)
  prog((aoiEntry)
    aoiEntry=aiOpampEntries[aoiRef]
    unless(car(errset(aiOpampValidate(aoiEntry) t))
      return(aiCircuitError("reference_changed" "Setup/source/window differs; inspect CIW")))
    return(aiOpampReply(aoiEntry))))

procedure(aiOpampCreate(aocTask aocRequest aocLib aocCell aocRoot aocSource @optional aocRebuild)
  prog((aocMode aocRecord aocEntry aocResult aocSession aocOwned aocSourceSession aocSourceSnapshot)
    aocMode=aiCircuitTasks[aocTask]
    unless(aocMode return(aiCircuitError("task_not_found" "Confirmed task mode required")))
    aocResult=errset(aiRcReserve(strcat("opamp:" aocRequest) list(aocTask aocLib aocCell aocRoot aocSource aocRebuild)) nil)
    unless(aocResult return(aiCircuitError("request_conflict_or_limit" "Reference request conflict/limit")))
    aocRecord=car(aocResult)
    when(aocRecord['entry] return(aiOpampReply(aocRecord['entry])))
    aocEntry=makeTable("aiOpampEntry" nil) aocRecord['entry]=aocEntry
    aocEntry['ref]=strcat("opamp:" aocRequest) aiOpampEntries[aocEntry['ref]]=aocEntry
    aocEntry['task]=aocTask aocEntry['mode]=aocMode aocEntry['target]=list(aocLib aocCell "maestro")
    aocEntry['rebuild]=aocRebuild
    aocEntry['root]=aocRoot aocEntry['stage]="preflight"
    aocResult=errset(unwindProtect(progn(
      unless(and(ddGetObj(aocLib) ddIsObjWritable(ddGetObj(aocLib))) error("work library unavailable"))
      when(or(member(aocLib list("Two_Stage_Opamp" "gpdk045" "basic" "analogLib"))
        ddGetObj(aocLib aocCell)) error("new work cell required"))
      unless(and(simplifyFilename(ddGetObj("gpdk045")~>readPath)==simplifyFilename(strcat(aocRoot "/gpdk045"))
        simplifyFilename(ddGetObj("Two_Stage_Opamp")~>readPath)==simplifyFilename(aocSource)) error("source library path mismatch"))
      foreach(aocS maeGetSessions()
        when(aiCircuitSessionMatches(aocS list("Two_Stage_Opamp" "OpAmp" "maestro_basic")) error("reference already open")))
      when(aocRebuild aiRebuildValidate(aiRebuildEntries[aocRebuild]))
      aocEntry['designSnapshot]=aiOpampDesignSnapshot()
      aocSourceSession=maeOpenSetup("Two_Stage_Opamp" "OpAmp" "maestro_basic" ?application "Assembler" ?mode "r")
      unless(and(aocSourceSession axlIsSessionReadOnly(aocSourceSession)) error("source not read-only"))
      aiOpampReferenceContract(aocSourceSession)
      aocSourceSnapshot=aiOpampSetupSnapshot(aocSourceSession)
      unless(and(equal(maeGetSetup(?typeName "tests" ?enabled t ?session aocSourceSession) list("AC" "TRAN"))
        equal(maeGetEnabledAnalysis("AC" ?session aocSourceSession) list("dc" "ac"))
        equal(maeGetEnabledAnalysis("TRAN" ?session aocSourceSession) list("tran" "dc" "stb"))) error("unexpected reference analyses"))
      aocEntry['stage]="save_work_copy"
      unless(maeSaveSetup(?lib aocLib ?cell aocCell ?view "maestro" ?session aocSourceSession) error("Save As failed"))
      unless(equal(aiOpampSetupSnapshot(aocSourceSession) aocSourceSnapshot) error("source setup changed during Save As"))
      unless(maeCloseSession(?session aocSourceSession) error("source close failed"))
      aocSourceSession=nil
      aocSession=maeOpenSetup(aocLib aocCell "maestro" ?application "Assembler" ?mode "a")
      unless(aocSession error("copy unavailable"))
      aocOwned=t aocEntry['session]=aocSession aocEntry['stage]="repair_model_paths"
      when(aocRebuild
        aocEntry['stage]="bind_rebuilt_testbenches"
        aiRebuildBindTests(aocEntry))
      foreach(aocTest list("AC" "TRAN")
        unless(maeSetEnvOption(aocTest ?options list(list("modelFiles" list(list(strcat(aocRoot "/models/spectre/gpdk045.scs") "mc")))
          list("includePath" strcat(aocRoot "/models/spectre"))) ?session aocSession) error("model path repair failed")))
      unless(maeSaveSetup(?session aocSession) error("save work setup failed"))
      unless(maeCloseSession(?session aocSession) error("bootstrap close failed"))
      aocSession=nil aocOwned=nil aocEntry['session]=nil
      if(aocMode=="foreground" then
        aocEntry['stage]="foreground_handoff"
        aocEntry['window]=deOpenCellView(aocLib aocCell "maestro" "maestro" nil "a")
        aiCircuitPlaceRightHalf(aocEntry['window])
        aiCircuitRaiseWindow(aocEntry['window])
        aocSession=axlGetWindowSession(aocEntry['window])
      else aocSession=maeOpenSetup(aocLib aocCell "maestro" ?application "Assembler" ?mode "a") aocOwned=t)
      unless(aocSession error("final session unavailable"))
      aocEntry['session]=aocSession
      unless(maeSaveSetup(?session aocSession) error("final normalization save failed"))
      aocEntry['setupSnapshot]=aiOpampSetupSnapshot(aocSession)
      aocEntry['saved]=t aocEntry['stage]="reference_saved" t)
      progn(when(aocSourceSession maeCloseSession(?session aocSourceSession))
        when(and(aocOwned aocSession !aocEntry['saved]) maeCloseSession(?session aocSession ?forceClose t)))) t)
    unless(aocResult aocEntry['error]=strcat("Failed at " aocEntry['stage] "; partial work target retained; inspect CIW"))
    return(aiOpampReply(aocEntry))))
