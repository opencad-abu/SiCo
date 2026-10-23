; Read-only target discovery and explicit target opening.
; Loaded with AI_copilot_circuit.il by the versioned AI parent.

procedure(aiCopilotTargetFamily(astFamily)
  if(astFamily=="layout"
    list("maskLayout" "maskLayoutXL" "maskLayoutGXL")
    list("schematic" "schematicXL" "schematicVXL")))

procedure(aiCopilotJsonList(asjItems)
  let((asjText)
    asjText=""
    foreach(asjItem asjItems
      asjText=strcat(asjText if(strlen(asjText)>0 "," "") asjItem))
    strcat("[" asjText "]")))

AI_COPILOT_TARGET_FAMILY="schematic"

procedure(aiCopilotSourcelessFunction(asfName)
  member(asfName aiCopilotSourcelessFunctions()))

procedure(aiCopilotSourcelessFunctions()
  list("aiCopilotListTargets" "aiCopilotListLibraries" "aiCopilotListCells"
    "aiCopilotListViews" "aiCopilotOpenTarget" "aiCopilotInspectSelected"
    "aiCopilotHighlightObject" "aiCopilotListObjects" "aiCopilotListObjectWindows"
    "aiCopilotListWindows"))

procedure(aiCopilotOpenWindows(asoFamily)
  let((asoFound asoWindow asoCv asoKey)
    asoFound=nil
    when(and(isCallable('hiGetWindowList) isCallable('geGetWindowCellView))
      foreach(asoWindow hiGetWindowList()
        asoCv=car(errset(geGetWindowCellView(asoWindow) nil))
        when(and(asoCv member(asoCv~>cellViewType aiCopilotTargetFamily(asoFamily)))
          asoKey=strcat(asoCv~>libName "/" asoCv~>cellName "/" asoCv~>viewName)
          unless(member(asoKey asoFound) asoFound=cons(asoKey asoFound)))))
    asoFound))

procedure(aiCopilotOpenTargets(asoFamily)
  let((asoTargets asoSeen asoWindow asoCv asoKey asoEditable asoObject)
    asoTargets=nil asoSeen=nil
    when(and(isCallable('hiGetWindowList) isCallable('geGetWindowCellView))
      foreach(asoWindow hiGetWindowList()
        asoCv=car(errset(geGetWindowCellView(asoWindow) nil))
        when(and(asoCv member(asoCv~>cellViewType aiCopilotTargetFamily(asoFamily)))
          asoKey=strcat(asoCv~>libName "/" asoCv~>cellName "/" asoCv~>viewName)
          unless(member(asoKey asoSeen)
            asoSeen=cons(asoKey asoSeen)
            asoObject=car(errset(ddGetObj(asoCv~>libName asoCv~>cellName asoCv~>viewName) nil))
            asoEditable=car(errset(and(asoObject ddIsObjWritable(asoObject)) nil))
            asoTargets=cons(sprintf(nil
              "{\"library\":\"%s\",\"cell\":\"%s\",\"view\":\"%s\",\"view_type\":\"%s\",\"editable\":%s}"
              aiJsonEscape(asoCv~>libName) aiJsonEscape(asoCv~>cellName) aiJsonEscape(asoCv~>viewName)
              aiJsonEscape(asoCv~>cellViewType) if(asoEditable "true" "false")) asoTargets)))))
    reverse(asoTargets)))

procedure(aiCopilotListTargets(aslFamily)
  strcat("{\"ok\":true,\"family\":\"" aiJsonEscape(aslFamily)
    "\",\"candidates\":" aiCopilotJsonList(aiCopilotOpenTargets(aslFamily)) "}"))

procedure(aiCopilotListLibraries(aslIncludeReadonly)
  let((aslParts aslEntry aslName aslObject aslWritable)
    aslParts=nil
    foreach(aslEntry car(errset(ddGetLibList() nil))
      aslName=if(stringp(aslEntry) aslEntry aslEntry~>name)
      aslObject=car(errset(ddGetObj(aslName) nil))
      aslWritable=car(errset(and(aslObject ddIsObjWritable(aslObject)) nil))
      when(or(aslWritable aslIncludeReadonly)
        aslParts=cons(sprintf(nil "{\"name\":\"%s\",\"writable\":%s}"
          aiJsonEscape(aslName) if(aslWritable "true" "false")) aslParts)))
    strcat("{\"ok\":true,\"libraries\":" aiCopilotJsonList(reverse(aslParts)) "}")))

procedure(aiCopilotListCells(aslLibrary)
  let((aslParts aslEntry aslName)
    aslParts=nil
    foreach(aslEntry car(errset(ddGetObj(aslLibrary)~>cells nil))
      aslName=if(stringp(aslEntry) aslEntry aslEntry~>name)
      aslParts=cons(strcat("\"" aiJsonEscape(aslName) "\"") aslParts))
    strcat("{\"ok\":true,\"cells\":" aiCopilotJsonList(reverse(aslParts)) "}")))

