; Read-only current-cellview selection snapshots for SiCo object pickers.
; This file never changes focus, selection, zoom, design data or the task target.
unless(boundp('aiSelectionHighlights)
  aiSelectionHighlights=makeTable("aiSelectionHighlights" nil))
unless(and(boundp('aiSelectionVersion)
           aiSelectionVersion=="20261021.ai.selection.v2")

procedure(aiSelectionRevision() "20261021.ai.selection.v2")

procedure(aiSelectionText(asxValue)
  if(not(and(stringp(asxValue) strlen(asxValue)>0 strlen(asxValue)<=512)) nil
    let((asxIndex asxCode asxOk)
      asxIndex=1 asxCode=nil asxOk=t
      while(and(asxOk asxIndex<=strlen(asxValue))
        asxCode=charToInt(concat(substring(asxValue asxIndex 1)))
        when(or(asxCode<32 asxCode==127) asxOk=nil)
        asxIndex=asxIndex+1)
      if(asxOk asxValue nil))))

procedure(aiSelectionTarget(asxCv)
  when(asxCv
    let((asxLib asxCell asxView asxType asxPath)
      asxLib=aiSelectionText(car(errset(asxCv~>libName nil)))
      asxCell=aiSelectionText(car(errset(asxCv~>cellName nil)))
      asxView=aiSelectionText(car(errset(asxCv~>viewName nil)))
      asxType=aiSelectionText(car(errset(asxCv~>cellViewType nil)))
      when(and(asxLib asxCell asxView asxType)
        asxPath=car(errset(ddGetObjReadPath(ddGetObj(asxLib)) nil))
        aiPdkObj(list(
          list("library" asxLib)
          list("cell" asxCell)
          list("view" asxView)
          list("view_type" asxType)
          list("library_path" when(and(stringp(asxPath) strlen(asxPath)>0) asxPath))))))))

procedure(aiSelectionMaster(asxObj)
  when(asxObj
    let((asxMaster asxLib asxCell asxView)
      asxMaster=car(errset(asxObj~>master nil))
      when(asxMaster
        asxLib=aiSelectionText(car(errset(asxMaster~>libName nil)))
        asxCell=aiSelectionText(car(errset(asxMaster~>cellName nil)))
        asxView=aiSelectionText(car(errset(asxMaster~>viewName nil)))
        when(and(asxLib asxCell asxView)
          aiPdkObj(list(
            list("library" asxLib)
            list("cell" asxCell)
            list("view" asxView))))))))

procedure(aiSelectionInstance(asxObj)
  let((asxType asxName asxMaster asxBox)
    asxType=car(errset(asxObj~>objType nil))
    when(or(asxType=="instance" asxType=="inst" asxType=='inst)
      asxName=aiSelectionText(car(errset(asxObj~>name nil)))
      when(asxName
        asxMaster=aiSelectionMaster(asxObj)
        when(asxMaster
          asxBox=aiWindowBBox(car(errset(asxObj~>bBox nil)))
          aiPdkObj(list(
            list("kind" "instance")
            list("name" asxName)
            list("master" asxMaster)
            list("hierarchy" aiPdkArray(nil))
            list("bbox" asxBox))))))))

procedure(aiSelectionNet(asxObj)
  let((asxType asxNet asxName asxBox)
    asxType=car(errset(asxObj~>objType nil))
    asxNet=car(errset(asxObj~>net nil))
    asxName=nil
    when(asxNet asxName=aiSelectionText(car(errset(asxNet~>name nil))))
    when(and(!asxName asxType=="net")
      asxName=aiSelectionText(car(errset(asxObj~>name nil))))
    when(asxName
      asxBox=aiWindowBBox(car(errset(asxObj~>bBox nil)))
      aiPdkObj(list(
        list("kind" "net")
        list("name" asxName)
        list("hierarchy" aiPdkArray(nil))
        list("bbox" asxBox))))))

procedure(aiSelectionTerminal(asxObj)
  let((asxType asxName asxDirection asxBox)
    asxType=car(errset(asxObj~>objType nil))
    asxDirection=car(errset(asxObj~>direction nil))
    when(or(asxType=="terminal"
            asxType=="term"
            and(asxDirection
                or(stringp(asxDirection) symbolp(asxDirection))))
      asxName=aiSelectionText(car(errset(asxObj~>name nil)))
      when(asxName
        asxBox=aiWindowBBox(car(errset(asxObj~>bBox nil)))
        aiPdkObj(list(
          list("kind" "terminal")
          list("name" asxName)
          list("hierarchy" aiPdkArray(nil))
          list("bbox" asxBox)))))))

procedure(aiSelectionItem(asxObj)
  or(aiSelectionInstance(asxObj)
     aiSelectionTerminal(asxObj)
     aiSelectionNet(asxObj)))

procedure(aiSelectionNetObject(asnNet)
  let((asnName asnFigures asnFigure asnBox)
    when(asnNet
      asnName=aiSelectionText(car(errset(asnNet~>name nil)))
      when(asnName
        asnFigures=car(errset(asnNet~>figs nil))
        foreach(asnFigure asnFigures
          when(asnFigure
            asnBox=aiSelectionUnionBox(asnBox
              car(errset(asnFigure~>bBox nil)))))
        aiPdkObj(list(
          list("kind" "net")
          list("name" asnName)
          list("hierarchy" aiPdkArray(nil))
          list("bbox" asnBox)))))))

procedure(aiSelectionShapeName(ashShape)
  let((ashType ashLpp ashBox ashName)
    ashType=car(errset(ashShape~>objType nil))
    ashLpp=car(errset(ashShape~>lpp nil))
    ashBox=aiWindowBBox(car(errset(ashShape~>bBox nil)))
    when(and(stringp(ashType) ashBox)
      ashName=sprintf(nil "shape:%s:%s:%s:%s"
        ashType
        if(ashLpp aiJsonEscape(car(ashLpp)) "")
        if(ashLpp aiJsonEscape(cadr(ashLpp)) "")
        aiPdkJson(ashBox))
      when(and(strlen(ashName)<=512 aiSelectionText(ashName)) ashName))))

procedure(aiSelectionShape(ashShape)
  let((ashName ashBox)
    ashName=aiSelectionShapeName(ashShape)
    when(ashName
      ashBox=aiWindowBBox(car(errset(ashShape~>bBox nil)))
      aiPdkObj(list(
        list("kind" "shape")
        list("name" ashName)
        list("hierarchy" aiPdkArray(nil))
        list("bbox" ashBox))))))

procedure(aiSelectionItemName(aslItem)
  when(aslItem
    let((aslPairs)
      aslPairs=cdr(aslItem)
      cadr(assoc("name" aslPairs)))))

procedure(aiSelectionItemMasterText(aslItem)
  let((aslPairs aslMaster aslFields)
    when(aslItem
      aslPairs=cdr(aslItem)
      aslMaster=cadr(assoc("master" aslPairs))
      when(aslMaster
        aslFields=cdr(aslMaster)
        strcat(cadr(assoc("library" aslFields)) "/"
          cadr(assoc("cell" aslFields)) "/" cadr(assoc("view" aslFields)))))))

procedure(aiSelectionMatches(aslItem aslQuery)
  let((aslName aslMaster)
    aslName=or(aiSelectionItemName(aslItem) "")
    aslMaster=or(aiSelectionItemMasterText(aslItem) "")
    or(aslQuery==""
       index(lowerCase(aslName) lowerCase(aslQuery))
       index(lowerCase(aslMaster) lowerCase(aslQuery)))))

procedure(aiSelectionList(aslRef aslKind aslQuery aslOffset aslLimit)
  prog((aslState aslWindow aslCv aslTarget aslItems aslCount aslCandidate
        aslResult aslSelected aslTruncated aslPage aslRemaining aslPageCount
        aslItem aslName)
    unless(and(fixp(aslOffset) aslOffset>=0 aslOffset<=4096
               fixp(aslLimit) aslLimit>=1 aslLimit<=50
               member(aslKind list("all" "instance" "net" "terminal" "shape")))
      return(aiPdkError("invalid_arguments" "Invalid object query arguments")))
    unless(and(stringp(aslQuery) strlen(aslQuery)<=128)
      return(aiPdkError("invalid_arguments" "Invalid object query")))
    aslState=aiWindowResolveState(aslRef)
    aslWindow=cadr(aslState)
    unless(aslWindow return(aiWindowStateError(aslState)))
    aslCv=car(errset(geGetEditCellView(aslWindow) nil))
    unless(aslCv return(aiPdkError("read_failed" "The edit cellview is unavailable")))
    aslTarget=aiSelectionTarget(aslCv)
    unless(aslTarget return(aiPdkError("read_failed" "Invalid cellview target")))
    aslItems=nil aslCount=0
    when(member(aslKind list("all" "instance"))
      foreach(aslCandidate aslCv~>instances
        when(and(aslCount<4096
                 (aslItem=aiSelectionInstance(aslCandidate))
                 aiSelectionMatches(aslItem aslQuery))
          aslItems=cons(aslItem aslItems)
          aslCount=aslCount+1)))
    when(member(aslKind list("all" "net"))
      foreach(aslCandidate aslCv~>nets
        when(and(aslCount<4096
                 (aslItem=aiSelectionNetObject(aslCandidate))
                 aiSelectionMatches(aslItem aslQuery))
          aslItems=cons(aslItem aslItems)
          aslCount=aslCount+1)))
    when(member(aslKind list("all" "terminal"))
      foreach(aslCandidate aslCv~>terminals
        when(and(aslCount<4096
                 (aslItem=aiSelectionTerminal(aslCandidate))
                 aiSelectionMatches(aslItem aslQuery))
          aslItems=cons(aslItem aslItems)
          aslCount=aslCount+1)))
    when(member(aslKind list("all" "shape"))
      foreach(aslCandidate aslCv~>shapes
        when(and(aslCount<4096
                 (aslItem=aiSelectionShape(aslCandidate))
                 aiSelectionMatches(aslItem aslQuery))
          aslItems=cons(aslItem aslItems)
          aslCount=aslCount+1)))
    aslItems=reverse(aslItems)
    aslTruncated=aslCount>=4096
    when(length(aslItems) > aslOffset+aslLimit aslTruncated=t)
    aslRemaining=nthcdr(min(aslOffset length(aslItems)) aslItems)
    aslPage=nil aslPageCount=0
    while(and(aslRemaining aslPageCount<aslLimit)
      aslPage=cons(car(aslRemaining) aslPage)
      aslPageCount=aslPageCount+1
      aslRemaining=cdr(aslRemaining))
    aslPage=reverse(aslPage)
    aslSelected=errset(geGetSelSet(aslWindow) nil)
    aslResult=aiPdkJson(aiPdkObj(list(
      list("ok" t)
      list("valid" t)
      list("schema" "cad.cv.objects.v1")
      list("session_ref" aiWindowEpoch)
      list("window_ref" aslRef)
      list("target" aslTarget)
      list("items" aiPdkArray(aslPage))
      list("total" length(aslItems))
      list("max_items" aslLimit)
      list("offset" aslOffset)
      list("truncated" aiPdkBool(aslTruncated))
      list("selection_count" if(aslSelected length(car(aslSelected)) 0)))))
    when(strlen(aslResult)>55000
      return(aiPdkError("result_too_large" "Object list exceeds the limit")))
    return(aslResult)))

procedure(aiSelectionRaiseWindow(aswWindow)
  let((aswState)
    when(aswWindow
      aswState=car(errset(hiGetWindowState(aswWindow) nil))
      cond(
        (aswState=='iconified errset(hiDeiconifyWindow(aswWindow) nil))
        (aswState=='unmapped errset(hiMapWindow(aswWindow) nil)))
      errset(hiRaiseWindow(aswWindow) nil))
    aswWindow))

procedure(aiSelectionWindowTarget(aswLib aswCell aswView)
  let((aswFound aswAmbiguous aswWindow aswCv)
    when(isCallable('hiGetWindowList)
      foreach(aswWindow hiGetWindowList('all)
        aswCv=car(errset(geGetWindowCellView(aswWindow) nil))
        when(and(aswCv
                 aswCv~>libName==aswLib
                 aswCv~>cellName==aswCell
                 aswCv~>viewName==aswView)
          if(aswFound
            aswAmbiguous=t
            aswFound=aswWindow))))
    cond(
      (aswAmbiguous list('ambiguous aswFound aswWindow))
      (aswFound list('valid aswFound))
      (t list('not_found)))))

procedure(aiSelectionMasterMatches(asmObj asmMaster)
  if(asmMaster
    let((asmFound)
      asmFound=car(errset(asmObj~>master nil))
      and(asmFound
           asmFound~>libName==car(asmMaster)
           asmFound~>cellName==cadr(asmMaster)
           asmFound~>viewName==caddr(asmMaster)))
    t))

procedure(aiSelectionHighlightSet(ahsCv)
  let((ahsOld ahsSet ahsLayer)
    ahsOld=aiSelectionHighlights['set]
    when(and(ahsOld isCallable('geDeleteHilightSet))
      errset(geDeleteHilightSet(ahsOld) nil))
    foreach(ahsLayer list(
        list("y0" "drawing")
        list("wire" "drawing")
        list("instance" "drawing")
        list("pin" "drawing")
        list("annotate" "drawing"))
      unless(ahsSet
        ahsSet=car(errset(geCreateHilightSet(ahsCv ahsLayer nil) nil))))
    when(ahsSet
      aiSelectionHighlights['set]=ahsSet
      aiSelectionHighlights['cv]=ahsCv)
    ahsSet))

procedure(aiSelectionAddHilight(ashSet ashObj ashKind @optional ashPath)
  let((ashCount ashFigures ashFigure)
    ashCount=0
    cond(
      (ashKind=="instance"
        when(car(errset(geAddHilightFig(ashSet ashObj t ashPath) nil))
          ashCount=1))
      (ashKind=="net"
        ashFigures=car(errset(ashObj~>figs nil))
        foreach(ashFigure ashFigures
          when(car(errset(geAddHilightFig(ashSet ashFigure nil ashPath) nil))
            ashCount=ashCount+1)))
      (ashKind=="terminal"
        ashFigures=car(errset(ashObj~>figs nil))
        when(!ashFigures ashFigures=car(errset(list(ashObj~>fig) nil)))
        foreach(ashFigure ashFigures
          when(and(ashFigure
                   car(errset(geAddHilightFig(ashSet ashFigure nil ashPath) nil)))
            ashCount=ashCount+1)))
      (ashKind=="shape"
        when(car(errset(geAddHilightFig(ashSet ashObj nil ashPath) nil))
          ashCount=1)))
    ashCount))

procedure(aiSelectionUnionBox(aubBox aubOther)
  if(!aubBox aubOther
    if(!aubOther aubBox
      list(
        list(min(xCoord(car(aubBox)) xCoord(car(aubOther)))
             min(yCoord(car(aubBox)) yCoord(car(aubOther))))
        list(max(xCoord(cadr(aubBox)) xCoord(cadr(aubOther)))
             max(yCoord(cadr(aubBox)) yCoord(cadr(aubOther))))))))

procedure(aiSelectionObjectBox(aobObj aobKind)
  let((aobBox aobFigures aobFigure)
    cond(
      (member(aobKind list("instance" "shape"))
        aobBox=car(errset(aobObj~>bBox nil)))
      (t
        aobFigures=car(errset(aobObj~>figs nil))
        when(!aobFigures aobFigures=car(errset(list(aobObj~>fig) nil)))
        foreach(aobFigure aobFigures
          when(aobFigure
            aobBox=aiSelectionUnionBox(aobBox
              car(errset(aobFigure~>bBox nil)))))))
    aobBox))

procedure(aiSelectionKey(asxItem)
  when(asxItem
    let((asxPairs)
      asxPairs=cdr(asxItem)
      list(cadr(assoc("kind" asxPairs)) cadr(assoc("name" asxPairs))))))

procedure(aiSelectionInspect(asxRef asxOffset asxLimit)
  prog((asxState asxWindow asxSignature asxDisplay asxCv asxTarget
        asxSelected asxTail asxCount asxItems asxKeys asxItem asxKey
        asxUnsupported asxTruncated asxPage asxRemaining asxPageCount asxResult)
    unless(and(fixp(asxOffset) asxOffset>=0 asxOffset<=4096
               fixp(asxLimit) asxLimit>=1 asxLimit<=50)
      return(aiPdkError("invalid_arguments"
        "Selection offset must be 0..4096 and max_items must be 1..50")))
    unless(and(stringp(asxRef)
               rexMatchp("^wctx_[A-Za-z0-9_-]+_[0-9]+$" asxRef))
      return(aiPdkError("invalid_arguments" "Invalid window reference")))
    unless(isCallable('geGetSelSet)
      return(aiPdkError("capability_not_available"
        "Selection inspection is unavailable in this Virtuoso session")))
    asxState=aiWindowResolveState(asxRef)
    asxWindow=cadr(asxState)
    unless(asxWindow return(aiWindowStateError(asxState)))
    asxSignature=caddr(asxState)
    asxDisplay=car(asxSignature)
    asxCv=or(asxDisplay cadr(asxSignature))
    asxTarget=aiSelectionTarget(asxCv)
    unless(asxTarget return(aiPdkError("read_failed"
      "The selected window has no readable cellview target")))
    asxSelected=errset(geGetSelSet(asxWindow) nil)
    unless(asxSelected return(aiPdkError("read_failed"
      "The current selection could not be read")))
    asxSelected=car(asxSelected)
    asxTail=asxSelected asxCount=0 asxItems=nil asxKeys=nil
    asxUnsupported=0 asxTruncated=nil
    while(asxTail
      asxCount=asxCount+1
      when(asxCount>512
        asxTruncated=t
        asxTail=nil)
      when(asxTail
        asxItem=aiSelectionItem(car(asxTail))
        if(asxItem then
          asxKey=aiSelectionKey(asxItem)
          unless(member(asxKey asxKeys)
            asxKeys=cons(asxKey asxKeys)
            asxItems=cons(asxItem asxItems))
        else asxUnsupported=asxUnsupported+1)
        asxTail=cdr(asxTail)))
    asxItems=reverse(asxItems)
    when(length(asxItems) > asxOffset+asxLimit asxTruncated=t)
    asxRemaining=nthcdr(min(asxOffset length(asxItems)) asxItems)
    asxPageCount=0 asxPage=nil
    while(and(asxPageCount<asxLimit asxRemaining)
      asxPage=cons(car(asxRemaining) asxPage)
      asxPageCount=asxPageCount+1
      asxRemaining=cdr(asxRemaining))
    asxPage=reverse(asxPage)
    unless(equal(car(aiWindowResolveState(asxRef)) 'valid)
      return(aiWindowStateError(aiWindowResolveState(asxRef))))
    asxResult=aiPdkJson(aiPdkObj(list(
      list("ok" t)
      list("valid" t)
      list("schema" "cad.cv.selection.v1")
      list("session_ref" aiWindowEpoch)
      list("window_ref" asxRef)
      list("target" asxTarget)
      list("items" aiPdkArray(asxPage))
      list("selection_count" length(asxSelected))
      list("unsupported_count" asxUnsupported)
      list("max_items" asxLimit)
      list("offset" asxOffset)
      list("truncated" aiPdkBool(asxTruncated)))))
    when(strlen(asxResult)>55000
      return(aiPdkError("result_too_large"
        "Selection snapshot exceeds the response limit")))
    return(asxResult)))

aiSelectionVersion="20261021.ai.selection.v2"
)
