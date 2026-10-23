; Bounded annotation placement. Never alter wires, device parameters or shared masters.
; Instance bboxes reserve conservative space only: IL/NLP text is NOT measured by them.
procedure(aiCrTextOverlap(actA actB @optional (actGap 0.0))
  and(car(car(actA))<car(cadr(actB))+actGap-1e-8
    car(cadr(actA))>car(car(actB))-actGap+1e-8
    cadr(car(actA))<cadr(cadr(actB))+actGap-1e-8
    cadr(cadr(actA))>cadr(car(actB))-actGap+1e-8))

procedure(aiCrTextCost(actCv actLabel actIgnored)
  prog((actBox actBody actStatic actProxy)
    actBox=actLabel~>bBox actBody=0 actStatic=0 actProxy=0
    foreach(actInst actCv~>instances
      when(aiCrTextOverlap(actBox actInst~>bBox 0.015625)
        actProxy=actProxy+1
        foreach(actPart actInst~>master~>shapes
          when(and(!member(actPart~>objType list("label" "textDisplay"))
              !equal(actPart~>lpp list("instance" "drawing"))
              aiCrTextOverlap(actBox dbTransformBBox(actPart~>bBox actInst~>transform) 0.015625))
            actBody=actBody+1))))
    foreach(actShape actCv~>shapes
      unless(or(actShape==actLabel member(actShape actIgnored)
        !member(actShape~>objType list("label" "line" "textDisplay")))
        when(and(or(actShape~>objType!="label" actShape~>isVisible)
          aiCrTextOverlap(actBox actShape~>bBox)) actStatic=actStatic+1)))
    return(list(actBody actStatic actProxy))))

procedure(aiCrTextClear(actCv actLabel actIgnored)
  equal(aiCrTextCost(actCv actLabel actIgnored) list(0 0 0)))

procedure(aiCrTextBetter(actLeft actRight)
  or(car(actLeft)<car(actRight)
    and(car(actLeft)==car(actRight)
      or(cadr(actLeft)<cadr(actRight)
        and(cadr(actLeft)==cadr(actRight) caddr(actLeft)<caddr(actRight))))))

procedure(aiCrTextWire(actCv actEntry actWire actPoint actNet)
  let((actLabel)
    actLabel=schCreateWireLabel(actCv actWire actPoint actNet "lowerLeft" "R0" "stick" 0.0625 nil)
    unless(actLabel error("wire label creation failed"))
    actEntry['textLabels]=cons(actLabel actEntry['textLabels])
    actLabel))

procedure(aiCrTextPoints(actLabel)
  prog((actWire actA actB actLength actPoints actCount actAxis actDirection actPoint)
    actWire=actLabel~>parent
    unless(and(actWire actWire~>objType=="line" length(actWire~>points)==2) return(nil))
    actA=car(actWire~>points) actB=cadr(actWire~>points)
    unless(or(car(actA)==car(actB) cadr(actA)==cadr(actB)) return(nil))
    actAxis=if(car(actA)==car(actB) 1 0)
    actLength=abs(nth(actAxis actB)-nth(actAxis actA))
    actDirection=if(nth(actAxis actB)>nth(actAxis actA) 1 -1)
    actPoints=list(actLabel~>xy actB actA)
    actCount=min(64 fix(actLength/0.0625))
    for(actIndex 1 actCount
      actPoint=if(actAxis==0
        list(car(actA)+actIndex*0.0625*actDirection cadr(actA))
        list(car(actA) cadr(actA)+actIndex*0.0625*actDirection))
      unless(member(actPoint actPoints) actPoints=append(actPoints list(actPoint))))
    return(actPoints)))

procedure(aiCrTextPlace(actCv actLabel)
  prog((actOrigin actJust actPoints actBest actCost)
    actOrigin=actLabel~>xy actJust=actLabel~>justify
    actBest=aiCrTextCost(actCv actLabel list(actLabel~>parent))
    actPoints=aiCrTextPoints(actLabel)
    foreach(actPoint actPoints
      foreach(actAlign list("lowerLeft" "lowerRight" "upperLeft" "upperRight")
        actLabel~>xy=actPoint actLabel~>justify=actAlign
        actCost=aiCrTextCost(actCv actLabel list(actLabel~>parent))
        when(equal(actCost list(0 0 0)) return(t))
        ; Measured overlap takes priority over an unexpanded instance bbox proxy.
        ; Never hide a label or claim that a remaining proxy is verified text.
        when(aiCrTextBetter(actCost actBest)
          actBest=actCost actOrigin=actPoint actJust=actAlign)))
    actLabel~>xy=actOrigin actLabel~>justify=actJust
    return(nil)))

