; Project-installed extension registry. No file loading or code supplied by MCP input.
; Registration is performed by a reviewed project init script in this session.
unless(boundp('aiCrExtensions) aiCrExtensions=makeTable("aiCrExtensions" nil))
procedure(aiCrRegisterExtension(aexRef aexRevision aexTarget aexLibraryPath aexValidate aexCreate)
  unless(and(stringp(aexRef) strlen(aexRef)>0 stringp(aexRevision) strlen(aexRevision)>0
    listp(aexTarget) length(aexTarget)==3 forall(aexPart aexTarget and(stringp(aexPart) strlen(aexPart)>0))
    stringp(aexLibraryPath) isDir(aexLibraryPath)
    symbolp(aexValidate) isCallable(aexValidate) symbolp(aexCreate) isCallable(aexCreate))
    error("invalid project extension registration"))
  when(aiCrExtensions[aexRef] error("extension reference already registered; use a new revision/ref"))
  aiCrExtensions[aexRef]=list(aexRevision aexTarget simplifyFilename(aexLibraryPath)
    aexValidate getd(aexValidate) aexCreate getd(aexCreate)) t)
procedure(aiCrListExtensions(aelLib aelCell aelView aelPath)
  prog((aelRows aelEntry aelValid aelLibrary aelBuiltin aelDiagnostic aelInit aelCdf)
    unless(boundp('aiCrExtensions)
      return(aiCircuitError("device_support_discovery_unavailable" "Device support registry unavailable")))
    aelLibrary=ddGetObj(aelLib)
    unless(and(aelLibrary equal(simplifyFilename(ddGetObjReadPath(aelLibrary)) simplifyFilename(aelPath)))
      return(aiCircuitError("device_support_library_changed" "Selected device library changed")))
    foreach(aelRef aiCrExtensions
      aelEntry=aiCrExtensions[aelRef]
      when(and(equal(cadr(aelEntry) list(aelLib aelCell aelView))
        equal(caddr(aelEntry) simplifyFilename(aelPath)))
        aelValid=and(eq(getd(nth(3 aelEntry)) nth(4 aelEntry))
          eq(getd(nth(5 aelEntry)) nth(6 aelEntry)))
        aelRows=cons(list(aelRef car(aelEntry) aiCircuitBool(aelValid)) aelRows)))
    when(and(!exists(aelRow aelRows eq(caddr(aelRow) t)) aelView=="symbol")
      aelBuiltin=errset(progn(
        unless(isCallable('aiPdkInitLibrary) error("PDK initializer is not loaded"))
        aelInit=aiPdkInitLibrary(aelLib)
        unless(car(aelInit) error("%s: %s" cadr(aelInit) caddr(aelInit)))
        unless(isCallable('aiCdfCallbackValidate) error("Built-in CDF executor is not loaded"))
        aelCdf=cdfGetCellCDF(ddGetObj(aelLib aelCell))
        unless(and(aelCdf or(!member(aelCdf~>formInitProc list(nil "" "nil"))
          !member(aelCdf~>doneProc list(nil "" "nil"))
          exists(aelParam aelCdf~>parameters
            !member(aelParam~>callback list(nil "" "nil")))))
          error("Selected cell has no CDF callbacks"))
        aiCdfCallbackEvidence(aelCdf nil) t) nil)
      if(aelBuiltin then
        aelRows=cons(list("builtin:cdf-callbacks:v1" aiCdfCallbackRevision() t) aelRows)
      else aelDiagnostic=aiCdfErrorText()))
    return(aiCircuitObject(list(list("ok" t) list("items" aelRows)
      list("builtin_diagnostic" aelDiagnostic)
      list("callbacks_executed" 'jsonFalse) list("request_validated" 'jsonFalse))))))
procedure(aiCrExtension(aexRow)
  prog((aexRef aexEntry)
    aexRef=nth(11 aexRow)
    unless(aexRef return(nil))
    when(aexRef=="builtin:cdf-callbacks:v1"
      unless(and(isCallable('aiCdfCallbackValidate) isCallable('aiCdfCallbackCreate))
        error("Built-in CDF callback executor is not loaded"))
      return(list(aiCdfCallbackRevision() cadr(aexRow) simplifyFilename(nth(2 aexRow))
        'aiCdfCallbackValidate getd('aiCdfCallbackValidate) 'aiCdfCallbackCreate getd('aiCdfCallbackCreate))))
    unless(boundp('aiCrExtensions) error("project extension registry not loaded"))
    aexEntry=aiCrExtensions[aexRef]
    unless(and(aexEntry equal(cadr(aexEntry) cadr(aexRow))
      caddr(aexEntry)==simplifyFilename(nth(2 aexRow))
      eq(getd(nth(3 aexEntry)) nth(4 aexEntry)) eq(getd(nth(5 aexEntry)) nth(6 aexEntry)))
      error("project extension missing, rebound or function changed"))
    return(aexEntry)))
procedure(aiCrExtensionValidate(aexMaster aexRow)
  prog((aexEntry)
    aexEntry=aiCrExtension(aexRow)
    unless(aexEntry return(nil))
    ; Validator contract: read-only; inspect descriptors and supported requests,
    ; never evaluate editable/display expressions or execute device callbacks.
    unless(apply(nth(3 aexEntry) list(aexMaster nth(5 aexRow))) error("project extension validation failed"))
    when(nth(11 aexRow)=="builtin:cdf-callbacks:v1"
      aexEntry=append(aexEntry list(aiCdfCallbackEvidence(
        cdfGetCellCDF(ddGetObj(aexMaster~>libName aexMaster~>cellName)) nth(5 aexRow)))))
    return(aexEntry)))
procedure(aiCrCreateInstance(aexCv aexMaster aexRow @optional aexPrepared)
  prog((aexEntry aexInst aexCdf aexType)
    aexEntry=aiCrExtensionValidate(aexMaster aexRow)
    if(aexEntry then
      if(and(aexPrepared nth(11 aexRow)=="builtin:cdf-callbacks:v1") then
        aexInst=aiCdfCallbackApply(nil aexMaster nth(5 aexRow) aexCv car(aexRow)
          nth(3 aexRow) nth(4 aexRow) nil nil aexPrepared)
      else
      aexInst=apply(nth(5 aexEntry) list(aexCv aexMaster car(aexRow) nth(3 aexRow) nth(4 aexRow) nth(5 aexRow)))
      )
    else
      aexInst=schCreateInst(aexCv aexMaster car(aexRow) nth(3 aexRow) nth(4 aexRow))
      unless(aexInst error("schCreateInst failed"))
      aexCdf=cdfGetInstCDF(aexInst)
      foreach(aexValue nth(5 aexRow)
        aexType=aiCrParam(aexCdf aexValue)
        unless(dbReplaceProp(aexInst car(aexValue) aexType caddr(aexValue)) error("property write failed"))))
    unless(and(aexInst aexInst~>cellView==aexCv aexInst~>name==car(aexRow))
      error("project creation returned wrong instance"))
    when(aexEntry
      aexCdf=cdfGetInstCDF(aexInst)
      foreach(aexValue nth(5 aexRow)
        unless(if(nth(11 aexRow)=="builtin:cdf-callbacks:v1"
          aiCdfCallbackEqual(cdfFindParamByName(aexCdf car(aexValue))~>value caddr(aexValue))
          equal(cdfFindParamByName(aexCdf car(aexValue))~>value caddr(aexValue)))
          error("project extension changed requested effective parameter: %s" car(aexValue)))))
    return(aexInst)))
