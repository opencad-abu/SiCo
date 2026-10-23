; Bounded analogLib RC testbench. sch* creation; actual master pin transforms.
procedure(aiRcGeometrySnapshot(agsCv)
  sort(append(
    foreach(mapcar agsInst agsCv~>instances
      sprintf(nil "%L" list(agsInst~>name agsInst~>libName agsInst~>cellName agsInst~>viewName
        agsInst~>transform)))
    foreach(mapcar agsShape agsCv~>shapes
      sprintf(nil "%L" list(agsShape~>objType agsShape~>lpp agsShape~>bBox
        agsShape~>points agsShape~>theLabel)))) 'alphalessp))
procedure(aiRcAnchor(araInst araName)
  prog((araTerm araPins araBox araPoint)
    araTerm=car(setof(araT araInst~>master~>terminals araT~>name==araName))
    unless(araTerm error("missing terminal %s" araName))
    araPins=araTerm~>pins
    unless(length(araPins)==1 error("recipe requires one pin figure: %s" araName))
    araBox=car(araPins)~>fig~>bBox
    unless(araBox error("missing pin geometry"))
    araPoint=list((xCoord(car(araBox))+xCoord(cadr(araBox)))/2.0
      (yCoord(car(araBox))+yCoord(cadr(araBox)))/2.0)
    return(dbTransformPoint(araPoint araInst~>transform))))

procedure(aiRcProperty(arpInst arpName arpValue)
  prog((arpCdf arpParam)
    arpCdf=cdfGetInstCDF(arpInst)
    arpParam=cdfFindParamByName(arpCdf arpName)
    unless(and(arpParam arpParam~>paramType=="string" or(!arpParam~>callback arpParam~>callback==""))
      error("unsupported CDF type/callback: %s" arpName))
    unless(dbReplaceProp(arpInst arpName "string" arpValue) error("parameter write failed"))
    unless(dbGet(arpInst arpName)==arpValue error("parameter roundtrip failed"))
    return(t)))

procedure(aiRcWire(arwCv arwPoints @optional arwLabel)
  prog((arwSegments)
    arwSegments=schCreateWire(arwCv "draw" "full" arwPoints 0.0625 0.0625 0.0)
    unless(arwSegments error("wire creation failed"))
    when(arwLabel
      unless(schCreateWireLabel(arwCv car(arwSegments) car(arwPoints) arwLabel
        "lowerLeft" "R0" "stick" 0.0625 nil) error("wire label failed")))
    return(arwSegments)))

procedure(aiRcVerify(arvCv arvEntry)
  prog((arvInst arvExpected arvActual arvP)
    unless(length(arvCv~>instances)==5 error("unexpected instance count"))
    foreach(arvSpec list(list("V1" "vpulse" list(list("PLUS" "IN") list("MINUS" "gnd!")))
      list("R1" "res" list(list("PLUS" "IN") list("MINUS" "OUT")))
      list("C1" "cap" list(list("PLUS" "OUT") list("MINUS" "gnd!")))
      list("G0" "gnd" list(list("gnd!" "gnd!"))))
      arvInst=dbFindAnyInstByName(arvCv car(arvSpec))
      unless(and(arvInst arvInst~>libName=="analogLib" arvInst~>cellName==cadr(arvSpec)
        arvInst~>viewName=="symbol") error("instance master changed"))
      foreach(arvConn caddr(arvSpec)
        arvActual=car(setof(arvIt arvInst~>instTerms arvIt~>name==car(arvConn)))
        unless(and(arvActual arvActual~>net~>name==cadr(arvConn)) error("net connectivity mismatch"))))
    unless(and(length(arvCv~>terminals)==1 car(arvCv~>terminals)~>name=="OUT"
      car(arvCv~>terminals)~>direction=="output"
      car(arvCv~>terminals)~>net~>name=="OUT") error("output terminal mismatch"))
    foreach(arvRow arvEntry['properties]
      arvInst=dbFindAnyInstByName(arvCv car(arvRow))
      foreach(arvPair cdr(arvRow)
        arvP=car(arvPair) arvExpected=cadr(arvPair)
        unless(dbGet(arvInst arvP)==arvExpected error("recipe parameter changed: %s" arvP))))
    return(t)))

procedure(aiRcValidateSaved(arvEntry)
  let((arvReadCv arvResult)
    arvReadCv=if(arvEntry['mode]=="foreground" then arvEntry['cv]
      else dbOpenCellViewByType(arvEntry['lib] arvEntry['cell] "schematic" "schematic" "r"))
    arvResult=unwindProtect(
      progn(unless(arvReadCv error("cannot read saved circuit"))
        when(dbIsCellViewModified(arvReadCv) error("schematic has unsaved changes"))
        unless(equal(aiRcGeometrySnapshot(arvReadCv) arvEntry['geometry])
          error("schematic geometry changed since recipe save"))
        aiRcVerify(arvReadCv arvEntry))
      when(and(arvReadCv arvEntry['mode]=="background") dbClose(arvReadCv)))
    arvResult))

procedure(aiRcCreate(arcTask arcRequest arcLib arcCell arcR arcC arcV arcDigest)
  prog((arcMode arcKey arcSignature arcRecord arcResult arcEntry arcMasters arcMaster
        arcCv arcV1 arcR1 arcC1 arcG0 arcWindow arcTau arcProperties arcInst)
    arcMode=aiCircuitTasks[arcTask]
    unless(arcMode return(aiCircuitError("task_not_found" "Begin task with confirmed mode first")))
    unless(and(aiCircuitTargetP(arcLib arcCell "schematic" "schematic")
      aiCircuitTextP(arcRequest) stringp(arcDigest) strlen(arcDigest)==64
      numberp(arcR) arcR>=1 arcR<=1e9 numberp(arcC) arcC>=1e-15 arcC<=1e-3
      numberp(arcV) arcV>=0.001 arcV<=100 arcR*arcC>=1e-12 arcR*arcC<=1)
      return(aiCircuitError("invalid_recipe" "invalid RC recipe parameters")))
    arcKey=strcat("create:" arcRequest)
    arcSignature=list(arcTask arcLib arcCell arcR arcC arcV arcDigest)
    arcResult=errset(aiRcReserve(arcKey arcSignature) nil)
    unless(arcResult return(aiCircuitError("request_conflict_or_limit" "Recipe request conflict/limit")))
    arcRecord=car(arcResult)
    when(arcRecord['entry] return(aiRcReply(arcRecord['entry])))
    arcEntry=makeTable("aiRcCircuit" nil) arcRecord['entry]=arcEntry
    arcEntry['ref]=strcat("rc:" arcRequest) arcEntry['task]=arcTask arcEntry['mode]=arcMode
    arcEntry['lib]=arcLib arcEntry['cell]=arcCell arcEntry['digest]=arcDigest
    arcEntry['r]=arcR arcEntry['c]=arcC arcEntry['v]=arcV
    aiRcCircuits[arcEntry['ref]]=arcEntry
    arcEntry['stage]="preflight"
    arcResult=errset(unwindProtect(progn(
      unless(and(ddGetObj(arcLib) ddIsObjWritable(ddGetObj(arcLib))) error("library missing/read-only"))
      when(ddGetObj(arcLib arcCell "schematic") error("target already exists; never overwrite"))
      when(and(arcMode=="foreground" !aiCircuitGuiP()) error("foreground GUI unavailable"))
      arcMasters=makeTable("aiRcMasters" nil)
      foreach(arcName list("res" "cap" "vpulse" "gnd" "opin")
        arcMaster=dbOpenCellViewByType(if(arcName=="opin" "basic" "analogLib") arcName "symbol" "" "r")
        unless(arcMaster error("missing symbol master: %s" arcName))
        arcMasters[arcName]=arcMaster)
      arcEntry['stage]="create_view"
      if(arcMode=="foreground" then
        arcWindow=deNewCellView(arcLib arcCell "schematic" "schematic" nil)
        unless(arcWindow error("new schematic window failed"))
        aiCircuitRaiseWindow(arcWindow)
        arcEntry['window]=arcWindow arcCv=geGetWindowCellView(arcWindow)
        unless(geGetEditCellView(arcWindow)==arcCv error("unexpected EIP context"))
      else arcCv=dbOpenCellViewByType(arcLib arcCell "schematic" "schematic" "a"))
      unless(and(arcCv arcCv~>mode!="r") error("new view is not writable"))
      arcEntry['cv]=arcCv arcEntry['stage]="instances_and_parameters"
      arcV1=schCreateInst(arcCv arcMasters["vpulse"] "V1" 0:1 "R0")
      arcR1=schCreateInst(arcCv arcMasters["res"] "R1" 1:1 "R90")
      arcC1=schCreateInst(arcCv arcMasters["cap"] "C1" 2.5:1 "R0")
      arcG0=schCreateInst(arcCv arcMasters["gnd"] "G0" 0:0 "R0")
      unless(and(arcV1 arcR1 arcC1 arcG0) error("instance creation failed"))
      arcTau=arcR*arcC
      arcProperties=list(list("R1" list("r" sprintf(nil "%.12g" arcR*1.0)))
        list("C1" list("c" sprintf(nil "%.12g" arcC*1.0)))
        list("V1" list("vdc" sprintf(nil "%.12g" arcV*1.0)) list("v1" "0")
          list("v2" sprintf(nil "%.12g" arcV*1.0)) list("td" sprintf(nil "%.12g" arcTau))
          list("tr" sprintf(nil "%.12g" arcTau/1000)) list("tf" sprintf(nil "%.12g" arcTau/1000))
          list("pw" sprintf(nil "%.12g" arcTau*20)) list("per" sprintf(nil "%.12g" arcTau*40))))
      arcEntry['properties]=arcProperties
      foreach(arcRow arcProperties
        arcInst=dbFindAnyInstByName(arcCv car(arcRow))
        foreach(arcPair cdr(arcRow) aiRcProperty(arcInst car(arcPair) cadr(arcPair))))
      arcEntry['stage]="pin_and_wires"
      unless(schCreatePin(arcCv arcMasters["opin"] "OUT" "output" nil 3.5:1 "R0") error("output pin failed"))
      aiRcWire(arcCv list(aiRcAnchor(arcV1 "PLUS") aiRcAnchor(arcR1 "PLUS")) "IN")
      aiRcWire(arcCv list(aiRcAnchor(arcR1 "MINUS") aiRcAnchor(arcC1 "PLUS")) "OUT")
      aiRcWire(arcCv list(aiRcAnchor(arcC1 "PLUS") 3.5:1))
      aiRcWire(arcCv list(aiRcAnchor(arcV1 "MINUS") aiRcAnchor(arcG0 "gnd!")))
      aiRcWire(arcCv list(aiRcAnchor(arcC1 "MINUS") 2.5:0 aiRcAnchor(arcG0 "gnd!")))
      arcEntry['stage]="notes"
      unless(schCreateNoteShape(arcCv "rectangle" "solid" list(-1:-0.5 4:1.75)) error("note shape failed"))
      unless(schCreateNoteLabel(arcCv -0.75:1.5 "RC low-pass testbench" "lowerLeft"
        "R0" "fixed" 0.125 "normalLabel") error("note label failed"))
      arcEntry['stage]="check"
      arcEntry['check]=schCheck(arcCv)
      unless(equal(arcEntry['check] list(0 0)) error("Check & Save reported errors/warnings"))
      aiRcVerify(arcCv arcEntry)
      arcEntry['stage]="save"
      unless(dbSave(arcCv) error("save failed"))
      arcEntry['geometry]=aiRcGeometrySnapshot(arcCv)
      arcEntry['saved]=t arcEntry['stage]="schematic_saved"
      when(arcWindow hiZoomIn(arcWindow arcCv~>bBox))
      t)
      progn(when(arcMasters foreach(arcName arcMasters when(arcMasters[arcName] dbClose(arcMasters[arcName]))))
        when(and(arcCv arcMode=="background") dbClose(arcCv) arcEntry['cv]=nil))) t)
    unless(arcResult arcEntry['error]=strcat("Failed at " arcEntry['stage]
      "; inspect CIW. Partial target may exist; same request never rebuilds it."))
    return(aiRcReply(arcEntry))))
