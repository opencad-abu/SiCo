; Qualified gpdk045 v3.5 only. Initialize the real CDF BEFORE schCreateInst,
; which calls PasCdfDone. Never save base/user PDK CDF. Restore effective values.
procedure(aiGateMos(agmCv agmMaster agmName agmXy agmOrient agmGate)
  prog((cdfgData cdfgForm agmSaved agmInst agmParam agmWidth agmExpected)
    cdfgForm=nil
    cdfgData=cdfGetCellCDF(ddGetObj(agmMaster~>libName agmMaster~>cellName))
    unless(and(cdfgData cdfgData~>formInitProc=="PasCdfFormInit" cdfgData~>doneProc=="PasCdfDone")
      error("unsupported gpdk CDF hooks"))
    agmWidth=if(or(agmMaster~>cellName=="pmos1v" agmGate=="NAND2") "480n" "240n")
    agmExpected=list(list("l" "45n") list("w" agmWidth) list("fingers" "1") list("m" "1"))
    foreach(agmPair agmExpected
      agmParam=cdfFindParamByName(cdfgData car(agmPair))
      unless(and(agmParam agmParam~>paramType=="string"
        agmParam~>callback==sprintf(nil "gpdk045_mosCB( '%s )" car(agmPair)))
        error("unsupported gpdk parameter/callback")))
    agmSaved=foreach(mapcar agmP cdfgData~>parameters list(agmP agmP~>value))
    unwindProtect(progn(
      foreach(agmP cdfgData~>parameters agmP~>value=agmP~>defValue)
      PasCdfFormInit(cdfgData)
      foreach(agmPair agmExpected
        agmParam=cdfFindParamByName(cdfgData car(agmPair))
        agmParam~>value=cadr(agmPair)
        ; Callback text is checked against this qualified profile, never user code.
        evalstring(agmParam~>callback))
      agmInst=schCreateInst(agmCv agmMaster agmName agmXy agmOrient)
      unless(agmInst error("MOS creation failed"))
      cdfgData=cdfGetInstCDF(agmInst)
      foreach(agmPair append(agmExpected list(list("fw" agmWidth)
        list("ad" if(agmWidth=="240n" "33.6f" "67.2f"))
        list("as" if(agmWidth=="240n" "33.6f" "67.2f"))
        list("pd" if(agmWidth=="240n" "760n" "1.24u"))
        list("ps" if(agmWidth=="240n" "760n" "1.24u"))
        list("model" if(agmMaster~>cellName=="pmos1v" "g45p1svt" "g45n1svt"))))
        unless(cdfFindParamByName(cdfgData car(agmPair))~>value==cadr(agmPair)
          error("CDF callback readback mismatch: %s" car(agmPair)))))
      foreach(agmPair agmSaved car(agmPair)~>value=cadr(agmPair)))
    return(agmInst)))

procedure(aiGateParams(agpInst)
  let((agpCdf)
    agpCdf=cdfGetInstCDF(agpInst)
    foreach(mapcar agpP agpCdf~>parameters list(agpP~>name agpP~>value))))

procedure(aiGatePoint(agpCv agpValue)
  if(stringp(car(agpValue)) then
    aiRcAnchor(dbFindAnyInstByName(agpCv car(agpValue)) cadr(agpValue))
  else agpValue))

procedure(aiGateWire(agwCv agwPoints agwName)
  prog((agwSegments agwBest agwLength agwBox agwSize agwPoint)
    agwSegments=schCreateWire(agwCv "draw" "full" agwPoints 0.0625 0.0625 0.0)
    unless(agwSegments error("wire creation failed"))
    agwLength=-1
    foreach(agwSegment agwSegments
      agwBox=agwSegment~>bBox
      agwSize=abs(xCoord(cadr(agwBox))-xCoord(car(agwBox)))+
        abs(yCoord(cadr(agwBox))-yCoord(car(agwBox)))
      when(agwSize>agwLength agwLength=agwSize agwBest=agwSegment))
    agwBox=agwBest~>bBox
    agwPoint=list((xCoord(car(agwBox))+xCoord(cadr(agwBox)))/2.0+0.0625
      (yCoord(car(agwBox))+yCoord(cadr(agwBox)))/2.0+0.0625)
    unless(schCreateWireLabel(agwCv agwBest agwPoint agwName "lowerLeft" "R0" "stick" 0.0625 nil)
      error("wire label failed"))
    return(t)))

