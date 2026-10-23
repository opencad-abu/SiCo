; Read-only handoff of a retained NEW Symbol to generic schematic/TB consumers.
procedure(aiTsBindingData(atdEntry)
  prog((atdCv atdCdf atdBody atdPins atdBox atdRows atdPoint atdEscape atdData atdSnapshot)
    aiTsValidate(atdEntry)
    unwindProtect(progn(
      atdCv=dbOpenCellViewByType(atdEntry['lib] atdEntry['cell] "symbol" "" "r")
      unless(and(atdCv !atdCv~>superMaster !atdCv~>isParamCell !atdCv~>instances
        !atdCv~>mosaics length(atdCv~>terminals)<=64) error("binding requires <=64 static flat scalar terminals"))
      atdCdf=cdfGetCellCDF(ddGetObj(atdEntry['lib] atdEntry['cell]))
      aiCrSafeCdf(atdCdf)
      unless(!atdCdf~>parameters error("parameterized design CDF requires an explicit project binding"))
      foreach(atdTerm atdCv~>terminals
        unless(and(atdTerm~>numBits==1 length(atdTerm~>pins)==1 !dbGetTermNetExpr(atdTerm))
          error("binding requires scalar single-pin terminals without inherited connections"))
        atdPins=cons(car(atdTerm~>pins)~>fig atdPins)
        atdPoint=car(aiCrCenters(atdCv atdTerm~>name))
        atdBody=cons(atdPoint atdBody))
      foreach(atdSignal atdCv~>signals
        when(dbGetSigNetExpr(atdSignal) error("inherited signal is unsupported")))
      foreach(atdShape atdCv~>shapes
        unless(or(member(atdShape atdPins) member(atdShape~>objType list("label" "text"))
          equal(atdShape~>lpp list("instance" "drawing")))
          atdBody=append(atdShape~>bBox atdBody)))
      ; Avoid coordinate alias function values in serialized contexts.
      atdBox=list(list(apply('min mapcar('car atdBody)) apply('min mapcar('cadr atdBody)))
        list(apply('max mapcar('car atdBody)) apply('max mapcar('cadr atdBody))))
      foreach(atdRow atdEntry['rows]
        atdPoint=car(aiCrCenters(atdCv car(atdRow)))
        ; Geometry decides the outward direction, not terminal names/directions.
        atdEscape=cond(
          (abs(xCoord(atdPoint)-xCoord(car(atdBox)))<1e-8 "left")
          (abs(xCoord(atdPoint)-xCoord(cadr(atdBox)))<1e-8 "right")
          (abs(yCoord(atdPoint)-yCoord(cadr(atdBox)))<1e-8 "up")
          (abs(yCoord(atdPoint)-yCoord(car(atdBox)))<1e-8 "down")
          (t nil))
        unless(atdEscape error("pin is inside occupied bounds; explicit routing needed: %s" car(atdRow)))
        atdRows=cons(list(car(atdRow) cadr(atdRow) atdPoint atdEscape) atdRows))
      atdSnapshot=sprintf(nil "%L" list(aiTsSnapshot(atdCv) aiCrCdfRows(atdCdf)
        atdCdf~>formInitProc atdCdf~>doneProc atdCdf~>simInfo))
      unless(strlen(atdSnapshot)<=48000 error("symbol binding signature exceeds budget"))
      atdData=aiCircuitObject(list(list("ok" t) list("symbol_ref" atdEntry['ref])
        list("task_ref" atdEntry['task]) list("template_ref" atdEntry['template])
        list("preview_digest" atdEntry['digest])
        list("target" list(atdEntry['lib] atdEntry['cell] "symbol"))
        list("library_path" atdEntry['workPath]) list("occupied_bbox" atdBox)
        list("ports" reverse(atdRows)) list("signature" atdSnapshot))) t)
      when(atdCv dbClose(atdCv)))
    return(atdData)))

procedure(aiTemplateSymbolBinding(atgRef)
  prog((atgEntry atgResult)
    atgEntry=aiTemplateSymbols[atgRef]
    unless(and(atgEntry !atgEntry['error] atgEntry['saved] atgEntry['verified])
      return(aiCircuitError("symbol_not_ready" "A successfully created retained Symbol is required")))
    atgResult=errset(aiTsBindingData(atgEntry) t)
    unless(atgResult return(aiCircuitError("symbol_binding_unavailable"
      "Symbol/library/window/interface changed or unsupported static design binding; inspect CIW" atgRef)))
    return(car(atgResult))))