procedure(aiCrTextNote(actCv actEntry actNote)
  prog((actBox actLabel actPoint actOrigin actClear actWidthOk)
    actBox=cadr(actNote)
    unless(schCreateNoteShape(actCv "rectangle" "solid" actBox) error("note box failed"))
    actOrigin=list(car(car(actBox)) cadr(cadr(actBox))+0.25)
    actLabel=schCreateNoteLabel(actCv actOrigin car(actNote)
      "lowerLeft" "R0" "stick" 0.1 "normalLabel")
    unless(actLabel error("note text failed"))
    actWidthOk=car(cadr(actLabel~>bBox))<=car(cadr(actBox))+1e-8
    ; Keep the note beside its group; do not enlarge the group over other devices.
    for(actIndex 0 64
      unless(actClear
        actPoint=list(car(actOrigin) cadr(actOrigin)+actIndex*0.0625)
        actLabel~>xy=actPoint
        actClear=aiCrTextClear(actCv actLabel nil)))
    unless(actClear actLabel~>xy=actOrigin)
    actEntry['textNotes]=cons(list(car(actNote) actLabel~>bBox actBox
      aiCircuitBool(actWidthOk) aiCircuitBool(actClear)) actEntry['textNotes])
    when(or(!actClear !actWidthOk)
      actEntry['textIssues]=cons(list("note_text_conflict" car(actNote) actLabel~>bBox)
        actEntry['textIssues]))
    return(actLabel)))

procedure(aiCrTextFinish(actCv actEntry)
  prog((actLabels actInitial actMoves actOrigin actJust actDynamic actExamples actIssues actCost actObservations)
    actLabels=reverse(actEntry['textLabels]) actInitial=0 actDynamic=0
    foreach(actLabel actLabels
      unless(aiCrTextClear(actCv actLabel list(actLabel~>parent)) actInitial=actInitial+1))
    foreach(actLabel actLabels
      actOrigin=actLabel~>xy actJust=actLabel~>justify
      unless(aiCrTextClear(actCv actLabel list(actLabel~>parent))
        aiCrTextPlace(actCv actLabel))
      unless(and(equal(actOrigin actLabel~>xy) actJust==actLabel~>justify)
        actMoves=cons(list(actLabel~>theLabel actOrigin actLabel~>xy actLabel~>justify
          actLabel~>bBox actLabel~>parent~>points) actMoves)))
    actIssues=actEntry['textIssues]
    foreach(actLabel actLabels
      actCost=aiCrTextCost(actCv actLabel list(actLabel~>parent))
      unless(equal(actCost list(0 0 0))
        if(car(actCost)+cadr(actCost)>0 then
          when(length(actIssues)<128
            actIssues=cons(list("wire_label_conflict"
              actLabel~>theLabel actLabel~>bBox actCost) actIssues))
        else
          when(length(actObservations)<128
            actObservations=cons(list("instance_text_extent_unverified"
              actLabel~>theLabel actLabel~>bBox actCost) actObservations)))))
    foreach(actInst actCv~>instances
      foreach(actShape actInst~>master~>shapes
        when(and(actShape~>objType=="label"
            member(actShape~>labelType list("ILLabel" "NLPLabel")))
          actDynamic=actDynamic+1
          when(length(actExamples)<16
            actExamples=cons(list(actInst~>name actShape~>theLabel) actExamples)))))
    actEntry['textLayout]=list("cad.schematic.text-layout.v1"
      if(actIssues "needs_visual_review" "static_text_checked")
      length(actLabels) reverse(actMoves) actInitial reverse(actIssues)
      reverse(actEntry['textNotes]) actDynamic reverse(actExamples) 'jsonFalse t
      reverse(actObservations) "cadence_expressions_not_blocking")
    return(t)))
