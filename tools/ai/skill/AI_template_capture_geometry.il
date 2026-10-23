; Capture v2 observations. Reuse the creator's qualified static-master geometry reader.
; No source save, schCheck, CDF evaluation or OA writes.
procedure(aiTplLess(atlLeft atlRight)
  alphalessp(sprintf(nil "%L" atlLeft) sprintf(nil "%L" atlRight)))

; Capture-path reads must come from the saved view.  A library-level super master
; (for example a PDK cell template) can report an edit state before it is
; materialized; open the saved view read-only and judge the opened object.  A
; view that is genuinely modified stays modified after the reopen and is refused.
; Read-only evaluation can also mark a view this capture already accepted dirty
; in memory (PCell layout masters update instance state without touching disk).
; Later re-reads within the same capture tolerate that side effect; the
; before/after dependency rows still guard the saved content.
; A dedicated background collection session has no editors, so its edit flags
; carry no unsaved-work meaning at all; there the saved content is guarded by
; the dependency and native byte checks and the flag never blocks a capture.
procedure(aiTplResetSavedViews()
  aiTplAcceptedViews=makeTable("aiTplAccepted" nil))
unless(boundp('aiTplAcceptedViews) aiTplResetSavedViews())
procedure(aiTplSavedView(asvCv)
  let((asvSaved asvKey)
    asvKey=list(asvCv~>libName asvCv~>cellName asvCv~>viewName)
    if(!dbIsCellViewModified(asvCv)
      then
        aiTplAcceptedViews[asvKey]=t
        asvCv
      else
        if(aiTplAcceptedViews[asvKey]
          then asvCv
          else
            asvSaved=car(errset(dbOpenCellViewByType(asvCv~>libName asvCv~>cellName asvCv~>viewName
              asvCv~>cellViewType "r" nil) nil))
            unless(asvSaved error("Saved source view is unavailable"))
            unless(and(boundp('aiTplIsolatedSession) aiTplIsolatedSession)
              when(dbIsCellViewModified(asvSaved)
                error(strcat("Unsaved source view " asvCv~>libName "/" asvCv~>cellName "/" asvCv~>viewName))))
            aiTplAcceptedViews[asvKey]=t
            asvSaved))))

procedure(aiTplMasterView(atmInstance)
  let((atmMaster)
    atmMaster=atmInstance~>master
    unless(atmMaster error("Template master unavailable"))
    aiTplSavedView(atmMaster)))

procedure(aiTplDependency(atdCv)
  let((atdFile atdRows)
    atdCv=aiTplSavedView(atdCv)
    atdFile=ddGetObj(atdCv~>libName atdCv~>cellName atdCv~>viewName "*")
    if(or(length(atdCv~>shapes)>4096 length(atdCv~>instances)>512)
      then
        ; Over-budget views keep an identity marker instead of detailed rows:
        ; the guard still detects saved changes through counter and timestamp,
        ; and qualification stays unknown instead of failing the whole capture.
        aiTplJson(list("over_budget" atdCv~>libName atdCv~>cellName atdCv~>viewName
          ddGetObjReadPath(ddGetObj(atdCv~>libName)) dbGetCellViewCounter(atdCv)
          when(atdFile ddGetObjLastModify(atdFile))
          length(atdCv~>shapes) length(atdCv~>instances)))
      else
        foreach(atdShape atdCv~>shapes
          atdRows=cons(list("shape" atdShape~>objType atdShape~>lpp atdShape~>bBox
            atdShape~>points atdShape~>theLabel aiTplNet(atdShape)) atdRows))
        foreach(atdInst atdCv~>instances
          atdRows=cons(list("instance" atdInst~>name atdInst~>libName atdInst~>cellName
            atdInst~>viewName atdInst~>xy atdInst~>orient
            sort(foreach(mapcar atdProp atdInst~>prop
              list(atdProp~>name atdProp~>valueType atdProp~>value)) 'aiTplLess)
            sort(foreach(mapcar atdTerm atdInst~>instTerms
              list(atdTerm~>name aiTplNet(atdTerm))) 'aiTplLess)) atdRows))
        foreach(atdTerminal atdCv~>terminals
          atdRows=cons(list("terminal" atdTerminal~>name atdTerminal~>direction
            atdTerminal~>numBits aiTplNet(atdTerminal)
            foreach(mapcar atdPin atdTerminal~>pins
              list(atdPin~>fig~>objType atdPin~>fig~>bBox atdPin~>fig~>points))) atdRows))
        aiTplJson(list(atdCv~>libName atdCv~>cellName atdCv~>viewName
          ddGetObjReadPath(ddGetObj(atdCv~>libName)) dbGetCellViewCounter(atdCv)
          when(atdFile ddGetObjLastModify(atdFile)) sort(atdRows 'aiTplLess))))))

; atdWarm runs the read-only walk without building the JSON evidence: the first
; walk can still read parameterized masters before their lazy evaluation
; completes, so the guard baseline must be taken after one warm walk.
procedure(aiTplDependencies(atdSource @optional (atdWarm nil))
  let((atdResult atdSeen atdKey)
    atdResult=list(aiTplDependency(atdSource)) atdSeen=makeTable("aiTplSeen" nil)
    foreach(atdInstance atdSource~>instances
      atdKey=list(atdInstance~>libName atdInstance~>cellName atdInstance~>viewName)
      unless(atdSeen[atdKey]
        atdResult=cons(aiTplDependency(aiTplMasterView(atdInstance)) atdResult)
        atdSeen[atdKey]=t))
    unless(atdWarm
      atdResult=aiTplJson(sort(atdResult 'aiTplLess))
      when(strlen(atdResult)>8388608 error("Template dependency evidence exceeds 8 MiB")))
    atdResult))

; Facts for v3 direct electrical qualification, not a precomputed success flag.
; The before/after dependency guard also protects these read-only observations.
; An inspected view with no net expression reports an empty list: -nil- would
; conflate "none found" with "not observed", and the reader keeps null unknown.
procedure(aiTplTextMap(attPairs)
  let((attParts)
    attParts=""
    foreach(attPair attPairs
      attParts=strcat(attParts if(attParts=="" "" ",")
        aiTplQuoted(car(attPair)) ":"
          aiTplQuoted(if(stringp(cadr(attPair)) cadr(attPair) sprintf(nil "%L" cadr(attPair))))))
    strcat("{" attParts "}")))

procedure(aiTplElectricalEvidence(ateSource)
  let((ateViews ateRows ateSeen ateKey ateTermExpr ateSigExpr ateHierarchy ateSourceView
       ateTexts ateSigTexts)
    ateViews=cons(ateSource ateSource~>instances~>master)
    ateSeen=makeTable("aiTplElectricalSeen" nil)
    foreach(ateCv ateViews
      unless(ateCv error("Electrical qualification requires every master"))
      ateKey=list(ateCv~>libName ateCv~>cellName ateCv~>viewName)
      unless(ateSeen[ateKey]
        ateTermExpr=nil ateSigExpr=nil ateHierarchy=0 ateTexts=nil ateSigTexts=nil
        ateSourceView=equal(ateCv ateSource)
        when(ateSourceView
          foreach(ateInst ateSource~>instances
            when(and(ateInst~>master
                !equal(ateInst~>master~>cellViewType "schematicSymbol")
                !aiTplGraphicRole(ateInst))
              ateHierarchy=ateHierarchy+1)))
        foreach(ateTerm ateCv~>terminals
          let((ateValue) ateValue=dbGetTermNetExpr(ateTerm)
            when(ateValue
              ateTermExpr=cons(ateTerm~>name ateTermExpr)
              ateTexts=cons(list(ateTerm~>name ateValue) ateTexts))))
        foreach(ateSig ateCv~>signals
          let((ateValue) ateValue=dbGetSigNetExpr(ateSig)
            when(ateValue
              ateSigExpr=cons(ateSig~>name ateSigExpr)
              ateSigTexts=cons(list(ateSig~>name ateValue) ateSigTexts))))
        ateTexts=reverse(ateTexts) ateSigTexts=reverse(ateSigTexts)
        ateRows=cons(strcat("{\"target\":" aiTplJson(ateKey)
          ",\"view_type\":" aiTplJson(ateCv~>cellViewType)
          ",\"source_view\":" aiTplJson(if(ateSourceView "source" "master"))
          ",\"parameterized\":" if(or(ateCv~>superMaster ateCv~>isParamCell) "true" "false")
          ",\"hierarchy_count\":" aiTplJson(ateHierarchy)
          ",\"instance_count\":" aiTplJson(length(ateCv~>instances))
          ",\"mosaic_count\":" aiTplJson(length(ateCv~>mosaics))
          ",\"terminal_expressions\":" if(ateTermExpr aiTplJson(ateTermExpr) "[]")
          ",\"signal_expressions\":" if(ateSigExpr aiTplJson(ateSigExpr) "[]")
          ",\"terminal_expression_texts\":" aiTplTextMap(ateTexts)
          ",\"signal_expression_texts\":" aiTplTextMap(ateSigTexts) "}") ateRows)
        ateSeen[ateKey]=t))
    strcat("{\"schema\":\"cad.template.electrical-observation.v1\",\"views\":["
      buildString(sort(ateRows 'alphalessp) ",") "]}")))

procedure(aiTplCaptureGeometry(atgCv atgPort atgCounts)
  let((atgObservation atgPath atgMaster atgFigure atgPoints atgBox atgCenter atgIndex)
    unless(isCallable('aiCrBindingGeometry) error("Capture v2 requires creator geometry reader"))
    foreach(atgInstance atgCv~>instances
      unless(aiTplGraphicRole(atgInstance)
        atgPath=ddGetObjReadPath(ddGetObj(atgInstance~>libName))
        atgMaster=aiTplMasterView(atgInstance)
        unless(atgMaster error("Template instance master is unavailable"))
        if(null(atgMaster~>terminals)
          then
            ; An observed empty interface: no terminals means no pin inventory exists.
            aiTplRecord(atgPort atgCounts "instance_geometry"
              strcat("\"instance\":" aiTplJson(atgInstance~>name)
                ",\"library_path\":" aiTplJson(atgPath)
                ",\"observation\":{\"ok\":false,\"code\":\"master_terminals_absent\""
                ",\"message\":\"Saved master exposes no terminals; no pin inventory exists\"}"))
          else
            atgObservation=aiCrBindingGeometry(atgInstance~>libName atgInstance~>cellName
              atgInstance~>viewName atgPath)
            aiTplRecord(atgPort atgCounts "instance_geometry"
              strcat("\"instance\":" aiTplJson(atgInstance~>name)
                ",\"library_path\":" aiTplJson(atgPath)
                ",\"observation\":" atgObservation)))))
    foreach(atgTerm atgCv~>terminals
      atgIndex=0
      foreach(atgPin atgTerm~>pins
        atgFigure=atgPin~>fig atgPoints=nil
        when(atgFigure
          if(atgFigure~>objType=="inst" then
            ; Standard schematic ports connect at the instance origin; their arrow
            ; polygon centre is displaced by half a grid and is not the port origin.
            when(equal(aiTplGraphicRole(atgFigure) "port_graphic")
              atgPoints=list(dbTransformPoint(0:0 atgFigure~>transform)))
          else
            when(atgFigure~>objType=="polygon"
              atgBox=atgFigure~>bBox
              unless(and(length(atgFigure~>points)==4
                forall(atgVertex list(car(atgBox) cadr(atgBox)
                  list(caar(atgBox) cadadr(atgBox)) list(caadr(atgBox) cadar(atgBox)))
                  member(atgVertex atgFigure~>points)))
                error("Capture v2 cannot prove a non-rectangular port pin anchor")))
            when(member(atgFigure~>objType list("rect" "polygon"))
              atgBox=atgFigure~>bBox
              atgPoints=list(list((caar(atgBox)+caadr(atgBox))/2.0
                (cadar(atgBox)+cadadr(atgBox))/2.0)))))
        foreach(atgCenter atgPoints
          aiTplRecord(atgPort atgCounts "port_anchor"
            strcat("\"terminal\":" aiTplJson(atgTerm~>name)
              ",\"anchor_id\":" aiTplJson(sprintf(nil "a%d" atgIndex))
              ",\"xy\":" aiTplJson(atgCenter)))
          atgIndex=atgIndex+1)))))
