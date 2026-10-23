; Installed CDF procedures only. Request values are data, never SKILL expressions.
procedure(aiCdfCallbackRevision() "20261022.ai.cdf.callbacks.v5")
procedure(aiCdfCallbackLiteral(acbArg)
  or(null(acbArg) eq(acbArg t) numberp(acbArg) stringp(acbArg)
    and(listp(acbArg) length(acbArg)==2 car(acbArg)=='quote symbolp(cadr(acbArg)))))
procedure(aiCdfCallbackParse(acbText)
  prog((acbForms acbForm)
    when(or(null(acbText) acbText=="") return(nil))
    unless(and(stringp(acbText) strlen(acbText)<=4096) error("Unsupported CDF procedure descriptor"))
    acbForms=linereadstring(acbText)
    unless(and(listp(acbForms) length(acbForms)==1) error("CDF callback must be one procedure call"))
    acbForm=car(acbForms)
    ; Some PDKs publish the literal string "nil" for a no-op callback.
    ; Check the form count first: "nil otherCall()" is never a no-op.
    when(null(acbForm) return(nil))
    unless(and(listp(acbForm) symbolp(car(acbForm))
      !member(car(acbForm) list('eval 'evalstring 'load 'apply 'funcall 'system 'exit 'ipcBeginProcess))
      length(acbForm)<=17 forall(acbArg cdr(acbForm) aiCdfCallbackLiteral(acbArg)))
      error("Unsupported CDF callback: expected one procedure call with literal arguments"))
    unless(isCallable(car(acbForm))
      error("CDF callback procedure is not loaded: %s; initialize the selected PDK library first"
        symbolToString(car(acbForm))))
    return(list(car(acbForm) foreach(mapcar acbArg cdr(acbForm)
      if(listp(acbArg) cadr(acbArg) acbArg)) getd(car(acbForm))))))
procedure(aiCdfCallbackHook(acbText)
  prog((acbSymbol)
    when(or(null(acbText) acbText=="") return(nil))
    unless(and(stringp(acbText) pcreMatchp("^[A-Za-z_][A-Za-z0-9_]*$" acbText))
      error("CDF hook must name an installed procedure"))
    acbSymbol=concat(acbText)
    unless(isCallable(acbSymbol) error("CDF hook procedure is not loaded: %s" acbText))
    return(list(acbSymbol getd(acbSymbol)))))
procedure(aiCdfCallbackInvoke(acbCall)
  when(acbCall
    unless(eq(getd(car(acbCall)) caddr(acbCall)) error("CDF callback procedure changed"))
    apply(car(acbCall) cadr(acbCall))))
procedure(aiCdfCallbackParam(acbCdf acbRow)
  prog((acbParam acbType acbValue)
    acbParam=cdfFindParamByName(acbCdf car(acbRow)) acbValue=caddr(acbRow)
    unless(acbParam error("CDF parameter missing: %s" car(acbRow)))
    acbType=case(acbParam~>paramType
      (("string" "cyclic" "radio") "string") ("int" "integer")
      ("float" "number") ("boolean" "boolean") (t nil))
    ; An integral request is a valid value for a float-typed CDF parameter (e.g. fingers).
    unless(and(acbType or(acbType==cadr(acbRow)
        and(acbType=="number" cadr(acbRow)=="integer" fixp(acbValue))))
      error("CDF parameter %s: live type %L does not accept requested %s"
        car(acbRow) acbParam~>paramType cadr(acbRow)))
    unless(case(acbType ("string" stringp(acbValue)) ("integer" fixp(acbValue))
      ("number" numberp(acbValue)) ("boolean" member(acbValue list(nil t))))
      error("CDF value type mismatch"))
    when(acbParam~>choices
      unless(member(acbValue acbParam~>choices) error("CDF value is outside published choices: %s" car(acbRow))))
    when(and(stringp(acbValue) !acbParam~>choices)
      unless(and(strlen(acbValue)<=1024
        pcreMatchp("^[A-Za-z0-9_+.-]+$" acbValue))
        error("CDF callback writes require literal scalar values, not expressions")))
    aiCdfCallbackParse(acbParam~>callback)
    return(acbParam)))
procedure(aiCdfCallbackEvidence(acbCdf acbRows)
  prog((acbEvidence acbParam acbNames)
    unless(and(acbCdf listp(acbRows) length(acbRows)<=16) error("CDF callback request limit"))
    acbEvidence=list(aiCdfCallbackHook(acbCdf~>formInitProc) aiCdfCallbackHook(acbCdf~>doneProc))
    foreach(acbRow acbRows
      unless(and(listp(acbRow) length(acbRow)==3 stringp(car(acbRow))
        !member(car(acbRow) acbNames)) error("Invalid or duplicate CDF parameter row"))
      acbNames=cons(car(acbRow) acbNames)
      acbParam=aiCdfCallbackParam(acbCdf acbRow)
      acbEvidence=append(acbEvidence list(list(car(acbRow) aiCdfCallbackParse(acbParam~>callback)))))
    return(acbEvidence)))
procedure(aiCdfCallbackValidate(acbMaster acbRows)
  let((acbInit)
    unless(and(acbMaster acbMaster~>cellViewType=="schematicSymbol"
      !dbIsCellViewModified(acbMaster) !acbMaster~>isParamCell !acbMaster~>superMaster !acbMaster~>instances)
      error("CDF callbacks currently require a saved static flat symbol"))
    acbInit=aiPdkInitLibrary(acbMaster~>libName)
    unless(car(acbInit) error("%s: %s" cadr(acbInit) caddr(acbInit)))
    aiCdfCallbackEvidence(cdfGetCellCDF(ddGetObj(acbMaster~>libName acbMaster~>cellName)) acbRows) t))
procedure(aiCdfCallbackEqual(acbActual acbWanted)
  prog((acbA acbW)
    when(equal(acbActual acbWanted) return(t))
    when(and(stringp(acbActual) stringp(acbWanted)
      pcreMatchp("^[+-]?[0-9]+[.]?[0-9]*([eE][+-]?[0-9]+|[afpnumkKMG])?$" acbActual)
      pcreMatchp("^[+-]?[0-9]+[.]?[0-9]*([eE][+-]?[0-9]+|[afpnumkKMG])?$" acbWanted))
      acbA=cdfParseFloatString(acbActual) acbW=cdfParseFloatString(acbWanted)
      return(and(numberp(acbA) numberp(acbW) abs(acbA-acbW)<=max(abs(acbW)*1e-9 1e-24))))
    return(nil)))
procedure(aiCdfCallbackApply(acbInst acbMaster acbRows @optional acbCv acbName acbXY acbOrient acbOutcome acbEvaluateOnly acbPrepared)
  prog((cdfgData cdfgForm acbSaved acbBefore acbInstCdf acbEvidence acbParam acbCall acbResult)
    aiCdfCallbackValidate(acbMaster acbRows)
    unless(acbRows error("CDF update requires at least one parameter"))
    cdfgData=cdfGetCellCDF(ddGetObj(acbMaster~>libName acbMaster~>cellName))
    ; No GUI widget policy is evaluated. Installed init may supply commit context.
    cdfgForm=nil
    acbSaved=foreach(mapcar acbP cdfgData~>parameters list(acbP acbP~>value))
    foreach(acbPair acbSaved
      unless(or(null(cadr(acbPair)) eq(cadr(acbPair) t) numberp(cadr(acbPair)) stringp(cadr(acbPair)))
        error("CDF restoration requires scalar parameter values")))
    acbBefore=aiCdfSourceState(cdfgData)
    acbEvidence=aiCdfCallbackEvidence(cdfgData acbRows)
    unwindProtect(progn(
      acbInstCdf=when(acbInst cdfGetInstCDF(acbInst))
      foreach(acbP cdfgData~>parameters
        acbP~>value=if(acbInst cdfFindParamByName(acbInstCdf acbP~>name)~>value acbP~>defValue))
      when(isCallable('aiCdfProbeCheckpoint) aiCdfProbeCheckpoint(cdfgData "builtin:before"))
      if(acbPrepared then
        unless(equal(car(acbPrepared) acbEvidence) error("Prepared CDF procedures changed"))
        foreach(acbValue cadr(acbPrepared)
          cdfFindParamByName(cdfgData car(acbValue))~>value=cadr(acbValue))
      else
      acbCall=car(acbEvidence)
      when(acbCall
        unless(eq(getd(car(acbCall)) cadr(acbCall)) error("CDF init procedure changed"))
        apply(car(acbCall) list(cdfgData)))
      when(isCallable('aiCdfProbeCheckpoint) aiCdfProbeCheckpoint(cdfgData "builtin:form_init"))
      foreach(acbRow acbRows
        acbParam=cdfFindParamByName(cdfgData car(acbRow))
        ; Dynamic editable/display callbacks are GUI policy, not parameter recalculation.
        acbParam~>value=caddr(acbRow)
        when(acbOutcome acbOutcome['parameter]=car(acbRow))
        unwindProtect(
          aiCdfCallbackInvoke(cadr(assoc(car(acbRow) cddr(acbEvidence))))
          when(isCallable('aiCdfProbeCheckpoint) aiCdfProbeCheckpoint(cdfgData strcat("builtin:set:" car(acbRow)))))))
      unless(equal(acbEvidence aiCdfCallbackEvidence(cdfgData acbRows)) error("CDF procedures changed during execution"))
      foreach(acbRow acbRows
        unless(aiCdfCallbackEqual(cdfFindParamByName(cdfgData car(acbRow))~>value caddr(acbRow))
          error("CDF callback changed requested value: %s requested=%L effective=%L; inspect callback order/constraints"
            car(acbRow) caddr(acbRow) cdfFindParamByName(cdfgData car(acbRow))~>value)))
      if(acbEvaluateOnly then
        acbResult=list(acbEvidence foreach(mapcar acbP cdfgData~>parameters list(acbP~>name acbP~>value)))
      else
      if(acbInst then
        unless(cdfUpdateInstParam(acbInst) error("CDF instance update failed"))
      else
        acbInst=schCreateInst(acbCv acbMaster acbName acbXY acbOrient)
        unless(acbInst error("CDF instance creation failed; check target/master cell identity and CIW diagnostics")))
      unless(equal(acbEvidence aiCdfCallbackEvidence(cdfgData acbRows)) error("CDF procedures changed during commit"))
      acbInstCdf=cdfGetInstCDF(acbInst)
      foreach(acbRow acbRows
        unless(aiCdfCallbackEqual(cdfFindParamByName(acbInstCdf car(acbRow))~>value caddr(acbRow))
          error("CDF commit changed requested value: %s" car(acbRow))))
      acbResult=acbInst))
      progn(
        foreach(acbPair acbSaved car(acbPair)~>value=cadr(acbPair))
        when(acbOutcome
          acbOutcome['valuesRestored]=aiCircuitBool(forall(acbPair acbSaved equal(car(acbPair)~>value cadr(acbPair))))
          acbOutcome['metadataUnchanged]=aiCircuitBool(equal(acbBefore aiCdfSourceState(cdfgData))))))
    unless(equal(acbBefore aiCdfSourceState(cdfgData)) error("Shared CDF metadata changed"))
    return(acbResult)))
procedure(aiCdfCallbackCreate(acbCv acbMaster acbName acbXY acbOrient acbRows)
  aiCdfCallbackApply(nil acbMaster acbRows acbCv acbName acbXY acbOrient))
