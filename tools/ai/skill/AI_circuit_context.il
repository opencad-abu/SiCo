; Explicit window/cellview/session bindings. Foreground release never closes UI.
procedure(aiCircuitContextJson(acxEntry)
  aiCircuitObject(list(list("ok" t) list("stage" acxEntry['state])
    list("context_ref" acxEntry['ref]) list("request_id" acxEntry['request])
    list("target" acxEntry['target]) list("effective_presentation" acxEntry['presentation])
    list("requested_access" acxEntry['access]) list("effective_access" acxEntry['effectiveAccess])
    list("ownership" acxEntry['ownership])
    list("window_open" aiCircuitBool(and(acxEntry['window]
      car(errset(member(acxEntry['window] hiGetWindowList('all)) nil))))) list("session" acxEntry['session])
    list("modified" acxEntry['modified]) list("saved" 'jsonFalse)
    list("content_revision_verified" 'jsonFalse) list("write_context_qualified" 'jsonFalse))))

procedure(aiCircuitInspect(aciRef)
  prog((aciEntry aciWindow aciCv aciResult)
    aciEntry=aiCircuitContexts[aciRef]
    unless(aciEntry return(aiCircuitError("context_not_found" "Unknown context reference" aciRef)))
    unless(aciEntry['state]=="open"
      return(aiCircuitError(aciEntry['state] "Context is not open; do not replay its open request" aciRef)))
    aciResult=errset(progn(
      aciWindow=aciEntry['window]
      if(aciWindow then
        unless(member(aciWindow hiGetWindowList('all)) error("bound window closed"))
        if(aciEntry['type]=="maestro" then
          unless(axlGetWindowSession(aciWindow)==aciEntry['session] error("window session changed"))
        else
          aciCv=geGetWindowCellView(aciWindow)
          unless(and(aciCv==aciEntry['cv] geGetEditCellView(aciWindow)==aciCv)
            error("window target or edit-in-place changed")))
      else
        aciCv=aciEntry['cv]
        when(and(aciEntry['type]!="maestro" !member(aciCv dbGetOpenCellViews()))
          error("owned database reference closed")))
      if(aciEntry['type]=="maestro" then
        unless(and(member(aciEntry['session] maeGetSessions())
          aiCircuitSessionMatches(aciEntry['session] aciEntry['target])) error("session target changed"))
        aciEntry['effectiveAccess]=if(axlIsSessionReadOnly(aciEntry['session]) "read" "edit")
        aciEntry['modified]=if(isCallable('maeIsSetupModified)
          aiCircuitBool(maeIsSetupModified(?session aciEntry['session])) nil)
      else
        unless(aiCircuitCvMatches(aciCv aciEntry['target]) error("cellview target changed"))
        aciEntry['effectiveAccess]=if(aciCv~>mode=="r" "read" "edit")
        aciEntry['modified]=if(isCallable('dbIsCellViewModified)
          aiCircuitBool(dbIsCellViewModified(aciCv)) nil))
      when(and(aciEntry['access]=="edit" aciEntry['effectiveAccess]!="edit")
        error("target is read only"))
      t) nil)
    unless(aciResult return(aiCircuitError("context_changed"
      "Bound window/session was closed, changed or could not be verified" aciRef)))
    return(aiCircuitContextJson(aciEntry))))

procedure(aiCircuitRelease(acrRef)
  prog((acrEntry acrResult acrStatus)
    acrEntry=aiCircuitContexts[acrRef]
    unless(acrEntry return(aiCircuitError("context_not_found" "Unknown context reference" acrRef)))
    when(acrEntry['state]=="released" return(aiCircuitContextJson(acrEntry)))
    if(acrEntry['ownership]=="owned_background" then
      acrResult=errset(progn(
        if(acrEntry['type]=="maestro" then
          ; No run tools accept this context yet; still reject externally started runs.
          when(member(acrEntry['session] maeGetSessions())
            unless(aiCircuitSessionMatches(acrEntry['session] acrEntry['target])
              error("session target changed"))
            unless(and(isCallable('maeIsSetupModified) isCallable('axlGetRunStatus))
              error("cannot verify setup modifications or run state"))
            when(maeIsSetupModified(?session acrEntry['session]) error("setup modified"))
            acrStatus=axlGetRunStatus(acrEntry['session])
            unless(and(listp(acrStatus) length(acrStatus)==2 numberp(car(acrStatus))
              numberp(cadr(acrStatus)) car(acrStatus)==cadr(acrStatus)) error("run state active/unknown"))
            unless(maeCloseSession(?session acrEntry['session] ?simulation "continue")
              error("session close failed")))
        else
          when(acrEntry['cv]
            unless(aiCircuitCvMatches(acrEntry['cv] acrEntry['target]) error("owned target changed"))
            unless(and(isCallable('dbIsCellViewModified)
              !dbIsCellViewModified(acrEntry['cv])) error("modified or unknown cellview state"))
            unless(dbClose(acrEntry['cv]) error("cellview close failed"))))
        t) t)
      unless(acrResult return(aiCircuitError("release_failed"
        "Owned reference retained; inspect modification/session state and CIW log" acrRef))))
    acrEntry['state]="released"
    acrEntry['cv]=nil acrEntry['session]=nil
    ; Keep GUI ownership with the user. No hiCloseWindow/dbClose on borrowed handles.
    return(aiCircuitContextJson(acrEntry))))

procedure(aiCircuitOpen(acoLib acoCell acoView acoType acoPresentation acoAccess acoRequest acoRef)
  prog((acoEntry acoPrevious acoSignature acoTarget acoMode acoObj acoWindows
        acoWindow acoCv acoSession acoMissing acoResult acoFound acoStep)
    unless(and(aiCircuitTargetP(acoLib acoCell acoView acoType)
      member(acoPresentation list("foreground" "background"))
      member(acoAccess list("read" "edit")) aiCircuitTextP(acoRequest) aiCircuitTextP(acoRef))
      return(aiCircuitError("invalid_arguments" "invalid target, presentation or request")))
    acoTarget=list(acoLib acoCell acoView)
    acoSignature=list(acoTarget acoType acoPresentation acoAccess)
    acoPrevious=aiCircuitRequests[acoRequest]
    when(acoPrevious
      unless(equal(acoPrevious['signature] acoSignature)
        return(aiCircuitError("request_conflict" "request_id already binds different arguments")))
      return(aiCircuitInspect(acoPrevious['ref])))
    when(aiCircuitRequestCount>=256
      return(aiCircuitError("context_limit" "256 open request records retained; start a fresh Virtuoso process")))
    when(aiCircuitContexts[acoRef]
      return(aiCircuitError("context_ref_conflict" "Context reference already exists")))
    acoMode=aiCircuitMode(acoPresentation)
    when(and(acoMode=="foreground" !aiCircuitGuiP())
      return(aiCircuitError("gui_unavailable" "Foreground requires a GUI Virtuoso instance")))
    when(and(acoType=="maestro" acoMode=="background" acoAccess=="edit")
      return(aiCircuitError("unsupported_access" "First background Maestro context adapter supports read access only")))
    acoMissing=aiCircuitMissing(aiCircuitOpenApis(acoType acoMode))
    when(acoMissing return(aiCircuitError("unsupported_runtime" sprintf(nil "Missing APIs: %L" acoMissing))))
    acoObj=ddGetObj(acoLib acoCell acoView)
    unless(acoObj return(aiCircuitError("view_not_found" "open_design_view does not create views")))
    unless(aiCircuitViewType(acoLib acoCell acoView)==acoType
      return(aiCircuitError("view_type_mismatch" "Actual view type differs from requested type")))
    unless(ddIsObjReadable(acoObj) return(aiCircuitError("not_readable" "Target is not readable")))
    when(and(acoAccess=="edit" !ddIsObjWritable(acoObj))
      return(aiCircuitError("not_writable" "Target is not writable")))
    ; Reserve before any editor side effect; failures remain inspectable, never replayed.
    acoEntry=makeTable("aiCircuitContext" nil)
    acoEntry['ref]=acoRef acoEntry['request]=acoRequest acoEntry['signature]=acoSignature
    acoEntry['target]=acoTarget acoEntry['type]=acoType acoEntry['presentation]=acoMode
    acoEntry['access]=acoAccess acoEntry['state]="opening"
    aiCircuitContexts[acoRef]=acoEntry aiCircuitRequests[acoRequest]=acoEntry
    aiCircuitRequestCount=aiCircuitRequestCount+1
    acoStep="inspect_existing"
    acoResult=errset(progn(
      acoWindows=when(aiCircuitGuiP() hiGetWindowList('all))
      if(acoType=="maestro" then
        acoFound=aiCircuitSessions(acoTarget)
        when(acoFound
          unless(and(acoMode=="foreground" length(acoFound)==1) error("setup already in use"))
          acoSession=car(acoFound)
          foreach(acoCandidate acoWindows
            when(car(errset(axlGetWindowSession(acoCandidate)==acoSession nil))
              acoWindow=acoCandidate))
          unless(acoWindow error("existing session is not a GUI session")))
      else
        foreach(acoCandidate acoWindows
          unless(acoCandidate==hiGetCIWindow()
          acoCv=car(errset(deGetCellView(acoCandidate) nil))
          when(and(acoCv aiCircuitCvMatches(acoCv acoTarget))
            when(acoWindow error("multiple windows match target; bind a single editor first"))
            acoWindow=acoCandidate)))
        when(and(acoMode=="background"
          exists(acoOpenCv dbGetOpenCellViews() aiCircuitCvMatches(acoOpenCv acoTarget)))
          error("target already open; use foreground binding")))
      acoStep="open_target"
      if(acoMode=="foreground" then
        unless(acoWindow
          acoWindow=deOpenCellView(acoLib acoCell acoView acoType nil
            if(acoAccess=="edit" "a" "r")))
        unless(and(acoWindow member(acoWindow hiGetWindowList('all))) error("no target window"))
        aiCircuitPlaceRightHalf(acoWindow)
        aiCircuitRaiseWindow(acoWindow)
        acoEntry['window]=acoWindow acoEntry['ownership]="borrowed_gui"
        if(acoType=="maestro" then
          acoSession=axlGetWindowSession(acoWindow)
          unless(acoSession error("GUI session not ready"))
          acoEntry['session]=acoSession
        else
          acoCv=geGetWindowCellView(acoWindow)
          acoEntry['cv]=acoCv
          unless(geGetEditCellView(acoWindow)==acoCv error("edit-in-place target differs"))
          when(and(acoAccess=="edit" acoCv~>mode=="r") error("editor is read only")))
      else
        acoEntry['ownership]="owned_background"
        if(acoType=="maestro" then
          acoSession=maeOpenSetup(acoLib acoCell acoView ?mode "r")
          acoEntry['session]=acoSession
          unless(acoSession error("open Maestro failed"))
        else
          acoCv=dbOpenCellViewByType(acoLib acoCell acoView acoType if(acoAccess=="edit" "a" "r"))
          acoEntry['cv]=acoCv
          unless(acoCv error("open cellview failed"))
          when(and(acoAccess=="edit" acoCv~>mode=="r") error("database opened read only"))))
      acoEntry['state]="open" t) t)
    unless(acoResult
      acoEntry['state]="open_failed"
      return(aiCircuitError("open_failed" strcat("Failed at " acoStep
        "; inspect log; release context to clean owned references. Do not replay") acoRef)))
    return(aiCircuitInspect(acoRef))))