procedure(aiCopilotListViews(aslLibrary aslCell @optional aslFamily)
  let((aslParts aslEntry aslName aslObject aslFile aslType aslOpen)
    aslParts=nil
    foreach(aslEntry car(errset(ddGetObj(aslLibrary aslCell)~>views nil))
      aslName=if(stringp(aslEntry) aslEntry aslEntry~>name)
      aslObject=car(errset(ddGetObj(aslLibrary aslCell aslName) nil))
      aslFile=car(errset(ddGetObj(aslLibrary aslCell aslName "*") nil))
      aslType=car(errset(ddMapGetFileViewType(aslFile) nil))
      aslOpen=member(strcat(aslLibrary "/" aslCell "/" aslName)
        aiCopilotOpenWindows(if(aslFamily aslFamily "schematic")))
      aslParts=cons(sprintf(nil
        "{\"name\":\"%s\",\"view_type\":\"%s\",\"open\":%s}"
        aiJsonEscape(aslName) aiJsonEscape(if(aslType aslType ""))
        if(aslOpen "true" "false")) aslParts))
    strcat("{\"ok\":true,\"views\":" aiCopilotJsonList(reverse(aslParts)) "}")))

procedure(aiCopilotOpenTarget(aotLibrary aotCell aotView aotType @optional (aotMode "a"))
  let((aotWindow)
    unless(isCallable('deOpenCellView) error("GUI target opening is unavailable"))
    aotWindow=car(errset(deOpenCellView(aotLibrary aotCell aotView aotType nil aotMode) nil))
    unless(and(aotWindow isCallable('hiGetWindowList) member(aotWindow hiGetWindowList('all)))
      error("no target window"))
    aiCircuitPlaceRightHalf(aotWindow)
    aiCircuitRaiseWindow(aotWindow)
    when(isCallable('sicoNativeOutput)
      sicoNativeOutput(aotWindow list(aotLibrary aotCell aotView) nil t))
    sprintf(nil
      "{\"ok\":true,\"library\":\"%s\",\"cell\":\"%s\",\"view\":\"%s\",\"view_type\":\"%s\",\"window\":\"%s\"}"
      aiJsonEscape(aotLibrary) aiJsonEscape(aotCell) aiJsonEscape(aotView) aiJsonEscape(aotType)
      aiJsonEscape(sprintf(nil "%L" aotWindow)))))

procedure(aiCopilotInspectSelected(acsRef acsOffset acsLimit)
  if(isCallable('aiSelectionInspect)
    aiSelectionInspect(acsRef acsOffset acsLimit)
    aiPdkError("capability_not_available"
      "Selection inspection is not loaded in this Virtuoso session")))

procedure(aiCopilotHighlightObject(ahoLib ahoCell ahoView ahoKind ahoName ahoHierarchy ahoMaster @optional ahoWindowRef)
  if(isCallable('aiSelectionHighlight)
    aiSelectionHighlight(ahoLib ahoCell ahoView ahoKind ahoName ahoHierarchy ahoMaster ahoWindowRef)
    aiPdkError("capability_not_available"
      "Object highlighting is not loaded in this Virtuoso session")))

procedure(aiCopilotListObjects(aloRef aloKind aloQuery aloOffset aloLimit)
  if(isCallable('aiSelectionList)
    aiSelectionList(aloRef aloKind aloQuery aloOffset aloLimit)
    aiPdkError("capability_not_available"
      "Object listing is not loaded in this Virtuoso session")))

procedure(aiCopilotListObjectWindows(aloLib aloCell aloView)
  if(isCallable('aiSelectionWindows)
    aiSelectionWindows(aloLib aloCell aloView)
    aiPdkError("capability_not_available"
      "Object window listing is not loaded in this Virtuoso session")))

procedure(aiCopilotListWindows(aclOffset aclLimit)
  if(isCallable('aiWindowList)
    aiWindowList(aclOffset aclLimit)
    aiPdkError("capability_not_available"
      "Window listing is not loaded in this Virtuoso session")))

; ADE stores asimenv.startup/projectDir verbatim ("~/simulation" is the tool
; default) and expands "~" when it computes the run area. Expand the same way,
; from this Virtuoso process's environment, so the authenticated run directory
; matches what ADE will use. "~user" spellings and a missing/relative HOME are
; returned unchanged and stay rejected by the caller.
procedure(aiCopilotExpandHome(aehPath)
  let((aehHome)
    when(and(stringp(aehPath) strlen(aehPath)>0 substring(aehPath 1 1)=="~")
      aehHome=getShellEnvVar("HOME")
      when(and(stringp(aehHome) substring(aehHome 1 1)=="/")
        cond(
          (aehPath=="~" aehPath=aehHome)
          (substring(aehPath 1 2)=="~/" aehPath=strcat(aehHome substring(aehPath 2)))
          (t nil))))
    aehPath))
