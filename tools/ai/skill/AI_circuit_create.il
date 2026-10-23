; Task-bound prepare/create/inspect/release. Dependencies checked in the same native turn as writes.
unless(boundp('aiCreatePrepares) aiCreatePrepares=makeTable("aiCreatePrepares" nil))
unless(boundp('aiCreatedCircuits) aiCreatedCircuits=makeTable("aiCreatedCircuits" nil))
procedure(aiCrErrorText()
  let((aceText)
    aceText=if(errset.errset then sprintf(nil "%L" errset.errset) else "inspect native CIW diagnostics")
    if(strlen(aceText)>2048 then substring(aceText 1 2048) else aceText)))
procedure(aiCrReply(aceEntry)
  strcat("{\"svg_preview\":" or(aceEntry['svgPreviewReply] "null") ","
  substring(aiCircuitObject(list(list("ok" aiCircuitBool(!aceEntry['error]))
    list("prepare_ref" aceEntry['prepare]) list("circuit_ref" aceEntry['ref])
    list("task_ref" aceEntry['task]) list("target" car(aceEntry['payload]))
    list("geometry_plan_digest" aceEntry['digest]) list("stage" aceEntry['stage])
    list("instance" aceEntry['instance])
    list("presentation" aceEntry['mode]) list("view_created" aiCircuitBool(aceEntry['created]))
    list("target_action" or(aceEntry['targetAction] "create")) list("backup_target" aceEntry['backup])
    list("view_reused" aiCircuitBool(aceEntry['reused]))
    list("saved" aiCircuitBool(aceEntry['saved])) list("readback_verified" aiCircuitBool(aceEntry['verified]))
    list("check" aceEntry['check]) list("effective_parameters" aceEntry['params])
    list("routing" aceEntry['routing])
    list("text_layout" aceEntry['textLayout])
    list("template_routing" nth(5 aceEntry['payload]))
    list("edited" aiCircuitBool(aceEntry['targetAction]=="edit"))
    list("user_approved" aiCircuitBool(aceEntry['approved]))
    list("window_open" aiCircuitBool(and(aceEntry['window] member(aceEntry['window] hiGetWindowList('all)))))
    list("released" aiCircuitBool(aceEntry['released])) list("error" aceEntry['error])
    list("simulation_qualified" 'jsonFalse))) 2)))

procedure(aiCreatePrepare(acpTask acpRequest acpDigest acpPayload @optional acpGuard acpTargetUse acpEditPlan)
  prog((acpRecord acpResult acpEntry)
    unless(member(aiCircuitTasks[acpTask] list("foreground" "background"))
      return(aiCircuitError("task_not_found" "Begin with confirmed task mode")))
    acpResult=errset(aiRcReserve(strcat("create-prepare:" acpRequest)
      list(acpTask acpDigest acpPayload acpGuard acpTargetUse)) nil)
    unless(acpResult return(aiCircuitError("request_conflict_or_limit" "Preparation request conflict/limit")))
    acpRecord=car(acpResult) when(acpRecord['reply] return(acpRecord['reply]))
    acpEntry=makeTable("aiCreatePrepare" nil)
    acpEntry['task]=acpTask acpEntry['mode]=aiCircuitTasks[acpTask]
    acpEntry['payload]=acpPayload acpEntry['digest]=acpDigest
    acpEntry['pdkGuard]=acpGuard
    ; Bind previews to this project once, independent of subsequent CIW chdir.
    acpEntry['previewWorkspace]=simplifyFilename(cond(
      (and(boundp('sicoNativeIdentity) sicoNativeIdentity) nth(4 sicoNativeIdentity))
      (and(boundp('aiCopilotSource) aiCopilotSource aiCopilotSource['cwd]) aiCopilotSource['cwd])
      (t getWorkingDir())))
    when(acpTargetUse
      acpEntry['targetRecord]=aiCrTargets[car(acpTargetUse)]
      acpEntry['targetAction]=cadr(acpTargetUse))
    acpEntry['editPlan]=acpEditPlan
    acpEntry['prepare]=strcat("create-prepare:" acpRequest)
    acpResult=errset(progn(
      unless(and(or(length(acpPayload)==5
          and(length(acpPayload)==6 car(nth(5 acpPayload))=="cad.template.routing-receipt.v1")) length(cadr(acpPayload))>0 length(cadr(acpPayload))<=64
        aiCircuitTargetP(car(car(acpPayload)) cadr(car(acpPayload)) caddr(car(acpPayload)) "schematic"))
        error("invalid native creation payload"))
      acpEntry['workPath]=aiCrWorkPath(acpEntry) aiCrAbsent(acpEntry)
      when(and(acpTargetUse !acpEntry['targetRecord]) error("Target observation missing"))
      aiCrAdditionCheck(acpEntry)
      ; A rebuild keeps exactly what the plan declares: any other device, port or note
      ; is the user's content, so the review must name it and the user must approve
      ; dropping it (edit.drops) before the write is prepared.
      when(and(acpEntry['targetAction]=="edit"
          equal(cadr(assoc("mode" acpEntry['editPlan])) "rebuild")
          acpEntry['targetRecord]
          !cadr(assoc("drops" acpEntry['editPlan])))
        let((acpContents acpNamed acpPortNamed acpLost)
          acpContents=nth(7 acpEntry['targetRecord]['facts])
          foreach(acpRow cadr(acpEntry['payload]) acpNamed=cons(car(acpRow) acpNamed))
          foreach(acpPort caddr(acpEntry['payload]) acpPortNamed=cons(car(acpPort) acpPortNamed))
          foreach(acpInst car(acpContents)
            when(and(!member(car(acpInst) acpNamed)
              !member(cadr(cadr(acpInst)) list("ipin" "opin" "iopin")))
              acpLost=cons(strcat(car(acpInst) " (" cadr(cadr(acpInst)) ")") acpLost)))
          foreach(acpTerm cadr(acpContents)
            unless(member(car(acpTerm) acpPortNamed)
              acpLost=cons(strcat("port " car(acpTerm)) acpLost)))
          when(acpLost
            error(strcat("edit_loss_check: the rebuild would drop unlisted objects "
              aiCrValueText(sort(acpLost 'alphalessp))
              "; approve them explicitly with edit.drops")))))
      acpEntry['dependencies]=aiCrDependencies(acpEntry)
      acpEntry['stage]="prepared"
      aiCreatePrepares[acpEntry['prepare]]=acpEntry
      acpRecord['reply]=aiCrReply(acpEntry) t) t)
    unless(acpResult acpRecord['reply]=aiCircuitError("creation_preflight_failed"
      strcat("Selected target/master/CDF/geometry is unsupported or changed: " aiCrErrorText())))
    return(acpRecord['reply])))

procedure(aiCrValidate(aceEntry)
  prog((aceCv aceWindow aceTarget)
    aiCrWorkPath(aceEntry) aceTarget=car(aceEntry['payload]) aceWindow=aceEntry['window]
    unless(equal(aiCrDependencies(aceEntry) aceEntry['dependencies])
      error("selected dependencies changed since creation"))
    when(aceEntry['mode]=="foreground"
      unless(and(member(aceWindow hiGetWindowList('all))
        aiCircuitCvMatches(geGetWindowCellView(aceWindow) aceTarget)
        geGetWindowCellView(aceWindow)==geGetEditCellView(aceWindow)) error("window closed/rebound/EIP")))
    unwindProtect(progn(
      aceCv=dbOpenCellViewByType(car(aceTarget) cadr(aceTarget) caddr(aceTarget) "" "r")
      unless(and(aceCv !dbIsCellViewModified(aceCv)) error("created view missing/modified"))
      aiCrReadback(aceCv aceEntry)
      unless(equal(aiCrSnapshot(aceCv) aceEntry['snapshot]) error("saved circuit changed")))
      when(aceCv dbClose(aceCv)))
    return(t)))

procedure(aiCreateExecute(acePrepare aceDigest aceRequest @optional aceGuard aceApproval)
  prog((aceP aceRecord aceResult aceEntry aceCv aceTarget aceWindow aceOwn)
    aceP=aiCreatePrepares[acePrepare]
    unless(and(aceP aceP['digest]==aceDigest)
      return(aiCircuitError("prepare_not_found" "Current-session preparation required")))
    ; An in-place edit rewrites existing content, so it only runs after the user
    ; approved the reviewed plan: the approval digest must be the one recorded by
    ; aiCreatePrepare, and the target record proves the view is still the reviewed
    ; one. A missing or different approval never reaches the write.
    when(aceP['targetAction]=="edit"
      let((aceApproved)
        aceApproved=cadr(assoc("approval" aceP['editPlan]))
        unless(and(aceApproved stringp(aceApproved) equal(aceApproved aceApproval))
          return(aiCircuitError("edit_approval_required"
            "User approval for the reviewed edit plan is required before writing")))
        aceP['approved]=t))
    aceResult=errset(aiRcReserve(strcat(if(aceP['targetAction]=="edit" "edit-execute:" "create-execute:")
      aceRequest) list(acePrepare aceDigest)) nil)
    unless(aceResult return(aiCircuitError("request_conflict_or_limit" "Creation request conflict/limit")))
    aceRecord=car(aceResult) when(aceRecord['entry] return(aiCrReply(aceRecord['entry])))
    when(and(aceP['pdkGuard] !equal(aceP['pdkGuard] aceGuard))
      return(aiCircuitObject(list(list("ok" 'jsonFalse) list("code" "pdk_validation_required")
        list("pdk_guard" aceP['pdkGuard])))))
    aceEntry=makeTable("aiCreatedCircuit" nil)
    foreach(aceKey list('task 'mode 'payload 'digest 'prepare 'workPath 'dependencies
      'targetRecord 'targetAction 'approved 'editPlan 'previewWorkspace)
      aceEntry[aceKey]=aceP[aceKey])
    aceEntry['ref]=strcat("created:" aceRequest) aceEntry['stage]="preflight"
    aceRecord['entry]=aceEntry aiCreatedCircuits[aceEntry['ref]]=aceEntry
    aceTarget=car(aceEntry['payload])
    aceResult=errset(unwindProtect(progn(
      aiCrWorkPath(aceEntry) aiCrAbsent(aceEntry)
      aiCrAdditionCheck(aceEntry)
      unless(equal(aiCrDependencies(aceEntry) aceEntry['dependencies]) error("dependencies changed since preparation"))
      ; Execute each requested callback once before touching the destination view.
      aiCrPrepareCallbacks(aceEntry)
      aceEntry['failureDetail]=nil
      aiCrWorkPath(aceEntry) aiCrAbsent(aceEntry) aiCrAdditionCheck(aceEntry)
      unless(equal(aiCrDependencies(aceEntry) aceEntry['dependencies]) error("dependencies changed during callbacks"))
      aceEntry['stage]="create_view"
      if(aceEntry['targetRecord] then
        aceCv=nth(4 aceEntry['targetRecord]['facts])
        aceWindow=nth(3 aceEntry['targetRecord]['facts])
        unless(aceCv
          aceCv=apply('dbOpenCellViewByType append(aceTarget list("schematic" "a"))) aceOwn=t)
        when(and(aceEntry['mode]=="foreground" !aceWindow)
          aceWindow=aiCircuitNativeCall('deOpenCellView append(aceTarget list("schematic" nil "a"))))
        when(aceEntry['mode]=="foreground"
          aiCircuitNativeCall('aiCircuitPlaceRightHalf list(aceWindow))
          aiCircuitNativeCall('aiCircuitRaiseWindow list(aceWindow))
          unless(and(aceWindow geGetEditCellView(aceWindow)==aceCv)
            error("Inspected foreground binding unavailable"))
          aceEntry['window]=aceWindow)
        unless(and(aceCv aceCv~>mode!="r") error("Inspected view is not writable"))
        aceEntry['targetRecord]['consumed]=t aceEntry['reused]=t
        aceCv=aiCrExisting(aceCv aceEntry)
      else if(aceEntry['mode]=="foreground" then
        aceWindow=aiCircuitNativeCall('deNewCellView append(aceTarget list("schematic" nil)))
        unless(aceWindow error("new foreground window failed"))
        aiCircuitNativeCall('aiCircuitRaiseWindow list(aceWindow))
        aceEntry['window]=aceWindow aceEntry['created]=t
        aceCv=dbOpenCellViewByType(car(aceTarget) cadr(aceTarget) caddr(aceTarget) "" "r")
        aceOwn=when(aceCv t)
        unless(and(aceCv geGetWindowCellView(aceWindow)==aceCv geGetEditCellView(aceWindow)==aceCv)
          error("created window binding differs"))
      else
        aceCv=dbOpenCellViewByType(car(aceTarget) cadr(aceTarget) caddr(aceTarget) "schematic" "a")
        aceOwn=when(aceCv t)))
      unless(aceCv error("new schematic failed"))
      unless(aceEntry['reused] aceEntry['created]=t)
      ; A new view starts empty, a continuation appends to it and an approved edit
      ; rewrites it in place: only a writable view is required for that last case.
      unless(or(aceEntry['targetAction]=="edit" and(!aceEntry['targetRecord] aceCv~>mode!="r")
        aceEntry['targetAction]=="continue"
        and(!aceCv~>instances !aceCv~>shapes !aceCv~>terminals !aceCv~>nets aceCv~>mode!="r"))
        error("view not empty/writable"))
      aiCrBuild(aceCv aceEntry)
      aceEntry['stage]="check" aceEntry['check]=schCheck(aceCv)
      unless(equal(aceEntry['check] list(0 0)) error("schCheck errors/warnings: %L" aceEntry['check]))
      aceEntry['stage]="readback" aceEntry['params]=aiCrReadback(aceCv aceEntry)
      aceEntry['stage]="save"
      unless(dbSave(aceCv) error("dbSave failed"))
      aceEntry['saved]=t aceEntry['snapshot]=aiCrSnapshot(aceCv)
      when(aceWindow aiCircuitNativeCall('hiZoomIn list(aceWindow list(
        list(xCoord(car(aceCv~>bBox))-0.5 yCoord(car(aceCv~>bBox))-0.5)
        list(xCoord(cadr(aceCv~>bBox))+0.5 yCoord(cadr(aceCv~>bBox))+0.5))))) t)
      progn(
      unless(aceEntry['failureDetail] aceEntry['failureDetail]=aiCrErrorText())
      when(and(aceCv aceOwn)
        if(or(aceEntry['mode]=="foreground" aceEntry['saved]) then dbClose(aceCv)
          else aceEntry['partialCv]=aceCv)))) t)
    when(aceResult
      aceEntry['failureDetail]=nil
      aceEntry['stage]="saved_readback"
      aceResult=errset(aiCrValidate(aceEntry) t)
      when(aceResult aceEntry['verified]=t aceEntry['stage]="schematic_saved"))
    unless(aceResult aceEntry['error]=strcat("Failed at " aceEntry['stage]
      ": " or(aceEntry['failureDetail] aiCrErrorText())
      if(or(aceEntry['created] aceEntry['reused]) "; partial target retained. " "; target not opened for writing. ")
      "Same request does not repeat writes."))
    when(isCallable('sicoNativeOutput)
      aiCircuitNativeCall('sicoNativeOutput
        list(aceEntry['window] aceTarget aceEntry['created] aceEntry['verified])))
    return(aiCrReply(aceEntry))))

procedure(aiCreateInspect(aciRef)
  prog((aciEntry aciResult)
    aciEntry=aiCreatedCircuits[aciRef]
    unless(aciEntry return(aiCircuitError("circuit_not_found" "Unknown creation record")))
    when(or(aciEntry['released] aciEntry['error]) return(aiCrReply(aciEntry)))
    aciResult=errset(aiCrValidate(aciEntry) t)
    unless(aciResult return(aiCircuitError("created_circuit_changed" "Created context/data changed; inspect CIW")))
    return(aiCrReply(aciEntry))))
procedure(aiCreateRelease(acrRef)
  prog((acrEntry acrResult)
    acrEntry=aiCreatedCircuits[acrRef]
    unless(acrEntry return(aiCircuitError("circuit_not_found" "Unknown creation record")))
    when(acrEntry['released] return(aiCrReply(acrEntry)))
    when(acrEntry['partialCv]
      return(aiCircuitError("partial_circuit_retained" "Inspect failed background creation before releasing")))
    when(and(acrEntry['saved] !acrEntry['error])
      acrResult=errset(aiCrValidate(acrEntry) t)
      unless(acrResult return(aiCircuitError("created_circuit_changed" "Modified context retained"))))
    acrEntry['released]=t return(aiCrReply(acrEntry))))
