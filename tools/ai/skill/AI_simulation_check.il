; Generic nominal recipe validation/readback. No reference-circuit or PDK constants.
procedure(aiSimulationRevision() "20261021.ai.simulation.v8")
procedure(aiSimFailText()
  let((asmText)
    asmText=sprintf(nil "%L" errset.errset)
    if(strlen(asmText)>2048 substring(asmText 1 2048) asmText)))
procedure(aiSimSorted(asmValues) sort(copy(asmValues) 'alphalessp))
; ADE echoes numeric option strings in its own form ("1.0"); compare numbers.
procedure(aiSimOptionEqual(asqActual asqExpected)
  if(equal(asqActual asqExpected) t
    if(and(stringp(asqActual) stringp(asqExpected)
      pcreMatchp("^[ \t]*[-+]?[0-9]+([.][0-9]*)?([eE][-+]?[0-9]+)?[ \t]*$" asqActual)
      pcreMatchp("^[ \t]*[-+]?[0-9]+([.][0-9]*)?([eE][-+]?[0-9]+)?[ \t]*$" asqExpected))
      equal(atof(asqActual) atof(asqExpected)) nil)))
; Optional tail slots: config_ref (string) and simulator-option pairs (list).
procedure(aiSimConfigRef(asxTest)
  let((asxTail)
    asxTail=nth(9 asxTest)
    if(stringp(asxTail) asxTail nil)))
procedure(aiSimSimOptions(asxTest)
  let((asxTail asxNext)
    asxTail=nth(9 asxTest)
    if(stringp(asxTail)
      (asxNext=nth(10 asxTest) if(listp(asxNext) asxNext nil))
      if(listp(asxTail) asxTail nil))))
procedure(aiSimAssertEqual(asmActual asmExpected asmLabel)
  unless(equal(asmActual asmExpected)
    error("recipe readback differs: %s; expected %L; actual %L" asmLabel asmExpected asmActual)) t)
procedure(aiSimSourceSnapshot(asdCv)
  ; ADE can reformat effective instance CDF values as contexts load. Compare raw OA
  ; properties and direct connectivity here, not transient formatted CDF objects.
  list(
    sort(foreach(mapcar asdInst asdCv~>instances sprintf(nil "%L"
      list(asdInst~>name asdInst~>libName asdInst~>cellName asdInst~>viewName asdInst~>transform
        simplifyFilename(ddGetObjReadPath(ddGetObj(asdInst~>libName)))
        sort(foreach(mapcar asdProp asdInst~>prop sprintf(nil "%L"
          list(asdProp~>name asdProp~>valueType asdProp~>value))) 'alphalessp)
        sort(foreach(mapcar asdTerm asdInst~>instTerms sprintf(nil "%L"
          list(asdTerm~>name asdTerm~>net~>name))) 'alphalessp)))) 'alphalessp)
    sort(foreach(mapcar asdShape asdCv~>shapes sprintf(nil "%L"
      list(asdShape~>objType asdShape~>lpp asdShape~>bBox asdShape~>points asdShape~>theLabel))) 'alphalessp)
    sort(foreach(mapcar asdTerm asdCv~>terminals sprintf(nil "%L"
      list(asdTerm~>name asdTerm~>direction asdTerm~>net~>name))) 'alphalessp)
    ; ADE adds these two environment caches to a source CV during test binding.
    ; Their authoritative settings are checked through maeGetEnvOption/SimOption.
    sort(foreach(mapcar asdProp setof(asdP asdCv~>prop !member(asdP~>name list("spectre" "viewNameList"))) sprintf(nil "%L"
      list(asdProp~>name asdProp~>valueType asdProp~>value))) 'alphalessp)))
procedure(aiSimFileCheck(asmEntry asmModels)
  aiSimAssertEqual(asmModels asmEntry['models] "explicit model content identities")
  foreach(asmModel asmModels
      unless(and(isFile(car(asmModel)) isReadable(car(asmModel))) error("model file missing/unreadable"))) t)

; Resolve only declared output paths, before maeOpenSetup. Config paths use the
; already verified HDB traversal; direct designs follow the recipe switch list.
; Every opened view is read-only and closed, including all failure paths.
procedure(aiSimSignalResolve(asgTest asgOutput asgSource)
  prog((asgPath asgParts asgType asgName asgParents asgCv asgTarget asgInst asgTerm
    asgBound asgPrefix asgRows asgProof asgLabel asgView asgResult asgFailure)
    asgPath=caddr(asgOutput) asgType=or(nth(5 asgOutput) "net")
    asgLabel=sprintf(nil "test=%s output=%s path=%s signal_type=%s"
      car(asgTest) car(asgOutput) asgPath asgType)
    asgParts=parseString(asgPath "/")
    unless(and(asgParts equal(asgPath strcat("/" buildString(asgParts "/")))
      member(asgType list("net" "terminal" "terminalV")) length(asgParts)<=10)
      error("Invalid signal path/type: %s" asgLabel))
    asgName=car(last(asgParts)) asgParents=reverse(cdr(reverse(asgParts)))
    when(asgType!="net"
      unless(asgParents error("Terminal output requires an instance and terminal: %s" asgLabel)))
    asgTarget=cadr(asgTest)
    when(aiSimConfigRef(asgTest)
      asgRows=nth(4 asgSource)
      asgTarget=cadr(car(setof(asgRow asgRows and(null(car(asgRow)) cadr(asgRow)!="binding")))))
    unwindProtect(progn(asgResult=errset(progn(
      asgCv=apply('dbOpenCellViewByType append(asgTarget list("" "r")))
      foreach(asgPart asgParents
        unless(and(asgCv asgCv~>cellViewType=="schematic" !dbIsCellViewModified(asgCv)
          aiSimSourceReadyP(asgCv))
          error("Signal hierarchy requires saved schematics without error markers: %s" asgLabel))
        asgInst=dbFindAnyInstByName(asgCv asgPart)
        unless(and(asgInst !asgInst~>pin)
          error("Signal instance is missing: %s; instance=%s" asgLabel asgPart))
        asgPrefix=append(asgPrefix list(asgPart))
        if(and(asgType!="net" equal(asgPrefix asgParents)) then
          asgTerm=dbFindTermByName(asgInst~>master asgName)
          unless(and(asgTerm dbGetNameNumBit(asgName)==1)
            error("Scalar terminal is missing: %s" asgLabel))
          asgProof=cons(list(asgPrefix list(asgInst~>libName asgInst~>cellName asgInst~>viewName)
            asgTerm~>name asgTerm~>direction) asgProof)
        else
          when(length(asgPrefix)>8 error("Signal hierarchy depth exceeds 8: %s" asgLabel))
          if(aiSimConfigRef(asgTest) then
            asgBound=car(setof(asgRow asgRows and(equal(car(asgRow) asgPrefix) cadr(asgRow)=="binding")))
            unless(asgBound error("Config signal binding is missing: %s" asgLabel))
            when(nth(3 asgBound) error("Signal path crosses a Config stop view: %s" asgLabel))
            asgTarget=caddr(asgBound)
          else
            asgView=nil
            foreach(asgCandidate nth(7 asgTest)
              when(and(!asgView ddGetObj(asgInst~>libName asgInst~>cellName asgCandidate))
                asgView=asgCandidate))
            unless(asgView error("Signal hierarchy has no switch view: %s" asgLabel))
            when(member(asgView nth(8 asgTest)) error("Signal path crosses a stop view: %s" asgLabel))
            asgTarget=list(asgInst~>libName asgInst~>cellName asgView))
          dbClose(asgCv) asgCv=nil
          asgCv=apply('dbOpenCellViewByType append(asgTarget list("" "r")))
          unless(and(asgCv asgCv~>cellViewType=="schematic" !dbIsCellViewModified(asgCv)
            !asgCv~>superMaster !asgCv~>isParamCell
            aiSimSourceReadyP(asgCv))
            error("Signal hierarchy requires a saved static schematic without error markers: %s" asgLabel))
          asgProof=cons(list(asgPrefix asgTarget
            simplifyFilename(ddGetObjReadPath(ddGetObj(car(asgTarget)))) aiSimSourceSnapshot(asgCv)) asgProof)))
      when(asgType=="net"
        unless(and(asgCv dbGetNameNumBit(asgName)==1 dbFindSigByName(asgCv asgName))
          error("Scalar net is missing; instance pins require terminal or terminalV: %s" asgLabel)))
      t) t)
      unless(asgResult asgFailure=aiSimFailText())) when(asgCv dbClose(asgCv)))
    unless(asgResult error("%s" asgFailure))
    return(list(car(asgOutput) asgPath asgType reverse(asgProof)))))
procedure(aiSimSignalChecks(ascTest ascSource)
  foreach(mapcar ascOutput setof(ascOut nth(6 ascTest) cadr(ascOut)=="signal")
    aiSimSignalResolve(ascTest ascOutput ascSource)))

; ADE 按 switch_views 顺序解析每个实例的 master。内置 analogLib/basic 器件没有
; schematic 视图，视图表若解析不了某个 master，ADE 直到网表阶段才以 OSSHNL-116
; 失败；这里在预检阶段按同一份列表走一遍有限层次，提前给出精确的定位。
procedure(aiSimViewNames(asvLib asvCell)
  let((asvObj)
    asvObj=ddGetObj(asvLib asvCell)
    if(asvObj sort(foreach(mapcar asvView asvObj~>views asvView~>name) 'alphalessp) nil)))
procedure(aiSimViewFirst(asvLib asvCell asvViews)
  let((asvChosen)
    foreach(asvView asvViews
      when(!asvChosen when(ddGetObj(asvLib asvCell asvView) asvChosen=asvView)))
    asvChosen))
; 每个实例给出一个带标签的决定：('follow lib cell view) / ('stop) / ('problem 描述)。
procedure(aiSimViewStep(asvInst asvSwitches asvStops)
  let((asvChosen)
    asvChosen=aiSimViewFirst(asvInst~>libName asvInst~>cellName asvSwitches)
    cond(
      (!asvChosen list('problem sprintf(nil "instance %s master %s/%s (views %L)"
        asvInst~>name asvInst~>libName asvInst~>cellName
        aiSimViewNames(asvInst~>libName asvInst~>cellName))))
      (member(asvChosen asvStops) list('stop))
      (t list('follow asvInst~>libName asvInst~>cellName asvChosen)))))
procedure(aiSimViewCoverage(asvTest)
  let((asvSwitches asvStops asvQueue asvSeen asvProblems asvItem asvCv asvStep)
    asvSwitches=nth(7 asvTest) asvStops=nth(8 asvTest)
    asvQueue=list(cadr(asvTest)) asvProblems=nil
    while(asvQueue
      asvItem=car(asvQueue) asvQueue=cdr(asvQueue)
      unless(member(asvItem asvSeen)
        asvSeen=cons(asvItem asvSeen)
        when(length(asvSeen)>256 error("view coverage exceeds 256 cell views"))
        asvCv=apply('dbOpenCellViewByType append(asvItem list("" "r")))
        unless(asvCv error(sprintf(nil "master %s/%s unreadable (views %L)"
          car(asvItem) cadr(asvItem) aiSimViewNames(car(asvItem) cadr(asvItem)))))
        when(asvCv~>cellViewType=="schematic"
          foreach(asvInst setof(asvI asvCv~>instances !asvI~>pin)
            asvStep=aiSimViewStep(asvInst asvSwitches asvStops)
            if(eq(car(asvStep) 'follow)
              asvQueue=append(asvQueue list(cdr(asvStep)))
              when(and(eq(car(asvStep) 'problem) length(asvProblems)<5)
                asvProblems=append(asvProblems list(cadr(asvStep)))))))
        dbClose(asvCv)))
    when(asvProblems
      error("%s" sprintf(nil
        "view_list_incomplete: %s%s; switch_views=%L. Builtin analogLib/basic primitives have no schematic view, so switch_views must resolve every master of the design (add \"spectre\" for the builtin default list). Preview the corrected recipe and create again."
        car(asvProblems)
        if(cdr(asvProblems) sprintf(nil " (+%d more)" length(cdr(asvProblems))) "")
        asvSwitches)))
    t))
; A saved schematic whose extraction is current may still contain warning
; markers (for example, an intentionally unconnected diagnostic pin). ADE can
; simulate that state; only stale extraction or error markers are fatal.
procedure(aiSimSourceReadyP(asrCv)
  let((asrStatus)
    asrStatus=schExtractStatus(asrCv)
    or(member(asrStatus list("clean" 'clean))
      and(member(asrStatus list("dirty" 'dirty))
        isCallable('geHasMarker) !geHasMarker(asrCv nil "error")))))
procedure(aiSimSources(asmEntry)
  prog((asmCv asmRows asmTarget asmLib asmSource asmResult asmFailure)
    unwindProtect(progn(asmResult=errset(progn(
      foreach(asmTest nth(3 asmEntry['payload])
        if(aiSimConfigRef(asmTest) then
          asmSource=aiSimConfigSource(asmTest)
          asmRows=cons(append(asmSource list(aiSimSignalChecks(asmTest asmSource))) asmRows)
        else
        asmTarget=cadr(asmTest) asmLib=ddGetObj(car(asmTarget))
        unless(and(asmLib apply('ddGetObj asmTarget)
          aiCircuitViewType(car(asmTarget) cadr(asmTarget) caddr(asmTarget))=="schematic")
          error("saved schematic source required; config/hierarchy qualification is separate"))
        asmCv=dbOpenCellViewByType(car(asmTarget) cadr(asmTarget) caddr(asmTarget) "" "r")
        unless(and(asmCv !dbIsCellViewModified(asmCv)) error("source schematic modified/unavailable"))
        unless(aiSimSourceReadyP(asmCv)
          error("source schematic requires Check & Save without error markers before simulation setup"))
        aiSimViewCoverage(asmTest)
        asmRows=cons(list(asmTarget simplifyFilename(ddGetObjReadPath(asmLib)) aiSimSourceSnapshot(asmCv)
          aiSimSignalChecks(asmTest nil)) asmRows)
        dbClose(asmCv) asmCv=nil)) t) t)
      unless(asmResult asmFailure=aiSimFailText())) when(asmCv dbClose(asmCv)))
    unless(asmResult error("%s" asmFailure))
    when(strlen(sprintf(nil "%L" asmRows))>524288 error("source snapshot exceeds budget"))
    return(reverse(asmRows))))
procedure(aiSimWork(asmEntry)
  prog((asmTarget asmLib asmPath)
    asmTarget=car(asmEntry['payload]) asmLib=ddGetObj(car(asmTarget))
    unless(and(asmLib ddIsObjWritable(asmLib)) error("existing writable setup library required"))
    asmPath=simplifyFilename(ddGetObjReadPath(asmLib))
    unless(aiCircuitTasks[asmEntry['task]]==asmEntry['mode] error("confirmed task changed"))
    when(asmEntry['workPath] aiSimAssertEqual(asmPath asmEntry['workPath] "work library"))
    when(and(asmEntry['mode]=="foreground" !aiCircuitGuiP()) error("foreground unavailable"))
    return(asmPath)))
procedure(aiSimSession(asmEntry)
  unless(and(asmEntry !asmEntry['released] member(asmEntry['session] maeGetSessions())
    aiCircuitSessionMatches(asmEntry['session] car(asmEntry['payload]))
    !axlIsSessionReadOnly(asmEntry['session])) error("setup session closed/rebound/read-only/released"))
  when(asmEntry['mode]=="foreground"
    unless(and(member(asmEntry['window] hiGetWindowList('all))
      axlGetWindowSession(asmEntry['window])==asmEntry['session]) error("setup window closed/rebound")))
  asmEntry['session])
procedure(aiSimVars(asmSession asmDb)
  foreach(mapcar asmName aiSimSorted(cadr(axlGetVars(asmDb)))
    list(asmName axlGetVarValue(axlGetVar(asmDb asmName)))))
procedure(aiSimSnapshot(asmSession)
  prog((asmDb asmTests asmRows asmTs asmTd asmOuts)
    asmDb=axlGetMainSetupDB(asmSession) asmTests=maeGetSetup(?typeName "tests" ?session asmSession)
    when(length(asmTests)>8 error("unexpected number of tests"))
    foreach(asmTest asmTests
      asmTs=maeGetTestSession(asmTest ?session asmSession) asmTd=axlGetTest(asmDb asmTest)
      asmOuts=maeGetTestOutputs(asmTest ?session asmSession)
      when(length(asmOuts)>64 error("unexpected output count"))
      asmRows=cons(list(asmTest
        list(asiGetDesignLibName(asmTs) asiGetDesignCellName(asmTs) asiGetDesignViewName(asmTs) asiGetSimName(asmTs))
        copy(maeGetEnvOption(asmTest ?session asmSession)) aiSimVars(asmSession asmTd)
        foreach(mapcar asmGlobal cadr(axlGetVars(asmDb))
          list(asmGlobal axlGetEnabledGlobalVarPerTest(asmDb asmGlobal asmTest)))
        copy(maeGetSimOption(asmTest ?session asmSession))
        maeGetEnabledAnalysis(asmTest ?session asmSession)
        foreach(mapcar asmAna list("dc" "ac" "tran") list(asmAna copy(maeGetAnalysis(asmTest asmAna ?session asmSession))))
        foreach(mapcar asmOut asmOuts list(asmOut->name copy(asmOut->expression) asmOut->signal asmOut->plot asmOut->save asmOut->evalType asmOut->type))) asmRows))
    asmRows=list(reverse(asmRows) maeGetSetup(?typeName "tests" ?enabled t ?session asmSession)
      aiSimVars(asmSession asmDb) axlGetAllSweepsEnabled(asmDb) axlGetAllVarsDisabled(asmDb)
      foreach(mapcar asmGlobal cadr(axlGetVars(asmDb)) list(asmGlobal axlGetEnabled(axlGetVar(asmDb asmGlobal))))
      foreach(mapcar asmCorner cadr(axlGetCorners(asmDb))
        list(asmCorner aiSimVars(asmSession axlGetCorner(asmDb asmCorner))))
      maeGetSetup(?typeName "corners" ?enabled t ?session asmSession)
      copy(maeGetJobPolicy(?session asmSession)) aiSimDimensionSnapshot(asmSession))
    when(strlen(sprintf(nil "%L" asmRows))>262144 error("setup snapshot exceeds budget"))
    return(asmRows)))
procedure(aiSimReadback(asmEntry)
  prog((asmSession asmDb asmPayload asmNames asmTs asmOptions asmOuts asmOutput asmWant asmPort asmParsed)
    asmSession=asmEntry['session] asmPayload=asmEntry['payload] asmDb=axlGetMainSetupDB(asmSession)
    asmNames=foreach(mapcar asmTest nth(3 asmPayload) car(asmTest))
    aiSimAssertEqual(aiSimSorted(maeGetSetup(?typeName "tests" ?session asmSession)) aiSimSorted(asmNames) "test names")
    aiSimAssertEqual(aiSimSorted(maeGetSetup(?typeName "tests" ?enabled t ?session asmSession)) aiSimSorted(asmNames) "enabled tests")
    aiSimReadbackDimensions(asmEntry)
    unless(axlGetAllSweepsEnabled(asmDb) error("point variables disabled"))
    when(and(cadr(asmPayload) axlGetAllVarsDisabled(asmDb)) error("global variables disabled"))
    foreach(asmVar cadr(asmPayload)
      unless(axlGetEnabled(axlGetVar(asmDb car(asmVar))) error("global variable disabled")))
    foreach(asmVar aiSimGlobalValues(asmPayload) aiSimAssertEqual(maeGetVar(car(asmVar) ?session asmSession) cadr(asmVar) car(asmVar)))
    foreach(asmTest nth(3 asmPayload)
      asmTs=maeGetTestSession(car(asmTest) ?session asmSession)
      aiSimAssertEqual(list(asiGetDesignLibName(asmTs) asiGetDesignCellName(asmTs) asiGetDesignViewName(asmTs)) cadr(asmTest) "design")
      aiSimAssertEqual(asiGetSimName(asmTs) caddr(asmTest) "simulator")
      foreach(asmVar cadr(asmPayload)
        aiSimAssertEqual(when(axlGetEnabledGlobalVarPerTest(asmDb car(asmVar) car(asmTest)) t)
          if(assoc(car(asmVar) nth(3 asmTest)) nil t) "global variable test binding"))
      aiSimAssertEqual(maeGetSimOption(car(asmTest) ?option "temp" ?session asmSession) nth(2 asmPayload) "temperature")
      foreach(asmOption aiSimSimOptions(asmTest)
        let((asmOptionActual asmOptionExpected)
          asmOptionActual=maeGetSimOption(car(asmTest) ?option car(asmOption) ?session asmSession)
          asmOptionExpected=cadr(asmOption)
          unless(aiSimOptionEqual(asmOptionActual asmOptionExpected)
            error("recipe readback differs: %s; expected %L; actual %L"
              car(asmOption) asmOptionExpected asmOptionActual))))
      foreach(asmVar nth(3 asmTest) aiSimAssertEqual(maeGetVar(car(asmVar) ?typeName "test" ?typeValue car(asmTest) ?session asmSession) cadr(asmVar) car(asmVar)))
      asmOptions=maeGetEnvOption(car(asmTest) ?session asmSession)
      foreach(asmPair append(
        when(nth(4 asmTest) list(list("modelFiles" nth(4 asmTest))))
        unless(aiSimConfigRef(asmTest) list(list("switchViewList" nth(7 asmTest)) list("stopViewList" nth(8 asmTest)))))
        aiSimAssertEqual(cadr(assoc(car(asmPair) asmOptions)) cadr(asmPair) car(asmPair)))
      aiSimAssertEqual(aiSimSorted(maeGetEnabledAnalysis(car(asmTest) ?session asmSession)) aiSimSorted(foreach(mapcar asmAna nth(5 asmTest) car(asmAna))) "enabled analyses")
      foreach(asmAna nth(5 asmTest)
        foreach(asmOpt cadr(asmAna)
          aiSimAssertEqual(maeGetAnalysis(car(asmTest) car(asmAna) ?option car(asmOpt) ?session asmSession) cadr(asmOpt) car(asmOpt))))
      asmOuts=maeGetTestOutputs(car(asmTest) ?session asmSession)
      aiSimAssertEqual(aiSimSorted(foreach(mapcar asmOut asmOuts asmOut->name)) aiSimSorted(foreach(mapcar asmOut nth(6 asmTest) car(asmOut))) "outputs")
      foreach(asmWant nth(6 asmTest)
        asmOutput=car(setof(asmOut asmOuts asmOut->name==car(asmWant)))
        aiSimAssertEqual(when(asmOutput->plot t) nth(3 asmWant)
          sprintf(nil "output plot test=%s output=%s signal_type=%s" car(asmTest) car(asmWant) or(nth(5 asmWant) "net")))
        aiSimAssertEqual(when(asmOutput->save t) nth(4 asmWant)
          sprintf(nil "output save test=%s output=%s" car(asmTest) car(asmWant)))
        if(cadr(asmWant)=="signal" then
          aiSimAssertEqual(asmOutput->signal caddr(asmWant) "signal")
          aiSimAssertEqual(if(symbolp(asmOutput->type) symbolToString(asmOutput->type) asmOutput->type)
            or(nth(5 asmWant) "net")
            sprintf(nil "signal type test=%s output=%s" car(asmTest) car(asmWant)))
        else
          unwindProtect(progn(asmPort=instring(caddr(asmWant)) asmParsed=lineread(asmPort)) close(asmPort))
          unless(length(asmParsed)==1 error("one calculator expression required"))
          aiSimAssertEqual(asmOutput->expression car(asmParsed) "expression")
          unless(member(asmOutput->evalType list('point "point")) error("expression evaluation type changed")))))
    return(t)))
procedure(aiSimValidate(asmEntry)
  aiSimWork(asmEntry) aiSimSession(asmEntry)
  unless(and(asmEntry['saved] !asmEntry['error]) error("setup not saved/verified"))
  when(maeIsSetupModified(?session asmEntry['session]) error("setup has unsaved edits"))
  aiSimAssertEqual(aiSimSources(asmEntry) asmEntry['sources] "source schematics")
  aiSimReadback(asmEntry)
  aiSimAssertEqual(aiSimSnapshot(asmEntry['session]) asmEntry['snapshot] "saved setup") t)
