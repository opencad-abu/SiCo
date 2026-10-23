; Creation-only dependency validation. No PDK enumeration, classification or callback evaluation.
procedure(aiCreateRevision() "20261022.ai.create.v12")
procedure(aiCrEmpty(accValue) or(null(accValue) equal(accValue "")))
procedure(aiCrValueText(acvValue)
  let((acvInfix)
    ; Function autoloading must not change the printed representation of list data.
    acvInfix=status(printinfix)
    unwindProtect(progn(sstatus(printinfix nil) sprintf(nil "%L" acvValue))
      sstatus(printinfix acvInfix))))
procedure(aiCrRowLess(acrLeft acrRight)
  alphalessp(aiCrValueText(acrLeft) aiCrValueText(acrRight)))
procedure(aiCrContains(accBox accPoint)
  and(xCoord(accPoint)>=xCoord(car(accBox))-1e-8 yCoord(accPoint)>=yCoord(car(accBox))-1e-8
    xCoord(accPoint)<=xCoord(cadr(accBox))+1e-8 yCoord(accPoint)<=yCoord(cadr(accBox))+1e-8))
procedure(aiCrCenters(accMaster accName)
  prog((accTerm accPoints accBox)
    accTerm=car(setof(accT accMaster~>terminals accT~>name==accName))
    unless(accTerm error("missing master terminal: %s" accName))
    foreach(accPin accTerm~>pins
      unless(and(accPin~>fig member(accPin~>fig~>objType list("rect" "polygon")))
        error("live creator v1 requires rectangular pin figures"))
      accBox=accPin~>fig~>bBox
      when(accPin~>fig~>objType=="polygon"
        unless(and(length(accPin~>fig~>points)==4
          forall(accVertex list(car(accBox) cadr(accBox)
            list(xCoord(car(accBox)) yCoord(cadr(accBox))) list(xCoord(cadr(accBox)) yCoord(car(accBox))))
            member(accVertex accPin~>fig~>points))) error("pin polygon is not an axis-aligned rectangle")))
      accPoints=cons(list((xCoord(car(accBox))+xCoord(cadr(accBox)))/2.0
        (yCoord(car(accBox))+yCoord(cadr(accBox)))/2.0) accPoints))
    unless(accPoints error("terminal has no physical pin: %s" accName))
    return(accPoints)))

