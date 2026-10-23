; Shared bounded entry summaries. No CDF callbacks, recursive library scan or OA writes.
procedure(aiEntryDefaultRef()
  when(boundp('aiIpcState) aiIpcState['entryWindowRef]))
procedure(aiEntryLibraries(aelLimit aelBasics)
  let((aelLibs aelRows aelCount aelSkipped aelName aelHidden)
    aelHidden=list("avTech" "analogLib" "basic" "cdsDefTechLib" "US_8ths" "ahdlLib"
      "bmslib" "rfExamples" "rfLib" "rfTlineLib" "sample")
    aelLibs=ddGetLibList() aelCount=0 aelSkipped=0
    when(length(aelLibs)>4096 error("Library summary limit"))
    foreach(aelLib aelLibs
      aelName=aelLib~>name
      if(and(!aelBasics member(aelName aelHidden)) then aelSkipped=aelSkipped+1
      else
        when(aelCount<aelLimit aelRows=cons(aiPdkLibrary(aelLib) aelRows))
        aelCount=aelCount+1))
    aiPdkObj(list(list("items" aiPdkArray(reverse(aelRows))) list("total_visible" aelCount)
      list("hidden_base_library_count" aelSkipped) list("truncated" aiPdkBool(aelCount>aelLimit))
      list("classification" "technology binding is evidence; PDK/design role remains unknown")
      list("source" "current-process DD resolver, not Library Manager GUI selection")))))
