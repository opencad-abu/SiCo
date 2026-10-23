; Shared window identity/observation backend. Only in-memory references and close hooks.
; Requires AI_protocol.il and AI_pdk_core.il; no editor focus, save or close operations.
unless(boundp('aiWindowEntries) aiWindowEntries=makeTable("aiWindowEntries" nil))
unless(boundp('aiWindowHandles) aiWindowHandles=makeTable("aiWindowHandles" nil))
unless(boundp('aiWindowCounter) aiWindowCounter=0)
unless(boundp('aiWindowEpoch)
  unless(and(boundp('cadContextBuilding) cadContextBuilding)
    aiWindowEpoch=car(last(parseString(makeTempFileName("/tmp/cwin") "/")))))
procedure(aiWindowRevision() "20261020.ai.window.v7")
procedure(aiWindowTry(awtFunction awtArgs)
  when(isCallable(awtFunction) car(errset(apply(awtFunction awtArgs) nil))))
; Required identity getters preserve (nil) versus errset failure. Optional UI
; metadata continues to use aiWindowTry.
procedure(aiWindowRead(awrFunction awrArgs)
  unless(isCallable(awrFunction) error("Window identity getter unavailable"))
  apply(awrFunction awrArgs))
procedure(aiWindowText(awxValue)
  cond((null(awxValue) nil) (stringp(awxValue) awxValue)
    (symbolp(awxValue) symbolToString(awxValue)) (t nil)))
; Window-manager titles can contain legacy non-UTF-8 trademark bytes. Preserve
; raw bytes for the shared Python decoder instead of corrupting the JSON channel.
procedure(aiWindowTitleBytes(awhWindow)
  let((awhTitle awhHex)
    awhTitle=aiWindowTry('hiGetWindowName list(awhWindow)) awhHex=""
    when(stringp(awhTitle)
      when(strlen(awhTitle)>4096 error("Window title limit"))
      for(awhIndex 1 strlen(awhTitle)
        awhHex=strcat(awhHex sprintf(nil "%02x" charToInt(concat(substring(awhTitle awhIndex 1))))))
      awhHex)))
procedure(aiWindowIdentity(awiSignature)
  ; Application type is display metadata, not target ownership. Keep the
  ; observed order but compare only the stable target components.
  list(car(awiSignature) cadr(awiSignature) caddr(awiSignature) nth(3 awiSignature)))
procedure(aiWindowDesignLabel(awlSignature)
  prog((awlCv awlProtected)
    awlCv=or(car(awlSignature) cadr(awlSignature))
    when(awlCv
      awlProtected=errset(sprintf(nil "%s/%s/%s"
        awlCv~>libName awlCv~>cellName awlCv~>viewName) nil)
      when(awlProtected return(car(awlProtected))))
    when(caddr(awlSignature) return(sprintf(nil "ade:%L" caddr(awlSignature))))
    return(nil)))
procedure(aiWindowDiagnostic(awdRef awdEntry awdReason awdCurrent)
  let((awdOld awdNew awdChanged awdCode awdBinding awdAppOld awdAppNew awdLabel)
    when(and(awdEntry !awdEntry['diagnosticLogged])
      awdOld=awdEntry['signature] awdNew=awdCurrent awdChanged="unknown"
      when(and(listp(awdOld) listp(awdNew) length(awdOld)>=4 length(awdNew)>=4)
        for(awdIndex 0 3
          when(and(awdChanged=="unknown"
                   !equal(nth(awdIndex awdOld) nth(awdIndex awdNew)))
            awdChanged=cond((awdIndex==0 "display") (awdIndex==1 "edit")
              (awdIndex==2 "ade") (t "hierarchy")))))
      awdAppOld=nth(4 awdOld) awdAppNew=nth(4 awdNew)
      ; Capture-time labels are strings. Reading a raw cellview here can hit a
      ; purged object after the editor closes, which dbGetq reports three times.
      awdLabel=or(awdEntry['label] "")
      awdCode=sprintf(nil "%s;changed=%s;app=%L->%L;target=%s"
        awdReason awdChanged awdAppOld awdAppNew awdLabel)
      when(and(isCallable('sicoDiagnostic) boundp('sicoBindings))
        foreach(awdCid sicoBindings
          when(and(!awdBinding sicoBindings[awdCid]
                   sicoBindings[awdCid]['diagnosticPath])
            awdBinding=sicoBindings[awdCid]))
        when(awdBinding
          errset(sicoDiagnostic(awdBinding "window.invalidated" awdRef
            "window_identity" awdRef awdCode 0 0) nil))
      awdEntry['diagnosticLogged]=t))))
procedure(aiWindowRetire(awrRef awrReason)
  let((awrRecord awrHandle awrCurrent)
    awrRecord=aiWindowEntries[awrRef]
    when(and(awrRecord !awrRecord['closed])
      awrRecord['closed]=t awrHandle=awrRecord['window]
      when(aiWindowHandles[awrHandle]==awrRef remove(awrHandle aiWindowHandles))
      when(isCallable('sicoNativeGone) sicoNativeGone(awrRef awrReason))
      ; Diagnostics cannot prevent retirement or return through the caller's prog.
      errset(prog(()
        awrCurrent=car(errset(aiWindowSignature(awrHandle) nil))
        aiWindowDiagnostic(awrRef awrRecord awrReason awrCurrent)
        return(nil)) nil))) t)
procedure(aiWindowGone(awgWindow)
  when(aiWindowHandles[awgWindow]
    aiWindowRetire(aiWindowHandles[awgWindow] "window_closed")) t)
; This native predicate has a no-warning argument. Never probe capabilities by
; calling cellview getters: errset does not suppress their GE-2067 warnings.
procedure(aiWindowGraphical(awgWindow)
  and(awgWindow aiWindowTry('geIsGraphicalWindow list(awgWindow t))))
procedure(aiWindowCapturable(awcWindow awcSignature)
  or(car(awcSignature) cadr(awcSignature) caddr(awcSignature)
    awcWindow==aiWindowRead('hiGetCIWindow nil)))
procedure(aiWindowSignature(awsWindow)
  let((awsSession awsDisplay awsEdit awsHierarchy awsApp awsCi)
    awsCi=aiWindowRead('hiGetCIWindow nil)
    awsApp=aiWindowTry('hiGetAppType list(awsWindow))
    ; GE owns its OA target even when it has an incidental ADE association.
    unless(awsWindow==awsCi
      if(aiWindowRead('geIsGraphicalWindow list(awsWindow t)) then
        awsDisplay=aiWindowRead('geGetWindowCellView list(awsWindow))
        awsEdit=aiWindowRead('geGetEditCellView list(awsWindow))
        when(awsDisplay awsHierarchy=aiWindowRead('geGetInstHierPath list(awsWindow)))
      else
        when(isCallable('axlGetWindowSession)
          awsSession=aiWindowRead('axlGetWindowSession list(awsWindow)))))
    list(awsDisplay awsEdit awsSession awsHierarchy awsApp)))
procedure(aiWindowAdeSession(awaWindow)
  caddr(aiWindowSignature(awaWindow)))
; Observations carry a state, not a guessed identity. Successful nil getters
; are known values; errors cannot prove closure or a target change.
procedure(aiWindowObserve(awoWindow)
  prog((awoProtected awoSignature)
    awoProtected=errset(prog(()
      unless(member(awoWindow aiWindowRead('hiGetWindowList list('all))) return(list('invalid)))
      awoSignature=aiWindowSignature(awoWindow)
      unless(aiWindowCapturable(awoWindow awoSignature) return(list('invalid)))
      return(list('valid awoSignature))) nil)
    return(if(awoProtected car(awoProtected) list('unknown)))))
procedure(aiWindowCaptureState(awcWindow)
  prog((awcEntry awcRef awcSignature awcHook awcObservation)
    awcObservation=aiWindowObserve(awcWindow)
    awcRef=aiWindowHandles[awcWindow]
    when(car(awcObservation)=='unknown return(list('unknown)))
    when(car(awcObservation)=='invalid
      when(awcRef aiWindowRetire(awcRef "window_closed_or_changed"))
      return(list('invalid)))
    awcSignature=cadr(awcObservation)
    when(awcRef
      awcEntry=aiWindowEntries[awcRef]
      when(and(!awcEntry['closed]
               equal(aiWindowIdentity(awcSignature) aiWindowIdentity(awcEntry['signature])))
        return(list('valid awcRef awcSignature)))
      aiWindowRetire(awcRef "window_signature_changed"))
    when(aiWindowCounter>=4096 return(list('unknown)))
    awcHook=aiWindowTry('hiRegCloseProc list(awcWindow 'aiWindowGone))
    unless(awcHook return(list('unknown)))
    aiWindowCounter=aiWindowCounter+1
    awcRef=sprintf(nil "wctx_%s_%d" aiWindowEpoch aiWindowCounter)
    awcEntry=makeTable("aiWindowEntry" nil)
    awcEntry['window]=awcWindow awcEntry['signature]=awcSignature
    awcEntry['label]=aiWindowDesignLabel(awcSignature)
    aiWindowEntries[awcRef]=awcEntry aiWindowHandles[awcWindow]=awcRef
    return(list('valid awcRef awcSignature))))
procedure(aiWindowCapture(awcWindow)
  cadr(aiWindowCaptureState(awcWindow)))
procedure(aiWindowResolveState(awrRef)
  prog((awrEntry awrWindow awrObservation awrSignature)
    awrEntry=aiWindowEntries[awrRef]
    unless(and(awrEntry !awrEntry['closed]) return(list('invalid)))
    awrWindow=awrEntry['window] awrObservation=aiWindowObserve(awrWindow)
    when(car(awrObservation)=='unknown return(list('unknown)))
    awrSignature=cadr(awrObservation)
    unless(and(car(awrObservation)=='valid
      equal(aiWindowIdentity(awrEntry['signature]) aiWindowIdentity(awrSignature)))
      aiWindowRetire(awrRef "window_closed_or_changed")
      return(list('invalid)))
    return(list('valid awrWindow awrSignature))))
procedure(aiWindowResolve(awrRef)
  cadr(aiWindowResolveState(awrRef)))
procedure(aiWindowStateError(aweState)
  if(car(aweState)=='unknown then
    aiPdkError("context_unavailable" "Source cannot be confirmed temporarily; capture retained")
  else aiPdkError("stale_context" "Source window closed or editing target changed")))
procedure(aiWindowCv(awvCv)
  when(awvCv
    aiPdkObj(list(list("lib" awvCv~>libName) list("cell" awvCv~>cellName)
      list("view" awvCv~>viewName) list("view_type" awvCv~>cellViewType)
      list("mode" awvCv~>mode)
      list("modified" car(errset(aiPdkBool(dbIsCellViewModified(awvCv)) nil)))))))
procedure(aiWindowBBox(awbBox)
  when(awbBox
    car(errset(list(list(xCoord(car(awbBox)) yCoord(car(awbBox)))
      list(xCoord(cadr(awbBox)) yCoord(cadr(awbBox)))) nil))))
procedure(aiWindowRow(awwWindow @optional awwRef)
  let((awwSig awwDisplay awwEdit awwSession awwKind awwClass awwParent awwHierarchy
        awwCi awwSelected awwTarget)
    awwSig=aiWindowSignature(awwWindow)
    awwDisplay=car(awwSig) awwEdit=cadr(awwSig) awwSession=caddr(awwSig)
    awwCi=aiWindowTry('hiGetCIWindow nil)
    awwClass=cond((member(awwWindow aiWindowTry('hiGetWindowList list('session))) "session")
      (member(awwWindow aiWindowTry('hiGetWindowList list('dockable))) "dockable") (t "window"))
    awwKind=cond((awwWindow==awwCi "ciw") (awwSession "ade")
      (and(awwDisplay awwDisplay~>cellViewType=="schematic") "schematic")
      (and(awwDisplay awwDisplay~>cellViewType=="schematicSymbol") "symbol")
      (and(awwDisplay awwDisplay~>cellViewType=="maskLayout") "layout") (t "other"))
    awwParent=aiWindowTry('hiGetSessionWindow list(awwWindow))
    foreach(awwStep nth(3 awwSig)
      awwHierarchy=cons(list(car(awwStep)~>name cadr(awwStep) caddr(awwStep) cadddr(awwStep)) awwHierarchy))
    when(or(awwEdit awwDisplay)
      awwSelected=aiWindowTry('geGetSelSetCount list(or(awwEdit awwDisplay))))
    when(awwSession
      awwTarget=aiPdkObj(list(list("lib" aiWindowTry('axlGetSessionLibName list(awwSession)))
        list("cell" aiWindowTry('axlGetSessionCellName list(awwSession)))
        list("view" aiWindowTry('axlGetSessionViewName list(awwSession)))
        list("read_only" car(errset(aiPdkBool(axlIsSessionReadOnly(awwSession)) nil)))
        list("modified" car(errset(aiPdkBool(maeIsSetupModified(?session awwSession)) nil))))))
    aiPdkObj(list(list("id" sprintf(nil "%L" awwWindow))
      list("window_ref" or(awwRef aiWindowCapture(awwWindow)))
      list("kind" awwKind) list("window_class" awwClass)
      list("application" nth(4 awwSig)) list("title_bytes_hex" aiWindowTitleBytes(awwWindow))
      list("parent_id" when(awwParent sprintf(nil "%L" awwParent)))
      list("is_current" aiPdkBool(awwWindow==aiWindowTry('hiGetCurrentWindow nil)))
      list("display_state" aiWindowText(aiWindowTry('hiGetWindowState list(awwWindow))))
      list("screen_bbox" aiWindowBBox(aiWindowTry('hiGetAbsWindowScreenBBox list(awwWindow))))
      list("screen_coordinates" "screen units; lower-left origin; excludes WM frame")
      list("visible_design_bbox" when(member(awwKind list("schematic" "symbol" "layout"))
        aiWindowBBox(aiWindowTry('hiGetViewBBox list(awwWindow)))))
      list("design_coordinates" "display cellview user units; not screen coordinates")
      list("cellview" aiWindowCv(awwDisplay)) list("display_cellview" aiWindowCv(awwDisplay))
      list("edit_cellview" aiWindowCv(awwEdit)) list("edit_hierarchy" aiPdkArray(reverse(awwHierarchy)))
      list("selection_count" awwSelected) list("ade_session" awwSession) list("maestro" awwTarget)))))
procedure(aiWindowList(awlOffset awlLimit)
  prog((awlAll awlTail awlRows awlCount awlRef awlResult)
    unless(and(fixp(awlOffset) awlOffset>=0 awlOffset<=4096
      fixp(awlLimit) awlLimit>=1 awlLimit<=200)
      return(aiPdkError("invalid_arguments" "Invalid window paging limits")))
    awlResult=errset(progn(
    awlAll=aiWindowRead('hiGetWindowList list('all))
    awlTail=nthcdr(min(awlOffset length(awlAll)) awlAll) awlCount=0
    while(and(awlTail awlCount<awlLimit)
      awlRef=aiWindowCapture(car(awlTail))
      awlRows=cons(aiWindowRow(car(awlTail) awlRef) awlRows)
      awlCount=awlCount+1 awlTail=cdr(awlTail))
    aiPdkJson(aiPdkObj(list(list("ok" t) list("valid" t) list("schema" "cad.windows.v1")
      list("session_ref" aiWindowEpoch) list("pid" ipcGetPid()) list("windows" aiPdkArray(reverse(awlRows)))
      list("max_items" awlLimit) list("total" length(awlAll)) list("offset" awlOffset)
      list("next_offset" when(awlTail awlOffset+awlCount)) list("truncated" aiPdkBool(awlTail))
      list("snapshot_atomic" 'pdkJsonFalse) list("read_scope" "current Virtuoso process only"))))) nil)
    unless(awlResult return(aiPdkError("read_failed" "Window summary unavailable")))
    when(strlen(car(awlResult))>55000
      return(aiPdkError("result_too_large" "Use a smaller max_items value")))
    return(car(awlResult))))
procedure(aiWindowInspect(awiRef)
  prog((awiWindow awiResult awiState)
    awiState=aiWindowResolveState(awiRef) awiWindow=cadr(awiState)
    unless(awiWindow return(aiWindowStateError(awiState)))
    awiResult=errset(aiPdkJson(aiPdkObj(list(list("ok" t) list("valid" t)
      list("schema" "cad.window.v1") list("session_ref" aiWindowEpoch)
      list("window" aiWindowRow(awiWindow awiRef))))) nil)
    unless(awiResult return(aiPdkError("read_failed" "Window inspection unavailable")))
    awiState=aiWindowResolveState(awiRef)
    unless(car(awiState)=='valid return(aiWindowStateError(awiState)))
    when(strlen(car(awiResult))>55000 return(aiPdkError("result_too_large" "Window inspection limit")))
    return(car(awiResult))))