procedure(aiCrCdfRows(accCdf)
  prog((accRows)
    when(length(accCdf~>parameters)>256 error("effective CDF exceeds 256 parameters"))
    accRows=sort(foreach(mapcar accParam accCdf~>parameters
      aiCrValueText(list(accParam~>name accParam~>paramType accParam~>defValue accParam~>value
        accParam~>choices accParam~>editable accParam~>display accParam~>callback
        accParam~>parseAsNumber accParam~>parseAsCEL accParam~>units
        accParam~>use accParam~>dontSave accParam~>storeDefault))) 'alphalessp)
    when(strlen(aiCrValueText(accRows))>131072 error("effective CDF snapshot exceeds budget"))
    return(accRows)))
procedure(aiCrShapeRows(accCv)
  prog((accRows)
    when(length(accCv~>shapes)>4096 error("symbol has too many shapes"))
    accRows=sort(foreach(mapcar accShape accCv~>shapes aiCrValueText(
      list(accShape~>objType accShape~>lpp accShape~>bBox accShape~>points
        accShape~>theLabel accShape~>labelType
        when(accShape~>objType=="label" list(accShape~>xy accShape~>justify accShape~>orient
          accShape~>font accShape~>height accShape~>isVisible accShape~>isDrafted))))) 'alphalessp)
    when(strlen(aiCrValueText(accRows))>262144 error("symbol geometry snapshot exceeds budget"))
    return(accRows)))
procedure(aiCrSafeCdf(accCdf)
  when(accCdf
    unless(and(aiCrEmpty(accCdf~>formInitProc) aiCrEmpty(accCdf~>doneProc))
      error("CDF cell hooks need a project extension"))
    foreach(accParam accCdf~>parameters
      unless(aiCrEmpty(accParam~>callback) error("CDF parameter callbacks need a project extension")))) t)
procedure(aiCrParam(accCdf accRow @optional accExtension)
  prog((accParam accType accEdit)
    accParam=cdfFindParamByName(accCdf car(accRow))
    unless(accParam error("parameter absent in effective CDF: %s" car(accRow)))
    accType=case(cadr(accRow) ("integer" "int") ("number" "float")
      ("string" "string") ("boolean" "boolean") (t nil))
    unless(or(accParam~>paramType==accType
      and(accExtension accType=="string" member(accParam~>paramType list("cyclic" "radio"))))
      error("effective CDF parameter type changed"))
    when(and(accParam~>choices !member(caddr(accRow) accParam~>choices))
      error("value is not in live CDF choices"))
    accEdit=accParam~>editable
    ; Read conditions as data. A nonempty expression is not a static true condition.
    ; nil means the optional condition is absent. The expression "nil" disables editing.
    unless(accExtension
      unless(or(null(accEdit) eq(accEdit t) equal(accEdit "t")) error("editable condition is false/unknown/dynamic"))
      unless(aiCrEmpty(accParam~>callback) error("parameter callback unsupported")))
    return(accType)))

procedure(aiCrMasterSignature(accMaster accRow)
  prog((accCdf accTerms accPins accPoints accBody accSig accWanted accType accExtension)
    unless(and(accMaster accMaster~>cellViewType=="schematicSymbol" !dbIsCellViewModified(accMaster)
      !accMaster~>superMaster !accMaster~>isParamCell !accMaster~>instances)
      error("saved static flat symbol required"))
    accCdf=cdfGetCellCDF(ddGetObj(accMaster~>libName accMaster~>cellName))
    foreach(accT accMaster~>terminals
      when(dbGetTermNetExpr(accT) error("inherited terminal connections are not supported by the pin-based creator")))
    foreach(accSignal accMaster~>signals
      when(dbGetSigNetExpr(accSignal) error("inherited signal connections are not supported by the pin-based creator")))
    accExtension=when(nth(11 accRow) aiCrExtensionValidate(accMaster accRow))
    unless(accExtension aiCrSafeCdf(accCdf))
    foreach(accParamRow nth(5 accRow) aiCrParam(accCdf accParamRow accExtension))
    accTerms=sort(foreach(mapcar accT accMaster~>terminals
      aiCrValueText(list(accT~>name accT~>direction))) 'alphalessp)
    unless(equal(accTerms sort(foreach(mapcar accT nth(7 accRow) aiCrValueText(accT)) 'alphalessp))
      error("live symbol interface differs from plan"))
    foreach(accT accMaster~>terminals
      foreach(accPin accT~>pins accPins=cons(accPin~>fig accPins)))
    foreach(accShape accMaster~>shapes
      unless(or(member(accShape accPins) member(accShape~>objType list("label" "text"))
        equal(accShape~>lpp list("instance" "drawing")))
        unless(and(aiCrContains(nth(8 accRow) car(accShape~>bBox))
          aiCrContains(nth(8 accRow) cadr(accShape~>bBox))) error("plan understates symbol body extent"))))
    foreach(accTermRow nth(9 accRow)
      accPoints=aiCrCenters(accMaster car(accTermRow)) accWanted=cadr(accTermRow)
      unless(and(length(accPoints)==length(accWanted)
        forall(accPoint accPoints member(accPoint accWanted))) error("pin geometry differs from plan")))
    accSig=list(accTerms aiCrCdfRows(accCdf) aiCrShapeRows(accMaster) accExtension
      aiCrValueText(list(accCdf~>formInitProc accCdf~>doneProc accCdf~>simInfo)))
    return(accSig)))

procedure(aiCrAbsent(accEntry)
  prog((accTarget)
    accTarget=car(accEntry['payload])
    when(accEntry['targetRecord] return(aiCrTargetCheck(accEntry)))
    when(apply('ddGetObj accTarget) error("target already exists; never overwrite"))
    foreach(accOpen dbGetOpenCellViews()
      when(aiCircuitCvMatches(accOpen accTarget) error("target already open, including unsaved view"))) return(t)))

unless(boundp('aiCrTargets) aiCrTargets=makeTable("aiCrTargets" nil))
procedure(aiCrTargetFacts(actTarget)
  prog((actCv actOwn actSnapshot actFacts actWindow actModified actState actPath actContents actWritable)
    unless(and(length(actTarget)==3 apply('aiCircuitTargetP append(actTarget list("schematic"))))
      error("Invalid schematic target"))
    actPath=ddGetObjReadPath(ddGetObj(car(actTarget)))
    unless(actPath error("Target library unavailable"))
    foreach(actOpen dbGetOpenCellViews()
      when(aiCircuitCvMatches(actOpen actTarget) actCv=actOpen))
    when(and(boundp('aiCopilotSource) aiCopilotSource aiCopilotSource['window])
      actWindow=aiCopilotSource['window]
      unless(and(member(actWindow hiGetWindowList('all))
        geGetWindowCellView(actWindow)==geGetEditCellView(actWindow)) error("Source closed or edit-in-place"))
      unless(aiCircuitCvMatches(geGetEditCellView(actWindow) actTarget) actWindow=nil))
    unwindProtect(progn(
      when(and(!actCv apply('ddGetObj actTarget))
        actCv=apply('dbOpenCellViewByType append(actTarget list("" "r"))) actOwn=t
        unless(actCv error("Existing schematic could not be read")))
      actState="absent"
      when(actCv
        unless(actCv~>cellViewType=="schematic" error("Target is not a schematic"))
        when(or(actCv~>mosaics actCv~>superMaster actCv~>isParamCell)
          error("Array or parameterized design requires separate inspection"))
        when(or(length(actCv~>instances)>1024 length(actCv~>shapes)>4096
          length(actCv~>nets)>4096 length(actCv~>terminals)>1024)
          error("Target exceeds bounded circuit inspection capacity"))
        actSnapshot=aiCrSnapshot(actCv) actModified=dbIsCellViewModified(actCv)
        when(strlen(aiCrValueText(actSnapshot))>4194304 error("Target snapshot exceeds budget"))
        actContents=list(
          sort(foreach(mapcar actInst actCv~>instances list(actInst~>name
            list(actInst~>libName actInst~>cellName actInst~>viewName)
            actInst~>xy actInst~>orient actInst~>bBox
            sort(foreach(mapcar actTerm actInst~>instTerms list(actTerm~>name actTerm~>net~>name)) 'aiCrRowLess)
            sort(foreach(mapcar actProp actInst~>prop
              list(actProp~>name actProp~>valueType aiCrValueText(actProp~>value))) 'aiCrRowLess))) 'aiCrRowLess)
          sort(foreach(mapcar actPort actCv~>terminals list(actPort~>name actPort~>direction actPort~>net~>name)) 'aiCrRowLess)
          sort(foreach(mapcar actShape actCv~>shapes list(actShape~>objType actShape~>lpp
            actShape~>bBox actShape~>points actShape~>theLabel actShape~>net~>name)) 'aiCrRowLess))
        ; Leave room for the metadata and Copilot's 180000-byte response envelope.
        when(strlen(aiCircuitJson(actContents))>131072 error("Target content exceeds inspection response capacity"))
        actState=if(or(actCv~>instances actCv~>shapes actCv~>terminals actCv~>nets) "nonempty" "empty"))
      actWritable=and(ddIsObjWritable(ddGetObj(car(actTarget)))
        or(!apply('ddGetObj actTarget) ddIsObjWritable(apply('ddGetObj actTarget)))
        or(!actCv actOwn actCv~>mode!="r"))
      actFacts=list(actState actSnapshot actPath actWindow
        when(and(actCv !actOwn) actCv) actModified
        when(actCv list(length(actCv~>instances) length(actCv~>shapes) length(actCv~>terminals)))
        actContents actWritable))
      when(and(actCv actOwn) dbClose(actCv)))
    return(actFacts)))
procedure(aiCrInspectTarget(aitTask aitId aitTarget)
  prog((aitRef aitRecord aitFacts aitResult)
    aitResult=errset(progn(
      unless(aiCircuitTasks[aitTask] error("Confirmed circuit task required"))
      aitRef=strcat("circuit-target:" aitId) aitFacts=aiCrTargetFacts(aitTarget)
      aitRecord=aiCrTargets[aitRef]
      if(aitRecord then
        unless(and(aitRecord['task]==aitTask equal(aitRecord['target] aitTarget)
          equal(aitRecord['facts] aitFacts)) error("Target changed; inspect with a new request"))
      else
        when(length(aiCrTargets)>=256 error("Target observation limit"))
        aitRecord=makeTable("aiCrTarget" nil) aitRecord['task]=aitTask
        aitRecord['target]=aitTarget aitRecord['facts]=aitFacts
        aiCrTargets[aitRef]=aitRecord)
      t) t)
    unless(aitResult return(aiCircuitError("target_inspection_failed" aiCrErrorText())))
    return(aiCircuitObject(list(list("ok" t) list("target_ref" aitRef)
      list("target" aitTarget) list("content_state" car(aitFacts))
      list("counts" nth(6 aitFacts)) list("modified_in_session" aiCircuitBool(nth(5 aitFacts)))
      list("contents" nth(7 aitFacts)) list("writable" aiCircuitBool(nth(8 aitFacts)))
      list("next_action" if(car(aitFacts)=="empty" "reuse_empty" "prepare_circuit_creation"))
      list("read_only" t))))))
procedure(aiCrTargetCheck(atcEntry)
  prog((atcRecord atcAction)
    atcRecord=atcEntry['targetRecord] atcAction=atcEntry['targetAction]
    unless(and(atcRecord !atcRecord['consumed] atcRecord['task]==atcEntry['task]
      equal(atcRecord['target] car(atcEntry['payload]))
      equal(atcRecord['facts] aiCrTargetFacts(atcRecord['target])))
      error("Inspected target changed or was already used; inspect again"))
    unless(nth(8 atcRecord['facts]) error("Inspected view is not writable"))
    unless(or(and(atcAction=="reuse_empty" car(atcRecord['facts])=="empty")
      and(member(atcAction list("continue" "replace" "edit")) car(atcRecord['facts])=="nonempty"))
      error("Target action does not match its observed content"))
    return(t)))

procedure(aiCrBoxesOverlap(aboA aboB)
  and(aboA aboB xCoord(car(aboA))<=xCoord(cadr(aboB))+0.0625
    yCoord(car(aboA))<=yCoord(cadr(aboB))+0.0625
    xCoord(cadr(aboA))>=xCoord(car(aboB))-0.0625
    yCoord(cadr(aboA))>=yCoord(car(aboB))-0.0625))
procedure(aiCrAdditionBoxCheck(aabEntry aabBox)
  when(aabEntry['targetAction]=="continue"
    let((aabContents)
      aabContents=nth(7 aabEntry['targetRecord]['facts])
      when(or(exists(aabOld car(aabContents) aiCrBoxesOverlap(aabBox nth(4 aabOld)))
        exists(aabOld caddr(aabContents) aiCrBoxesOverlap(aabBox caddr(aabOld))))
        error("New geometry overlaps existing content; use inspected bounds to revise placement")))) t)

procedure(aiCrRouteCheckPoints(arcEntry arcWire)
  prog((arcRoute arcRow arcAnchors arcEscape arcPoints arcLength)
    arcRoute=nth(3 arcWire)
    unless(and(length(arcRoute)==6 numberp(nth(4 arcRoute)) nth(4 arcRoute)>0)
      error("Deferred route requires two endpoints and a positive fallback length"))
    arcLength=nth(4 arcRoute)
    foreach(arcIndex list(0 1)
      arcEscape=nth(arcIndex+2 arcRoute)
      unless(member(arcEscape list(list(1 0) list(-1 0) list(0 1) list(0 -1)))
        error("Deferred route requires an axis-aligned escape"))
      let((arcRef)
        arcRef=nth(arcIndex arcRoute)
        cond(
          (and(car(arcRef)=="port" length(arcRef)==2)
            arcRow=assoc(cadr(arcRef) caddr(arcEntry['payload]))
            unless(and(arcRow equal(car(arcRow) car(arcWire)))
              error("Deferred route port/net differs from plan"))
            arcAnchors=list(caddr(arcRow)))
          (and(car(arcRef)=="inst" length(arcRef)==3)
            arcRow=assoc(cadr(arcRef) cadr(arcEntry['payload]))
            unless(and(arcRow equal(cadr(assoc(caddr(arcRef) nth(6 arcRow))) car(arcWire)))
              error("Deferred route instance/terminal/net differs from plan"))
            arcAnchors=cadr(assoc(caddr(arcRef) nth(9 arcRow)))
            unless(arcAnchors error("Deferred route terminal has no planned anchors"))
            arcAnchors=foreach(mapcar arcPoint arcAnchors
              dbTransformPoint(arcPoint list(nth(3 arcRow) nth(4 arcRow) 1.0))))
          (t error("Invalid deferred route endpoint"))))
      foreach(arcPoint arcAnchors
        arcPoints=cons(arcPoint cons(list(car(arcPoint)+car(arcEscape)*arcLength
          cadr(arcPoint)+cadr(arcEscape)*arcLength) arcPoints))))
    return(arcPoints)))

procedure(aiCrAdditionCheck(aacEntry)
  prog((aacContents aacBoxes aacX aacY aacPoints)
    unless(aacEntry['targetAction]=="continue" return(t))
    aacContents=nth(7 aacEntry['targetRecord]['facts])
    foreach(aacRow cadr(aacEntry['payload])
      when(assoc(car(aacRow) car(aacContents)) error("New instance name conflicts with existing design"))
      aacBoxes=cons(dbTransformBBox(nth(8 aacRow) list(nth(3 aacRow) nth(4 aacRow) 1.0)) aacBoxes))
    foreach(aacPort caddr(aacEntry['payload])
      when(assoc(car(aacPort) cadr(aacContents)) error("New port conflicts with existing design"))
      aacX=xCoord(caddr(aacPort)) aacY=yCoord(caddr(aacPort))
      aacBoxes=cons(list(list(aacX-0.25 aacY-0.25) list(aacX+0.25 aacY+0.25)) aacBoxes))
    foreach(aacWire nth(3 aacEntry['payload])
      ; Deferred routes have no drawn points yet. Check their retained endpoints
      ; and fallback extents now; build also checks the actual router segments.
      aacPoints=if(nth(3 aacWire) aiCrRouteCheckPoints(aacEntry aacWire) cadr(aacWire))
      unless(length(aacPoints)>=2 error("Wire requires at least two planned points"))
      aacX=mapcar('car aacPoints) aacY=mapcar('cadr aacPoints)
      aacBoxes=cons(list(list(apply('min aacX) apply('min aacY))
        list(apply('max aacX) apply('max aacY))) aacBoxes))
    foreach(aacNote nth(4 aacEntry['payload]) aacBoxes=cons(cadr(aacNote) aacBoxes))
    foreach(aacBox aacBoxes aiCrAdditionBoxCheck(aacEntry aacBox))
    return(t)))

procedure(aiCrWorkPath(accEntry)
  prog((accTarget accLib accPath)
    accTarget=car(accEntry['payload]) accLib=ddGetObj(car(accTarget))
    unless(and(accLib ddIsObjWritable(accLib)) error("writable work library required"))
    accPath=simplifyFilename(ddGetObjReadPath(accLib))
    unless(member(aiCircuitTasks[accEntry['task]] list("foreground" "background")) error("task missing"))
    unless(aiCircuitTasks[accEntry['task]]==accEntry['mode] error("task mode changed"))
    when(and(accEntry['mode]=="foreground" !aiCircuitGuiP()) error("foreground GUI unavailable"))
    when(and(accEntry['workPath] accPath!=accEntry['workPath]) error("work library path changed"))
    foreach(accRow cadr(accEntry['payload])
      when(and(accPath==simplifyFilename(nth(2 accRow))
        or(nth(10 accRow)!="design" cadr(accTarget)==cadr(cadr(accRow))))
        error("target aliases a device library or source design cell")))
    return(accPath)))

procedure(aiCrDependencies(accEntry)
  prog((accMaster accRows accPins accCdf accName accLib accTarget)
    unwindProtect(progn(
      foreach(accRow cadr(accEntry['payload])
        accTarget=cadr(accRow) accLib=ddGetObj(car(accTarget))
        when(and(nth(10 accRow)=="design" caddr(accTarget)!="symbol")
          error("design instances require symbol view; create and verify the missing symbol first; never copy schematic into TB"))
        unless(and(accLib simplifyFilename(ddGetObjReadPath(accLib))==simplifyFilename(nth(2 accRow)))
          error("selected library binding path changed"))
        accMaster=dbOpenCellViewByType(car(accTarget) cadr(accTarget) caddr(accTarget) "" "r")
        unless(accMaster error("selected symbol missing"))
        accRows=cons(aiCrMasterSignature(accMaster accRow) accRows)
        dbClose(accMaster) accMaster=nil)
      foreach(accPort caddr(accEntry['payload])
        accName=case(cadr(accPort) ("input" "ipin") ("output" "opin") ("inputOutput" "iopin"))
        accMaster=dbOpenCellViewByType("basic" accName "symbol" "" "r")
        unless(and(accMaster !dbIsCellViewModified(accMaster)
          !accMaster~>superMaster !accMaster~>isParamCell !accMaster~>instances)
          error("standard pin master unavailable/modified/nonstatic"))
        accCdf=cdfGetCellCDF(ddGetObj("basic" accName)) aiCrSafeCdf(accCdf)
        accPins=cons(list(accName ddGetObjReadPath(ddGetObj("basic")) accMaster~>bBox
          aiCrCdfRows(accCdf) aiCrShapeRows(accMaster)
          foreach(mapcar accTerm accMaster~>terminals
            list(accTerm~>name accTerm~>direction foreach(mapcar accPin accTerm~>pins
              list(accPin~>fig~>objType accPin~>fig~>bBox accPin~>fig~>points))))) accPins)
        dbClose(accMaster) accMaster=nil)
      t) when(accMaster dbClose(accMaster)))
    return(list(reverse(accRows) accPins))))

; Creator-only read supplement. PDK discovery intentionally does not collect body outlines.
; One raw observation table serves every presentation: aiCrBindingGeometry below and
; the generic read-only inspector. Failure raises; callers own their JSON contract.
procedure(aiCrBodyData(acbLib acbCell acbView acbPath)
  prog((acbCv acbPins acbPoints acbTerms acbBox acbSig acbData acbRead acbError)
    acbRead=errset(progn(
      unless(equal(simplifyFilename(ddGetObjReadPath(ddGetObj(acbLib))) simplifyFilename(acbPath))
        error("library binding changed"))
      acbCv=dbOpenCellViewByType(acbLib acbCell acbView "" "r")
      unless(and(acbCv acbCv~>cellViewType=="schematicSymbol" !dbIsCellViewModified(acbCv)
        !acbCv~>superMaster !acbCv~>isParamCell !acbCv~>instances !acbCv~>mosaics)
        error("saved static flat symbol required"))
      foreach(acbTerm acbCv~>terminals
        acbTerms=cons(list(acbTerm~>name aiCrCenters(acbCv acbTerm~>name)) acbTerms)
        acbPoints=append(aiCrCenters(acbCv acbTerm~>name) acbPoints)
        foreach(acbPin acbTerm~>pins acbPins=cons(acbPin~>fig acbPins)))
      foreach(acbShape acbCv~>shapes
        unless(or(member(acbShape acbPins) member(acbShape~>objType list("label" "text"))
          equal(acbShape~>lpp list("instance" "drawing")))
          acbPoints=append(acbShape~>bBox acbPoints)))
      unless(acbPoints error("empty symbol geometry"))
      ; car/cadr remain callable after protected context relocation; xCoord/yCoord aliases do not.
      acbBox=list(list(apply('min mapcar('car acbPoints)) apply('min mapcar('cadr acbPoints)))
        list(apply('max mapcar('car acbPoints)) apply('max mapcar('cadr acbPoints))))
      acbSig=aiCrValueText(aiCrShapeRows(acbCv))
      when(strlen(acbSig)>48000 error("body signature exceeds adapter budget"))
      acbData=makeTable("aiCrBody" nil)
      acbData['occupied_bbox]=acbBox
      ; This reserves unexpanded master text space; it is not measured CDF rendering.
      acbData['annotation_bbox]=acbCv~>bBox
      acbData['pin_centers]=reverse(acbTerms)
      acbData['signature]=acbSig
      t) t)
    ; dbClose/autoload may overwrite errset.errset; retain the reader's cause first.
    unless(acbRead acbError=aiCrErrorText())
    when(acbCv dbClose(acbCv))
    unless(acbRead error(or(acbError "body observation unavailable")))
    return(acbData)))

; Binding-path presentation of the shared body observation.
procedure(aiCrBindingGeometry(acbLib acbCell acbView acbPath)
  prog((acbRead acbData)
    acbRead=errset(aiCrBodyData(acbLib acbCell acbView acbPath) nil)
    unless(acbRead
      return(aiCircuitError("pdk_body_unavailable"
        or(aiCrErrorText() "body observation unavailable"))))
    acbData=car(acbRead)
    return(aiCircuitObject(list(list("ok" t)
      list("occupied_bbox" acbData['occupied_bbox])
      list("annotation_bbox" acbData['annotation_bbox])
      list("pin_centers" acbData['pin_centers]) list("signature" acbData['signature]))))))