procedure(aiGateSchematic(agsCv agsSpec agsGate)
  prog((agsMaster agsInst agsMasterName agsCheck)
    foreach(agsDev car(agsSpec)
      agsMaster=nil
      unwindProtect(progn(
        agsMaster=dbOpenCellViewByType(cadr(agsDev) caddr(agsDev) "symbol" "" "r")
        unless(agsMaster error("missing master: %s/%s" cadr(agsDev) caddr(agsDev)))
        agsInst=if(cadr(agsDev)=="gpdk045" then
          aiGateMos(agsCv agsMaster car(agsDev) nth(3 agsDev) nth(4 agsDev) agsGate)
        else schCreateInst(agsCv agsMaster car(agsDev) nth(3 agsDev) nth(4 agsDev)))
        unless(agsInst error("instance creation failed"))
        foreach(agsProp nth(5 agsDev) aiRcProperty(agsInst car(agsProp) cadr(agsProp))))
        when(agsMaster dbClose(agsMaster))))
    foreach(agsPort cadr(agsSpec)
      agsMasterName=case(cadr(agsPort) ("input" "ipin") ("output" "opin") (t "iopin"))
      agsMaster=nil
      unwindProtect(progn(
        agsMaster=dbOpenCellViewByType("basic" agsMasterName "symbol" "" "r")
        unless(and(agsMaster schCreatePin(agsCv agsMaster car(agsPort) cadr(agsPort)
          nil caddr(agsPort) "R0")) error("schematic port creation failed")))
        when(agsMaster dbClose(agsMaster))))
    foreach(agsWire caddr(agsSpec)
      aiGateWire(agsCv foreach(mapcar agsPoint cdr(agsWire) aiGatePoint(agsCv agsPoint)) car(agsWire)))
    unless(schCreateNoteLabel(agsCv -1:2.5 strcat(agsGate " | gpdk045 v3.5 | 1V")
      "lowerLeft" "R0" "fixed" 0.125 "normalLabel") error("note creation failed"))
    agsCheck=schCheck(agsCv)
    unless(equal(agsCheck list(0 0)) error("schematic check failed: %L" agsCheck))
    return(agsCheck)))

procedure(aiGateSymbol(agsCv agsPorts agsGate)
  let((agsMaster agsPt agsRight agsInside)
    unwindProtect(progn(
      agsMaster=dbOpenCellViewByType("basic" "sympin" "symbolNN" "" "r")
      unless(agsMaster error("missing qualified symbol pin master"))
      unless(schCreateSymbolShape(agsCv "rectangle" "outline" list(0:-1 1:1)) error("symbol box failed"))
      foreach(agsPort agsPorts
        agsPt=caddr(agsPort) agsRight=cadr(agsPort)=="output"
        agsInside=list(if(agsRight 1 0) yCoord(agsPt))
        unless(schCreateSymbolPin(agsCv agsMaster car(agsPort) cadr(agsPort) agsPt "R0")
          error("symbol pin failed"))
        unless(schCreateSymbolShape(agsCv "line" "outline" list(agsPt agsInside)) error("symbol stub failed"))
        unless(schCreateSymbolLabel(agsCv list(if(agsRight 0.9375 0.0625) yCoord(agsPt))
          "pin name" car(agsPort) if(agsRight "centerRight" "centerLeft") "R0" "stick" 0.0625 "normalLabel")
          error("symbol pin label failed"))
        aiCircuitAnalogPinLabel(agsCv car(agsPort) agsPt agsRight))
      unless(schCreateSymbolLabel(agsCv 0.5:0 "symbol shape" agsGate "centerCenter"
        "R0" "fixed" 0.0875 "normalLabel") error("symbol name failed"))
      unless(schCreateSymbolLabel(agsCv 0:1.125 "instance label" "[@instanceName]" "lowerLeft"
        "R0" "fixed" 0.125 "NLPLabel") error("instance label failed"))
      aiCircuitCreateSymbolBox(agsCv))
      when(agsMaster dbClose(agsMaster)))
    t))
