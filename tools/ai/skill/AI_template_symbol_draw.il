; Template-outline renderer. Only the NEW, owned symbol transaction calls this.
; schCreateSymbolShape lacks a documented arc point convention; dbCreateArc is
; the public bbox-based fallback, checked against captured/native bounds.
procedure(aiTdPreflight(adpEntry)
  prog((adpOutline adpAnnotation)
    adpOutline=adpEntry['outline]
    unless(and(length(adpOutline)>=5 length(adpOutline)<=6 car(adpOutline)
      length(car(adpOutline))+length(cadr(adpOutline))<=512
      length(nth(3 adpOutline))==length(adpEntry['rows])
      length(nth(4 adpOutline))==length(adpEntry['rows])
      or(length(adpOutline)==5 length(nth(5 adpOutline))<=64)
      isCallable('schCreateSymbolPinWithLabel) isCallable('dbCreateArc))
      error("invalid/unavailable outline plan"))
    when(length(adpOutline)==6
      foreach(adpAnnotation nth(5 adpOutline)
        unless(and(length(adpAnnotation)==8 stringp(car(adpAnnotation))
          listp(cadr(adpAnnotation)) length(cadr(adpAnnotation))==2
          listp(caddr(adpAnnotation)) length(caddr(adpAnnotation))==2
          stringp(nth(3 adpAnnotation)) stringp(nth(4 adpAnnotation))
          stringp(nth(5 adpAnnotation)) numberp(nth(6 adpAnnotation))
          member(nth(7 adpAnnotation) list("NLPLabel" "normalLabel")))
          error("invalid preserved annotation"))))
    foreach(adpShape append(car(adpOutline) cadr(adpOutline))
      unless(and(length(adpShape)==3 member(car(adpShape) list("line" "polygon" "rectangle" "ellipse" "arc")))
        error("unsupported outline shape")))
    aiTdValidateStubs(cadr(adpOutline) car(adpOutline) adpEntry['rows] 0.000001)
    return(t)))

procedure(aiTdNear(adnA adnB adnTolerance)
  and(abs(xCoord(adnA)-xCoord(adnB))<=adnTolerance abs(yCoord(adnA)-yCoord(adnB))<=adnTolerance))

procedure(aiTdStubLength(adsPoints)
  sqrt(expt(xCoord(car(adsPoints))-xCoord(cadr(adsPoints)) 2)+
    expt(yCoord(car(adsPoints))-yCoord(cadr(adsPoints)) 2)))

procedure(aiTdContactOnBody(acoPoint acoBody acoTolerance)
  prog((acoKind acoPts acoExtra acoNext acoFirst acoLast acoDx acoDy acoD2 acoT
    acoBox acoCx acoCy acoRx acoRy acoAngle acoStart acoStop acoTau acoHit acoDist)
    acoTau=2*acos(-1.0)
    foreach(acoRow acoBody
      acoKind=car(acoRow) acoPts=cadr(acoRow) acoExtra=caddr(acoRow)
      when(acoKind=="rectangle"
        acoFirst=car(acoPts) acoLast=cadr(acoPts)
        acoPts=list(acoFirst list(xCoord(acoLast) yCoord(acoFirst))
          acoLast list(xCoord(acoFirst) yCoord(acoLast))) acoKind="polygon")
      when(member(acoKind list("line" "polygon"))
        acoNext=cdr(acoPts)
        when(acoKind=="polygon" acoNext=append(acoNext list(car(acoPts))))
        foreach((acoFirst acoLast) acoPts acoNext
          acoDx=xCoord(acoLast)-xCoord(acoFirst) acoDy=yCoord(acoLast)-yCoord(acoFirst)
          acoD2=acoDx*acoDx+acoDy*acoDy
          when(acoD2>1e-16
            acoT=max(0.0 min(1.0 ((xCoord(acoPoint)-xCoord(acoFirst))*acoDx+
              (yCoord(acoPoint)-yCoord(acoFirst))*acoDy)/acoD2))
            acoHit=list(xCoord(acoFirst)+acoT*acoDx yCoord(acoFirst)+acoT*acoDy)
            when(aiTdStubLength(list(acoHit acoPoint))<=acoTolerance return(t)))))
      when(member(acoKind list("ellipse" "arc"))
        acoBox=if(acoKind=="ellipse" acoPts car(acoExtra))
        acoCx=(xCoord(car(acoBox))+xCoord(cadr(acoBox)))/2.0
        acoCy=(yCoord(car(acoBox))+yCoord(cadr(acoBox)))/2.0
        acoRx=(xCoord(cadr(acoBox))-xCoord(car(acoBox)))/2.0
        acoRy=(yCoord(cadr(acoBox))-yCoord(car(acoBox)))/2.0
        when(and(acoRx>1e-10 acoRy>1e-10)
          acoAngle=atan2((yCoord(acoPoint)-acoCy)/acoRy (xCoord(acoPoint)-acoCx)/acoRx)
          acoHit=list(acoCx+acoRx*cos(acoAngle) acoCy+acoRy*sin(acoAngle))
          acoDist=aiTdStubLength(list(acoHit acoPoint))
          when(acoDist<=acoTolerance
            when(acoKind=="ellipse" return(t))
            acoStart=cadr(acoExtra) acoStop=caddr(acoExtra)
            when(abs(acoStop-acoStart)>=acoTau-1e-8 return(t))
            while(acoStop<acoStart acoStop=acoStop+acoTau)
            while(acoAngle<acoStart-1e-7 acoAngle=acoAngle+acoTau)
            while(acoAngle>acoStart+acoTau acoAngle=acoAngle-acoTau)
            when(acoAngle<=acoStop+1e-7 return(t))))))
    return(nil)))

procedure(aiTdValidateStubs(avsStubs avsBody avsPorts avsTolerance)
  prog((avsPts avsOrigins avsPoint)
    unless(length(avsStubs)==length(avsPorts) error("one stub per terminal required"))
    foreach(avsRow avsStubs
      avsPts=cadr(avsRow)
      unless(and(car(avsRow)=="line" length(avsPts)==2
        aiTdStubLength(avsPts)>1e-8 aiTdStubLength(avsPts)<=0.3125+1e-8
        or(abs(xCoord(car(avsPts))-xCoord(cadr(avsPts)))<1e-8
           abs(yCoord(car(avsPts))-yCoord(cadr(avsPts)))<1e-8)
        aiTdContactOnBody(cadr(avsPts) avsBody avsTolerance))
        error("terminal stub is detached, diagonal, degenerate or longer than five grid steps"))
      when(member(car(avsPts) avsOrigins) error("duplicate terminal stub origin"))
      avsOrigins=cons(car(avsPts) avsOrigins))
    foreach(avsPort avsPorts
      avsPoint=list(nth(5 avsPort) nth(6 avsPort))
      unless(member(avsPoint avsOrigins) error("terminal/stub origin mismatch")))
    return(t)))

procedure(aiTdShape(adsCv adsRow)
  prog((adsShape adsBox adsExpected adsTol)
    adsTol=1.0/adsCv~>DBUPerUU
    if(car(adsRow)=="arc" then
      adsShape=dbCreateArc(adsCv list("device" "drawing") car(nth(2 adsRow)) cadr(adsRow))
      unless(adsShape error("template arc reconstruction failed"))
      adsBox=adsShape~>bBox adsExpected=cadr(adsRow)
      unless(and(aiTdNear(car(adsBox) car(adsExpected) adsTol)
        aiTdNear(cadr(adsBox) cadr(adsExpected) adsTol)) error("arc geometry differs"))
      ; Source bboxes were rounded on the source DBU grid. Restore the documented
      ; writable angles to preserve the actual curve endpoints after scaling.
      adsShape~>startAngle=cadr(nth(2 adsRow))
      adsShape~>stopAngle=caddr(nth(2 adsRow))
      unless(and(abs(adsShape~>startAngle-cadr(nth(2 adsRow)))<0.000001
        abs(adsShape~>stopAngle-caddr(nth(2 adsRow)))<0.000001)
        error("arc angle readback differs"))
    else adsShape=schCreateSymbolShape(adsCv car(adsRow) "outline" cadr(adsRow)))
    unless(adsShape error("template shape creation failed: %s" car(adsRow)))
    return(adsShape)))

procedure(aiTdLabel(adlCv adlPoint adlChoice adlText adlJustify adlType
    @optional adlOrient adlFont adlHeight)
  unless(adlOrient adlOrient="R0")
  unless(adlFont adlFont="stick")
  unless(adlHeight adlHeight=0.0625)
  unless(schCreateSymbolLabel(adlCv adlPoint adlChoice adlText adlJustify adlOrient adlFont adlHeight adlType)
    error("template label failed: %s" adlText)) t)

procedure(aiTdBuild(adbCv adbEntry)
  prog((adbMaster adbPoint adbLabels adbPin adbBefore adbBox adbLo adbHi adbShape
    adbBodyPoints adbActualBox adbNativeBody adbNativeStubs adbTolerance adbAnnotation)
    aiTdPreflight(adbEntry)
    ; The caller has already created an empty symbol cellview. Do not seed a
    ; rectangular bootstrap: its left/right selection-box contract conflicts
    ; with valid top/bottom family slots. The actual outline supplies all
    ; geometry and pins below.
    ; Deleting a pin figure leaves its terminal/net; explicitly clear both.
    ; This is never called on an existing saved or user-modified symbol.
    foreach(adbOldShape adbCv~>shapes dbDeleteObject(adbOldShape))
    foreach(adbOldTerm adbCv~>terminals dbDeleteObject(adbOldTerm))
    foreach(adbOldNet adbCv~>nets dbDeleteObject(adbOldNet))
    unless(and(!adbCv~>shapes !adbCv~>terminals !adbCv~>nets !adbCv~>instances)
      error("generated symbol cleanup incomplete"))
    adbEntry['bootstrapCleaned]=t
    foreach(adbRow car(adbEntry['outline])
      adbShape=aiTdShape(adbCv adbRow)
      adbNativeBody=cons(list(car(adbRow)
        if(member(car(adbRow) list("line" "polygon")) adbShape~>points adbShape~>bBox)
        when(car(adbRow)=="arc" list(adbShape~>ellipseBBox adbShape~>startAngle adbShape~>stopAngle))) adbNativeBody)
      adbBodyPoints=append(adbShape~>bBox adbBodyPoints))
    adbTolerance=1.0/adbCv~>DBUPerUU
    foreach(adbRow cadr(adbEntry['outline])
      adbShape=aiTdShape(adbCv adbRow)
      unless(and(aiTdNear(car(adbShape~>points) car(cadr(adbRow)) adbTolerance)
        aiTdNear(cadr(adbShape~>points) cadr(cadr(adbRow)) adbTolerance))
        error("native terminal stub differs from preview"))
      adbNativeStubs=cons(list("line" adbShape~>points nil) adbNativeStubs))
    aiTdValidateStubs(adbNativeStubs adbNativeBody adbEntry['rows] adbTolerance)
    unwindProtect(progn(
      adbMaster=dbOpenCellViewByType("basic" "sympin" "symbolNN" "" "r")
      unless(and(adbMaster !dbIsCellViewModified(adbMaster)) error("symbol pin master unavailable/modified"))
      foreach(adbPort adbEntry['rows]
        adbPoint=list(nth(5 adbPort) nth(6 adbPort))
        adbLabels=assoc(car(adbPort) nth(4 adbEntry['outline]))
        unless(adbLabels error("pin label plan missing"))
        adbBefore=adbCv~>shapes
        adbPin=schCreateSymbolPinWithLabel(?cellView adbCv ?master adbMaster ?name car(adbPort)
          ?direction cadr(adbPort) ?origin adbPoint ?orient "R0" ?flatten t)
        unless(adbPin error("template terminal creation failed"))
        ; Place the generated name explicitly to keep it clear of its stub.
        foreach(adbNew adbCv~>shapes
          when(and(!member(adbNew adbBefore) adbNew~>objType=="label" equal(adbNew~>lpp list("pin" "label")))
            dbDeleteObject(adbNew)))
        adbBox=adbPin~>bBox
        unless(aiTdNear(list((xCoord(car(adbBox))+xCoord(cadr(adbBox)))/2.0
          (yCoord(car(adbBox))+yCoord(cadr(adbBox)))/2.0) adbPoint 0.000001)
          error("pin origin cannot be represented on target DBU grid"))
        dbFindTermByName(adbCv car(adbPort))~>net~>sigType=nth(4 adbPort)
        adbBodyPoints=cons(adbPoint adbBodyPoints)
        aiTdLabel(adbCv cadr(adbLabels) "pin name" car(adbPort) caddr(adbLabels) "normalLabel")
        aiTdLabel(adbCv nth(3 adbLabels)
          "analog pin annotate" sprintf(nil "cdsTerm(%L)" car(adbPort))
          nth(4 adbLabels) "ILLabel")) t)
      when(adbMaster dbClose(adbMaster)))
    when(length(adbEntry['outline])==6
      foreach(adbAnnotation nth(5 adbEntry['outline])
        adbBodyPoints=append(caddr(adbAnnotation) adbBodyPoints)))
    ; Avoid coordinate alias function values in serialized contexts.
    adbLo=list(apply('min mapcar('car adbBodyPoints)) apply('min mapcar('cadr adbBodyPoints)))
    adbHi=list(apply('max mapcar('car adbBodyPoints)) apply('max mapcar('cadr adbBodyPoints)))
    adbActualBox=list(adbLo adbHi) adbBox=nth(2 adbEntry['outline])
    unless(and(aiTdNear(car(adbBox) adbLo 0.000001) aiTdNear(cadr(adbBox) adbHi 0.000001))
      error("native body/pin selection box differs from preview"))
    adbShape=schCreateInstBox(adbCv adbActualBox)
    unless(and(adbShape equal(adbShape~>bBox adbActualBox)) error("selection box creation/readback failed"))
    unless(dbReplaceProp(adbCv "partName" "string" adbEntry['cell]) error("partName write failed"))
    aiTdLabel(adbCv list(xCoord(adbLo) yCoord(adbHi)+0.125) "analog instance label" "cdsName()" "lowerLeft" "ILLabel")
    aiTdLabel(adbCv list((xCoord(adbLo)+xCoord(adbHi))/2.0 (yCoord(adbLo)+yCoord(adbHi))/2.0)
      "logical label" "[@partName]" "centerCenter" "NLPLabel")
    aiTdLabel(adbCv list(xCoord(adbHi) yCoord(adbHi)+0.125) "device annotate" "[@cellName]" "lowerRight" "NLPLabel")
    aiTdLabel(adbCv list(xCoord(adbLo) yCoord(adbLo)-0.375) "instance label" "[@instanceName]" "upperLeft" "NLPLabel")
    when(length(adbEntry['outline])==6
      foreach(adbAnnotation nth(5 adbEntry['outline])
        aiTdLabel(adbCv cadr(adbAnnotation) "logical label" car(adbAnnotation)
          nth(3 adbAnnotation) nth(7 adbAnnotation) nth(4 adbAnnotation)
          nth(5 adbAnnotation) nth(6 adbAnnotation))))
    aiTsCheckPorts(adbCv adbEntry['rows] t)
    return(t)))

procedure(aiDrawSymbol(addPreview addDigest addRequest)
  prog((addEntry)
    addEntry=aiTemplateSymbolPreviews[addPreview]
    unless(and(addEntry addEntry['digest]==addDigest addEntry['outline])
      return(aiCircuitError("outline_preview_required" "Preview style=template_outline with the exact digest first")))
    return(aiTemplateSymbolCreate(addPreview addDigest addRequest))))
