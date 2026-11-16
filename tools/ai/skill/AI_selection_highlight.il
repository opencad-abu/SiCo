; Bounded, non-mutating window/object highlight operations.
; Selection snapshots and object listing remain in AI_selection_context.il.
unless(and(boundp('aiSelectionHighlightVersion)
           aiSelectionHighlightVersion=="20261023.ai.selection.highlight.v4")

procedure(aiSelectionHighlightRevision() "20261023.ai.selection.highlight.v4")

procedure(aiSelectionWindows(aslLib aslCell aslView)
  let((aslRows aslWin aslCv aslRef aslTarget aslRow)
    foreach(aslWin hiGetWindowList('all)
      aslCv=car(errset(geGetWindowCellView(aslWin) nil))
      when(and(aslCv
               aslCv~>libName==aslLib
               aslCv~>cellName==aslCell
               aslCv~>viewName==aslView)
        aslRef=aiWindowCapture(aslWin)
        unless(aslTarget aslTarget=aiSelectionTarget(aslCv))
        aslRow=aiPdkObj(list(
          list("window_ref" aslRef)
          list("window" sprintf(nil "%L" aslWin))
          list("title_bytes_hex" aiWindowTitleBytes(aslWin))))
        aslRows=cons(aslRow aslRows)))
    if(aslRows
      aiPdkJson(aiPdkObj(list(
        list("ok" t)
        list("valid" t)
        list("schema" "cad.cv.windows.v1")
        list("session_ref" aiWindowEpoch)
        list("target" aslTarget)
        list("windows" aiPdkArray(reverse(aslRows))))))
      aiPdkError("target_not_open" "The requested cellview is not open"))))

procedure(aiSelectionSelectObject(assWindow assObject assKind)
  let((assFigures assFigure assCount)
    errset(geDeselectAll(assWindow) nil)
    cond(
      (assKind=="shape"
        when(isCallable('geSelectObjectNoFilter)
          errset(geSelectObjectNoFilter(assObject) nil)))
      (assKind=="instance"
        when(isCallable('geSelectFig)
          errset(geSelectFig(assObject) nil)))
      (t
        when(assKind=="terminal"
          assFigures=aiSelectionTerminalFigures(assObject))
        when(!assFigures
          assFigures=car(errset(assObject~>figs nil)))
        when(!assFigures assFigures=car(errset(list(assObject~>fig) nil)))
        when(isCallable('geSelectFig)
          foreach(assFigure assFigures
            when(assFigure errset(geSelectFig(assFigure) nil))))))
    assCount=length(car(errset(geGetSelSet(assWindow) nil)))
    when(and(!assCount isCallable('geSelectFig))
      errset(geSelectFig(assObject) nil)
      assCount=length(car(errset(geGetSelSet(assWindow) nil))))
    when(and(!assCount isCallable('geSelectObjectNoFilter))
      errset(geSelectObjectNoFilter(assObject) nil)
      assCount=length(car(errset(geGetSelSet(assWindow) nil))))
    assCount))

procedure(aiSelectionHighlight(ahsLib ahsCell ahsView ahsKind ahsName ahsHierarchy ahsMaster @optional (ahsWindowRef nil))
  prog((ahsTarget ahsWindow ahsCv ahsObject ahsSet ahsCount ahsBox
        ahsState ahsZoomed ahsResult ahsRef ahsLeafCv ahsPath ahsStep
        ahsInst ahsMasterCv ahsShape ahsFocus ahsSelected)
    unless(and(stringp(ahsLib) stringp(ahsCell) stringp(ahsView)
               stringp(ahsKind) stringp(ahsName))
      return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
        "invalid_arguments" "Selection target is incomplete")))
    unless(member(ahsKind list("instance" "net" "terminal" "shape" "cellview"))
      return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
        "unsupported_object" "This object kind cannot be located")))
    unless(listp(ahsHierarchy)
      return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
        "invalid_arguments" "Invalid object hierarchy")))
    unless(or(ahsKind=="cellview"
              and(isCallable('geCreateHilightSet) isCallable('geAddHilightFig)))
      return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
        "capability_not_available"
        "Object highlighting is unavailable in this Virtuoso session")))
    if(ahsWindowRef then
      ahsState=aiWindowResolveState(ahsWindowRef)
      unless(car(ahsState)=='valid return(aiWindowStateError(ahsState)))
      ahsWindow=cadr(ahsState)
    else
      ahsState=aiSelectionWindowTarget(ahsLib ahsCell ahsView)
      when(car(ahsState)=='ambiguous
        return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
          "ambiguous_target"
          "More than one window displays the requested cellview")))
      unless(car(ahsState)=='valid
        return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
          "target_not_open" "The requested cellview is not open")))
      ahsWindow=cadr(ahsState))
    ahsCv=car(errset(geGetEditCellView(ahsWindow) nil))
    unless(ahsCv
      return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
        "read_failed" "The target edit cellview is unavailable")))
    unless(and(ahsCv~>libName==ahsLib ahsCv~>cellName==ahsCell ahsCv~>viewName==ahsView)
      return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
        "stale_context" "The selected window target changed")))
    ahsTarget=aiSelectionTarget(ahsCv)
    unless(ahsTarget return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
      "read_failed" "Invalid target identity")))
    ahsFocus=(ahsKind=="cellview") ahsPath=nil ahsObject=nil ahsCount=0
    ahsSelected=0
    aiSelectionRaiseWindow(ahsWindow)
    when(isCallable('hiSetCurrentWindow)
      errset(hiSetCurrentWindow(ahsWindow) nil))
    if(ahsFocus then
      ahsBox=aiWindowBBox(car(errset(hiGetViewBBox(ahsWindow) nil)))
    else
      ahsLeafCv=ahsCv
      foreach(ahsStep ahsHierarchy
        unless(and(stringp(ahsStep) strlen(ahsStep)>0)
          return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
            "invalid_arguments" "Invalid hierarchy step")))
        ahsInst=car(errset(dbFindAnyInstByName(ahsLeafCv ahsStep) nil))
        unless(ahsInst
          return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
            "stale_object" "A hierarchy instance no longer exists")))
        ahsMasterCv=car(errset(ahsInst~>master nil))
        unless(ahsMasterCv
          return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
            "stale_object" "A hierarchy master is unavailable")))
        ahsPath=cons(list(ahsInst 0 0 0) ahsPath)
        ahsLeafCv=ahsMasterCv)
      ahsPath=reverse(ahsPath)
      cond(
        (ahsKind=="instance"
          ahsObject=car(errset(dbFindAnyInstByName(ahsLeafCv ahsName) nil)))
        (ahsKind=="net"
          ahsObject=car(errset(dbFindNetByName(ahsLeafCv ahsName) nil)))
        (ahsKind=="terminal"
          ahsObject=car(errset(dbFindTermByName(ahsLeafCv ahsName) nil)))
        (ahsKind=="shape"
          foreach(ahsShape ahsLeafCv~>shapes
            when(aiSelectionShapeName(ahsShape)==ahsName ahsObject=ahsShape))))
      unless(ahsObject
        return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
          "stale_object"
          "The referenced object no longer exists in this cellview")))
      when(and(ahsKind=="instance"
               !aiSelectionMasterMatches(ahsObject ahsMaster))
        return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
          "object_changed"
          "The instance master no longer matches the reference")))
      when(!ahsPath
        ahsSelected=aiSelectionSelectObject(ahsWindow ahsObject ahsKind))
      ahsSet=aiSelectionHighlightSet(ahsCv)
      unless(ahsSet
        return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
          "highlight_failed" "Could not create a highlight set")))
      ahsCount=aiSelectionAddHilight(ahsSet ahsObject ahsKind ahsPath)
      unless(ahsCount>0
        errset(geDeleteHilightSet(ahsSet) nil)
        aiSelectionHighlights['set]=nil
        return(datahubLegacyError("sico.cad.highlight.v1" "highlight"
          "highlight_failed"
          "The referenced object has no drawable figure")))
      errset(ahsSet~>enable=t nil)
      ahsBox=unless(ahsPath aiSelectionObjectBox(ahsObject ahsKind)))
    ahsZoomed=nil
    when(and(ahsBox isCallable('hiZoomIn))
      ahsZoomed=if(car(errset(hiZoomIn(ahsWindow ahsBox) nil)) t nil))
    when(and(ahsBox isCallable('geRefreshWindow))
      errset(geRefreshWindow(ahsWindow ahsBox) nil))
    aiSelectionRaiseWindow(ahsWindow)
    when(isCallable('hiSetCurrentWindow)
      errset(hiSetCurrentWindow(ahsWindow) nil))
    ahsRef=aiWindowCapture(ahsWindow)
    ahsResult=datahubLegacyResult(datahubOk(
      "sico.cad.highlight.v1" 1 "selection-highlight" "highlight"
      aiPdkObj(list(
      list("session_ref" aiWindowEpoch)
      list("target" ahsTarget)
      list("kind" ahsKind)
      list("name" ahsName)
      list("window_ref" ahsRef)
      list("highlighted" if(ahsFocus 'pdkJsonFalse t))
      list("highlight_count" ahsCount)
      list("bbox" ahsBox)
      list("zoomed" aiPdkBool(ahsZoomed))
      list("selection_preserved" aiPdkBool(or(ahsFocus ahsPath)))
      list("selected" aiPdkBool(ahsSelected>0))))
      nil datahubMeta())
      "Highlight result exceeds the limit")
    return(ahsResult)))

aiSelectionHighlightVersion="20261023.ai.selection.highlight.v4"
)