procedure(aiEntryDesign(aedCv aedLimit)
  let((aedPorts aedInstances aedNets aedLayers aedRows aedCount aedInst aedMaster aedTail)
    when(aedCv
      aedTail=aedCv~>terminals aedCount=0
      while(and(aedTail aedCount<aedLimit)
        aedPorts=cons(list(car(aedTail)~>name car(aedTail)~>direction) aedPorts)
        aedTail=cdr(aedTail) aedCount=aedCount+1)
      aedTail=aedCv~>instances aedCount=0
      while(and(aedTail aedCount<aedLimit)
        aedInst=car(aedTail) aedMaster=aedInst~>master
        aedInstances=cons(list(aedInst~>name when(aedMaster list(aedMaster~>libName
          aedMaster~>cellName aedMaster~>viewName))) aedInstances)
        aedTail=cdr(aedTail) aedCount=aedCount+1)
      aedTail=aedCv~>nets aedCount=0
      while(and(aedTail aedCount<aedLimit)
        aedNets=cons(car(aedTail)~>name aedNets) aedTail=cdr(aedTail) aedCount=aedCount+1)
      aedTail=aedCv~>lpps aedCount=0
      while(and(aedTail aedCount<aedLimit)
        aedLayers=cons(list(car(aedTail)~>layerName car(aedTail)~>purpose) aedLayers)
        aedTail=cdr(aedTail) aedCount=aedCount+1)
      aiPdkObj(list(list("cellview" aiWindowCv(aedCv)) list("data_basis" "live open edit cellview")
        list("bbox" aiWindowBBox(aedCv~>bBox))
        list("technology_library" aiWindowTry('techGetTechLibName list(ddGetObj(aedCv~>libName))))
        list("port_count" length(aedCv~>terminals)) list("ports" aiPdkArray(reverse(aedPorts)))
        list("instance_count" length(aedCv~>instances)) list("instances" aiPdkArray(reverse(aedInstances)))
        list("net_count" length(aedCv~>nets)) list("nets" aiPdkArray(reverse(aedNets)))
        list("shape_count" length(aedCv~>shapes))
        list("modified_in_session" aiPdkBool(dbIsCellViewModified(aedCv)))
        list("content_state" if(or(aedCv~>instances aedCv~>shapes aedCv~>terminals aedCv~>nets aedCv~>mosaics)
          "nonempty" "empty"))
        list("layer_count" length(aedCv~>lpps)) list("layers" aiPdkArray(reverse(aedLayers)))
        list("truncated" aiPdkBool(or(length(aedCv~>terminals)>aedLimit
          length(aedCv~>instances)>aedLimit length(aedCv~>nets)>aedLimit length(aedCv~>lpps)>aedLimit)))
        list("not_checked" list("connectivity correctness" "electrical function" "DRC/LVS" "PDK readiness")))))))
procedure(aiEntryAde(aeaSession aeaTest aeaHistory aeaLimit)
  let((aeaSdb aeaNames aeaSelected aeaRows aeaArgs aeaCount aeaTb aeaCv aeaHistoryValid)
    aeaSdb=axlGetMainSetupDB(aeaSession) aeaNames=cadr(axlGetTests(aeaSdb))
    when(and(aeaTest !member(aeaTest aeaNames)) error("Captured test disappeared"))
    when(aeaHistory
      aeaHistoryValid=axlGetHistoryEntry(aeaSdb aeaHistory)
      unless(and(aeaHistoryValid aeaHistoryValid!=0) error("Captured history disappeared")))
    aeaSelected=if(aeaTest list(aeaTest) aeaNames) aeaCount=0
    foreach(aeaName aeaSelected
      when(aeaCount<aeaLimit
        aeaArgs=axlGetTestToolArgs(axlGetTest(aeaSdb aeaName))
        aeaTb=list(cadr(assoc("lib" aeaArgs)) cadr(assoc("cell" aeaArgs)) cadr(assoc("view" aeaArgs)))
        aeaCv=nil
        foreach(aeaOpen dbGetOpenCellViews()
          when(equal(list(aeaOpen~>libName aeaOpen~>cellName aeaOpen~>viewName) aeaTb) aeaCv=aeaOpen))
        aeaRows=cons(aiPdkObj(list(list("name" aeaName) list("testbench" aeaTb)
          list("simulator" cadr(assoc("sim" aeaArgs)))
          list("testbench_summary" aiEntryDesign(aeaCv min(aeaLimit 8)))
          list("testbench_summary_status" if(aeaCv "open_view_observed" "not_open_not_read")))) aeaRows))
      aeaCount=aeaCount+1)
    aiPdkObj(list(list("session" aeaSession) list("data_basis" "current_setup_not_history_checkpoint")
      list("selected_test" aeaTest) list("selected_history" aeaHistory)
      list("test_count" length(aeaNames)) list("tests" aiPdkArray(reverse(aeaRows)))
      list("truncated" aiPdkBool(aeaCount>aeaLimit))
      list("results_evaluated" 'pdkJsonFalse)
      list("not_checked" list("models/sections" "analysis settings" "run readiness" "numerical results"))))))
procedure(aiEntryContext(aecRef aecLimit aecBasics @optional aecTest aecHistory)
  prog((aecWindow aecSig aecSession aecCv aecSummary aecAde aecLibraries aecResult aecState)
    unless(and(fixp(aecLimit) aecLimit>=1 aecLimit<=50 member(aecBasics list(nil t)))
      return(aiPdkError("invalid_arguments" "Invalid entry summary options")))
    aecRef=or(aecRef aiEntryDefaultRef())
    unless(aecRef return(aiPdkError("source_not_captured" "Select an explicit window_ref; never infer source from focus")))
    aecState=aiWindowResolveState(aecRef) aecWindow=cadr(aecState)
    unless(aecWindow return(aiWindowStateError(aecState)))
    aecResult=errset(progn(
      aecSig=caddr(aecState) aecSession=caddr(aecSig)
      aecCv=or(cadr(aecSig) car(aecSig))
      when(aecSession aecAde=aiEntryAde(aecSession aecTest aecHistory min(aecLimit 8)))
      when(aecCv aecSummary=aiEntryDesign(aecCv aecLimit))
      when(aecWindow==aiWindowTry('hiGetCIWindow nil) aecLibraries=aiEntryLibraries(aecLimit aecBasics))
      aiPdkJson(aiPdkObj(list(list("ok" t) list("valid" t) list("schema" "cad.entry.context.v1")
        list("backend_revision" aiWindowRevision()) list("session_ref" aiWindowEpoch)
        list("pid" ipcGetPid()) list("version" getVersion(t)) list("cwd" getWorkingDir())
        list("captured_at" getCurrentTime()) list("source" aiWindowRow(aecWindow aecRef))
        list("design" aecSummary) list("ade" aecAde) list("libraries" aecLibraries)
        list("max_items" aecLimit) list("writes_design" 'pdkJsonFalse)
        list("target_authorization" "observation only; final operation checks required"))))) nil)
    if(aecResult then
      aecState=aiWindowResolveState(aecRef)
      unless(car(aecState)=='valid return(aiWindowStateError(aecState)))
      when(strlen(car(aecResult))>55000 return(aiPdkError("result_too_large" "Narrow the entry summary")))
      return(car(aecResult))
    else return(aiPdkError("read_failed" "Entry summary unavailable; no design changes performed")))))
