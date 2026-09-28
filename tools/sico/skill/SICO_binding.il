; Authenticated logical identity and immutable native window lifecycle.
; All request variables are dynamically scoped; never recover ownership from focus.
unless(boundp('sicoNativeTargets) sicoNativeTargets=makeTable("sicoNativeTargets" nil))
unless(boundp('sicoNativeCounter) sicoNativeCounter=0)

procedure(sicoBindingRevision() "20260921.sico.binding.v1")

procedure(sicoNativeRequest(anrCid anrLine)
  prog((anrFields anrTail anrBinding anrProtected anrError
        sicoNativeIdentity sicoNativeCid sicoNativeWindows)
    anrFields=parseString(anrLine " ") anrTail=reverse(anrFields)
    anrBinding=sicoBindings[anrCid]
    when(cadr(anrTail)=="@binding"
      anrProtected=errset(aiCopilotDecode(car(anrTail)) nil)
      sicoNativeIdentity=when(anrProtected car(anrProtected))
      anrFields=reverse(cddr(anrTail)) anrLine=buildString(anrFields " ")
      unless(and(listp(sicoNativeIdentity) length(sicoNativeIdentity)==8
        listp(nth(7 sicoNativeIdentity)))
        ; Validate the scalar fields separately; the eighth field is a resource list.
        sicoNativeIdentity=nil)
      unless(and(sicoNativeIdentity
        forall(anrIndex list(0 1 2 3 4 5 6) stringp(nth(anrIndex sicoNativeIdentity)))
        car(sicoNativeIdentity)!="" cadr(sicoNativeIdentity)!=""
        caddr(sicoNativeIdentity)==anrBinding['instanceId]
        nth(3 sicoNativeIdentity)==anrBinding['generation]
        nth(4 sicoNativeIdentity)==anrBinding['cwd]
        nth(6 sicoNativeIdentity)==car(anrFields)
        listp(nth(7 sicoNativeIdentity)))
        anrError=t))
    when(and(anrBinding['instanceBridge] !sicoNativeIdentity) anrError=t)
    when(anrError
      return(sprintf(nil "{\"id\":\"%s\",\"ok\":false,\"code\":\"binding_identity_invalid\",\"error\":\"Authenticated logical session identity required\",\"diagnostic\":{\"category\":\"binding\",\"operation_dispatched\":false,\"automatic_resume_allowed\":false}}\n" car(anrFields))))
    sicoNativeCid=anrCid
    return(sicoRequestBody(anrCid anrLine))))

; Read-only discovery precedes reservation; the write checks resolution again.
procedure(aiCopilotResolveResources(anpFunction anpValues anpExpected)
  prog((anpTargets anpRows anpPath)
    if(anpExpected then anpTargets=list(nth(2 anpExpected))
    else when(anpFunction=="aiCopilotOpenTarget" anpTargets=list(list(car(anpValues) cadr(anpValues) caddr(anpValues)))))
    when(anpFunction=="aiConfigCreate" anpTargets=append(anpTargets list(nth(5 anpValues))))
    foreach(anpTarget anpTargets
      unless(and(listp(anpTarget) length(anpTarget)==3 forall(anpName anpTarget stringp(anpName)))
        error("Invalid output resource"))
      if(and(anpExpected nth(3 anpExpected)=="library") then anpPath=nth(5 anpExpected)
      else anpPath=ddGetObjReadPath(ddGetObj(car(anpTarget))))
      unless(and(stringp(anpPath) substring(anpPath 1 1)=="/") error("Absolute output library path required"))
      anpRows=cons(append(anpTarget list(simplifyFilename(anpPath))) anpRows))
    return(aiCircuitObject(list(list("ok" t) list("resources" reverse(anpRows)))))))

; Every call that declares an output target is compared against the rows the
; ContextBroker resolved and reserved for this request, including guarded reads
; that carry the operation's task/target expectation. A rejection names the
; requested row and the authenticated rows so an operator can tell a path alias
; apart from an identity mismatch or a genuinely unreserved resource.
procedure(sicoNativeResourceCheck(ancFunction ancValues ancExpected)
  let((ancTargets ancReserved ancPath ancFound ancRequested)
    when(and(boundp('sicoNativeIdentity) sicoNativeIdentity)
      when(ancExpected ancTargets=list(nth(2 ancExpected)))
      when(ancFunction=="aiCopilotOpenTarget" ancTargets=list(list(car(ancValues) cadr(ancValues) caddr(ancValues))))
      when(ancFunction=="aiConfigCreate" ancTargets=append(ancTargets list(nth(5 ancValues))))
      ancReserved=nth(7 sicoNativeIdentity)
      foreach(ancTarget ancTargets
        ancPath=if(and(ancExpected nth(3 ancExpected)=="library") nth(5 ancExpected)
          ddGetObjReadPath(ddGetObj(car(ancTarget))))
        ancRequested=append(ancTarget list(simplifyFilename(ancPath)))
        ancFound=nil
        foreach(ancRow ancReserved
          when(equal(ancRow ancRequested) ancFound=t))
        unless(ancFound
          aiCopilotReject("binding_reservation_changed" "binding"
            sprintf(nil "Output resource differs from authenticated reservation: requested %s reserved %s"
              aiEvalTextPrefix(sprintf(nil "%L" ancRequested) 320)
              aiEvalTextPrefix(sprintf(nil "%L" ancReserved) 960))))))))

procedure(sicoNativeRecord(antCid antTarget antBinding antSnapshot)
  prog((antParent antRecord antKey antRow)
    antParent=sicoBindings[antCid]
    antRecord=sprintf(nil "{\"contract\":\"virtuoso_context.v1\",\"instance_id\":\"%s\",\"generation\":\"%s\",\"target_id\":\"%s\",\"snapshot\":%s}"
      aiJsonEscape(antParent['instanceId]) aiJsonEscape(antParent['generation]) aiJsonEscape(antTarget) antSnapshot)
    antKey=strcat(antParent['generation] "_" antTarget)
    unless(sicoNativeTargets[antKey]
      antRow=makeTable("sicoNativeTarget" nil)
      antRow['cid]=antCid antRow['ref]=antBinding['sharedWindowRef] antRow['record]=antRecord
      sicoNativeTargets[antKey]=antRow)
    return(antRecord)))

procedure(sicoNativeOutput(anoWindow anoTarget anoCreated anoVerified)
  prog((anoBinding anoSnapshot anoRecord anoId anoParent)
    unless(and(boundp('sicoNativeIdentity) sicoNativeIdentity anoWindow) return(nil))
    anoParent=sicoBindings[sicoNativeCid]
    unless(anoParent['targets] anoParent['targets]=makeTable("sicoInstanceTargets" nil))
    anoBinding=makeTable("sicoOutputBinding" nil)
    anoBinding['window]=anoWindow anoBinding['cwd]=nth(4 sicoNativeIdentity)
    anoBinding['entry]="workflow" anoSnapshot=sicoContextJson(anoBinding)
    unless(anoBinding['valid] return(nil))
    ; ContextJson captures the actual window design/session; the host compares it to reservations.
    sicoNativeCounter=sicoNativeCounter+1
    anoId=sprintf(nil "native_%d_%d" ipcGetPid() sicoNativeCounter)
    anoRecord=sicoNativeRecord(sicoNativeCid anoId anoBinding anoSnapshot)
    anoParent['targets][anoId]=anoBinding
    ipcWriteProcess(sicoNativeCid strcat("@binding " sprintf(nil
      "{\"kind\":\"binding.native_created\",\"request_id\":\"%s\",\"session_id\":\"%s\",\"binding_id\":\"%s\",\"context\":%s,\"created_by_workflow\":%s,\"verified\":%s,\"reason\":\"Native workflow opened an explicit target\"}\n"
      nth(6 sicoNativeIdentity) car(sicoNativeIdentity) cadr(sicoNativeIdentity) anoRecord
      if(anoCreated "true" "false") if(anoVerified "true" "false"))))
    return(t)))

procedure(sicoNativeGone(angRef angReason)
  let((angRow angKeys)
    foreach(angKey sicoNativeTargets
      angRow=sicoNativeTargets[angKey]
      when(and(angRow angRow['ref]==angRef) angKeys=cons(angKey angKeys)))
    foreach(angKey angKeys
      angRow=remove(angKey sicoNativeTargets)
      when(and(angRow !angRow['closed])
        angRow['closed]=t
        when(and(sicoBindings[angRow['cid]] ipcIsAliveProcess(angRow['cid]))
          ipcWriteProcess(angRow['cid] strcat("@binding " sprintf(nil
            "{\"kind\":\"binding.native_closed\",\"context\":%s,\"reason\":\"%s\"}\n"
            angRow['record] aiJsonEscape(angReason))))))) t))

procedure(sicoNativePostInstall(anwArgs)
  ; Cadence supplies a property list with the explicit window in the trigger args.
  ; Observation only; finite operation completion attests creation and readback.
  let((anwWindow)
    anwWindow=cadr(memq('window anwArgs))
    when(and(boundp('sicoNativeCid) sicoNativeCid anwWindow)
      unless(member(anwWindow sicoNativeWindows)
        sicoNativeWindows=cons(anwWindow sicoNativeWindows)
        aiWindowCapture(anwWindow)))) t)

procedure(sicoNativePoll()
  let((anlRow anlKeys)
    foreach(anlKey sicoNativeTargets anlKeys=cons(anlKey anlKeys))
    foreach(anlKey anlKeys
      anlRow=sicoNativeTargets[anlKey]
      when(anlRow
        if(or(anlRow['closed] !anlRow['ref] !sicoSessionLive(anlRow['cid])) then
          remove(anlKey sicoNativeTargets)
        else
          unless(aiWindowResolve(anlRow['ref])
            sicoNativeGone(anlRow['ref] "window_closed_or_changed"))))))
  hiRegTimer("sicoNativePoll()" 10))

procedure(sicoInstallNativeBindings()
  when(and(isCallable('hiRegTimer) isCallable('deRegUserTriggers) isCallable('hiGetBindKey))
    foreach(aniType list("schematic" "schematicXL" "schematicSymbol" "maestro" "maskLayout" "maskLayoutXL" "maskLayoutGXL")
      let((aniInfo)
        aniInfo=deGetAppInfo(aniType)
        when(and(aniInfo !member('sicoNativePostInstall aniInfo->userPostInstallTrigList))
          deRegUserTriggers(aniType nil nil 'sicoNativePostInstall))))
    unless(and(boundp('sicoNativePolling) sicoNativePolling)
      sicoNativePolling=t hiRegTimer("sicoNativePoll()" 10))) t)
