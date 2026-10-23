; CDF diagnostics data only. No callbacks, editable evaluation or PDK discovery.
procedure(aiCdfRevision() "20260910.ai.cdf.v1")
procedure(aiCdfErrorText()
  let((acdText)
    acdText=if(errset.errset then sprintf(nil "%L" errset.errset) else "inspect native CIW diagnostics")
    if(strlen(acdText)>2048 then substring(acdText 1 2048) else acdText)))
unless(boundp('aiCdfCaptures) aiCdfCaptures=makeTable("aiCdfCaptures" nil))
unless(boundp('aiCdfCaptureCount) aiCdfCaptureCount=0)
procedure(aiCdfTyped(acdValue @optional (acdDepth 0))
  prog((acdItems acdTail acdCount)
    cond((null(acdValue) return(list("nil" nil)))
      (stringp(acdValue)
        return(if(strlen(acdValue)<=8192 list("string" acdValue) list("unsupported" "string_limit"))))
      (numberp(acdValue) return(list(if(fixp(acdValue) "integer" "float") acdValue)))
      (symbolp(acdValue) return(list("symbol" symbolToString(acdValue))))
      (and(listp(acdValue) acdDepth<8)
        acdTail=acdValue acdCount=0
        while(and(acdTail listp(acdTail) acdCount<1024)
          acdItems=cons(aiCdfTyped(car(acdTail) acdDepth+1) acdItems)
          acdTail=cdr(acdTail) acdCount=acdCount+1)
        return(if(acdTail list("unsupported" "list_limit_or_dotted_tail") list("list" reverse(acdItems)))))
      (t return(list("unsupported" "opaque_or_depth_limit"))))))
procedure(acdValueLess(acdA acdB) alphalessp(car(acdA) car(acdB)))
procedure(aiCdfValues(acdCdf)
  sort(foreach(mapcar acdParam acdCdf~>parameters
    list(acdParam~>name aiCdfTyped(acdParam~>value)))
    'acdValueLess))
procedure(aiCdfParamRows(acdCdf acdInst)
  prog((acdRows acdProp)
    when(length(acdCdf~>parameters)>512 error("CDF has more than 512 parameters"))
    foreach(acdParam acdCdf~>parameters
      acdProp=when(acdInst dbFindProp(acdInst acdParam~>name))
      acdRows=cons(list(acdParam~>name acdParam~>paramType acdParam~>prompt
        aiCdfTyped(acdParam~>defValue) aiCdfTyped(acdParam~>value)
        aiCdfTyped(acdParam~>choices) aiCdfTyped(acdParam~>units)
        aiCdfTyped(acdParam~>editable) aiCdfTyped(acdParam~>display)
        aiCdfTyped(acdParam~>callback) aiCdfTyped(acdParam~>parseAsNumber)
        aiCdfTyped(acdParam~>parseAsCEL)
        list(aiCircuitBool(acdProp) when(acdProp acdProp~>valueType)
          when(acdProp aiCdfTyped(acdProp~>value)))) acdRows))
    return(sort(acdRows 'acdValueLess))))
procedure(aiCdfSnapshot(acdTarget acdInstance)
  prog((acdCv acdInst acdMaster acdCdf acdAnswer acdInit)
    unwindProtect(progn(
      unless(apply('ddGetObj acdTarget) error("CDF target does not exist"))
      acdCv=dbOpenCellViewByType(car(acdTarget) cadr(acdTarget) caddr(acdTarget) "" "r")
      unless(acdCv error("CDF target cannot be opened"))
      if(acdInstance then
        unless(acdCv~>cellViewType=="schematic" error("instance CDF requires schematic"))
        acdInst=dbFindAnyInstByName(acdCv acdInstance)
        unless(acdInst error("instance not found; direct instance name required"))
        acdMaster=acdInst~>master
      else
        acdMaster=acdCv)
      ; Ordinary work libraries can be inspected without a process initializer.
      ; If this master publishes libInit.il, initialize before freezing its CDF.
      when(ddGetObj(acdMaster~>libName nil nil "libInit.il")
        acdInit=aiPdkInitLibrary(acdMaster~>libName)
        unless(car(acdInit) error("%s: %s" cadr(acdInit) caddr(acdInit))))
      acdCdf=if(acdInst cdfGetInstCDF(acdInst)
        cdfGetCellCDF(ddGetObj(acdMaster~>libName acdMaster~>cellName)))
      acdAnswer=list(acdTarget acdInstance
        list(acdMaster~>libName acdMaster~>cellName acdMaster~>viewName)
        ddGetObjReadPath(ddGetObj(acdMaster~>libName))
        aiCircuitBool(dbIsCellViewModified(acdCv)) aiCircuitBool(acdCdf)
        aiCdfParamRows(acdCdf acdInst)
        list(aiCdfTyped(acdCdf~>formInitProc) aiCdfTyped(acdCdf~>doneProc))
        aiCdfTyped(acdCdf~>simInfo)
        ddGetObjReadPath(ddGetObj(car(acdTarget)))
        acdMaster~>cellViewType
        aiCrShapeRows(acdMaster))
      when(strlen(aiCircuitJson(acdAnswer))>524288 error("CDF snapshot exceeds 512 KiB")))
      when(acdCv dbClose(acdCv)))
    return(acdAnswer)))
procedure(aiCdfInspect(acdTarget acdInstance acdRef acdNames acdOffset acdLimit acdNewRef)
  prog((acdData acdResult acdRows acdPage acdIndex acdNext)
    acdResult=errset(progn(
      if(acdRef then
        acdData=aiCdfCaptures[acdRef]
        unless(acdData error("unknown CDF capture in this session"))
      else
        when(aiCdfCaptureCount>=64 error("64 CDF captures retained; session limit"))
        acdData=aiCdfSnapshot(acdTarget acdInstance)
        aiCdfCaptureCount=aiCdfCaptureCount+1
        acdRef=acdNewRef
        when(aiCdfCaptures[acdRef] error("capture reference conflict"))
        aiCdfCaptures[acdRef]=acdData)
      acdRows=if(acdNames setof(acdRow nth(6 acdData) member(car(acdRow) acdNames)) nth(6 acdData))
      acdIndex=0
      foreach(acdRow acdRows
        when(and(acdIndex>=acdOffset acdIndex<acdOffset+acdLimit) acdPage=cons(acdRow acdPage))
        acdIndex=acdIndex+1)
      when(acdOffset+acdLimit<length(acdRows) acdNext=acdOffset+acdLimit)
      t) t)
    unless(acdResult return(aiCircuitError("cdf_inspection_failed" aiCdfErrorText())))
    return(aiCircuitObject(list(list("ok" t) list("cdf_ref" acdRef) list("target" car(acdData))
      list("instance" cadr(acdData)) list("master" nth(2 acdData)) list("library_path" nth(3 acdData))
      list("target_modified" nth(4 acdData)) list("cdf_present" nth(5 acdData))
      list("scope" if(cadr(acdData) "effective_instance" "effective_cell"))
      list("parameters" reverse(acdPage)) list("hooks" nth(7 acdData)) list("sim_info" nth(8 acdData))
      list("total_parameters" length(nth(6 acdData))) list("matched_parameters" length(acdRows))
      list("missing_parameters" setof(acdName acdNames !assoc(acdName nth(6 acdData))))
      list("offset" acdOffset) list("next_offset" acdNext) list("callbacks_executed" 'jsonFalse)
      list("tool_version" aiCdfRevision()) list("virtuoso_version" getVersion())
      list("freshness" "frozen_session_capture"))))))
