; Finite shared workflow adapter. The relay supplies literal data, never SKILL source.
unless(boundp('aiCopilotTasks) aiCopilotTasks=makeTable("aiCopilotTasks" nil))
; 兼容：历史脚本与在途探针仍引用旧产品名（Copilot Studio）时期的表名。
unless(boundp('aiStudioTasks) aiStudioTasks=aiCopilotTasks)
unless(boundp('aiCopilotClaims) aiCopilotClaims=makeTable("aiCopilotClaims" nil))

; Per-call diagnostic scope, dynamically bound only by sicoCircuitReply.
; Helpers also remain callable outside the Agent transport.
procedure(aiCopilotFailureStage(acfsStage acfsCode acfsCategory)
  when(and(boundp('aiCopilotFailureState) aiCopilotFailureState)
    aiCopilotFailureState['stage]=acfsStage
    aiCopilotFailureState['code]=acfsCode
    aiCopilotFailureState['category]=acfsCategory))

procedure(aiCopilotReject(acfrCode acfrCategory acfrMessage)
  when(and(boundp('aiCopilotFailureState) aiCopilotFailureState)
    aiCopilotFailureState['code]=acfrCode
    aiCopilotFailureState['category]=acfrCategory)
  error("%s" acfrMessage))

procedure(aiCopilotMutatingFunction(acmfName)
  member(acmfName list("aiCdfUpdateApply" "aiLibraryCreate" "aiCreateExecute"
    "aiTemplateSymbolCreate" "aiDrawSymbol" "aiConfigCreate" "aiSimCreate" "aiSimRun"
    "aiSimStop" "aiProjectClaim" "aiCopilotOpenTarget")))

procedure(aiCopilotEnterFunction(acefName)
  aiCopilotFailureStage(if(acefName=="aiCopilotDescribe" "describe" "invoke")
    if(aiCopilotMutatingFunction(acefName) "circuit_outcome_unknown"
      if(acefName=="aiCopilotDescribe" "circuit_preflight_failed" "circuit_read_failed"))
    if(aiCopilotMutatingFunction(acefName) "outcome_unknown"
      if(acefName=="aiCopilotDescribe" "preflight" "execution")))
  when(and(boundp('aiCopilotFailureState) aiCopilotFailureState)
    aiCopilotFailureState['functionEntered]=t
    aiCopilotFailureState['operationDispatched]=aiCopilotMutatingFunction(acefName)))

procedure(aiCopilotSourceCheck(assSource)
  sicoProjectContextJson(assSource)
  unless(assSource['projectValid]
    aiCopilotReject("circuit_context_stale" "binding" "Captured project is stale"))
  when(or(assSource['adeSession] assSource['historyHandle] assSource['testName])
    unless(assSource['valid]
      when(assSource['contextCode]=="context_unavailable"
        aiCopilotReject("context_unavailable" "binding" "Captured ADE source temporarily unavailable"))
      aiCopilotReject("circuit_context_stale" "binding" "Captured ADE source is stale"))) t)

procedure(aiCopilotTaskCheck(astTask)
  unless(and(boundp('aiCopilotSource) equal(aiCopilotTasks[astTask] aiCopilotOwner(aiCopilotSource))
    member(aiCircuitTasks[astTask] list("foreground" "background")))
    aiCopilotReject("task_source_mismatch" "preflight" "Task belongs to another logical session or retired binding lifetime; begin a new task and inspect the target")) t)

procedure(aiCopilotTaskBindingReply(astSource astTask)
  unless(and(equal(aiCopilotTasks[astTask] aiCopilotOwner(astSource))
    member(aiCircuitTasks[astTask] list("foreground" "background")))
    aiCircuitObject(list(list("ok" 'jsonFalse) list("code" "task_source_mismatch")
      list("task_ref" astTask) list("previous_presentation" aiCircuitTasks[astTask])
      list("operation_dispatched" 'jsonFalse)
      list("message" "Task belongs to another logical session or retired binding lifetime. Begin a new task; do not rebind retained preparations or claims.")
      list("next_action" "begin_circuit_task_then_inspect_target")))))

procedure(aiCopilotOwner(acoSource)
  if(and(boundp('sicoNativeIdentity) sicoNativeIdentity)
    list(car(sicoNativeIdentity) cadr(sicoNativeIdentity) caddr(sicoNativeIdentity)
      nth(3 sicoNativeIdentity) nth(4 sicoNativeIdentity)) acoSource))

procedure(aiCopilotRetainedCheck(acrFunction acrValues)
  let((acrEntry acrRun)
    case(acrFunction
      (("aiCreateInspect" "aiCrExportSvg") acrEntry=aiCreatedCircuits[car(acrValues)])
      ("aiTemplateSymbolInspect" acrEntry=aiTemplateSymbols[car(acrValues)])
      ("aiTemplateSymbolBinding" acrEntry=aiTemplateSymbols[car(acrValues)])
      ("aiConfigInspect" acrEntry=aiConfigEntries[car(acrValues)])
      (("aiSimModels" "aiSimInspect") acrEntry=aiSimulationSetups[car(acrValues)])
      (("aiSimStatus" "aiSimStop")
        acrRun=aiSimulationRuns[car(acrValues)]
        when(acrRun acrEntry=aiSimulationSetups[acrRun['setup]])))
    when(acrEntry aiCopilotTaskCheck(acrEntry['task]))))

procedure(aiCopilotDescribe(asdOperation asdRef asdTask asdTarget)
  prog((asdEntry asdType asdDigest asdPath asdRunDir asdModels)
    case(asdOperation
      ("apply_cdf_update"
        asdEntry=aiCdfUpdates[asdRef]
        unless(errset(aiCdfUpdateCheck(asdEntry) nil)
          return(aiCircuitError("cdf_update_preflight_failed" aiCdfErrorText())))
        aiCopilotTaskCheck(asdEntry['task])
        return(aiCircuitObject(list(list("ok" t) list("target_intent" "edit")
          list("binding" list(asdEntry['task] asdEntry['mode] asdEntry['target]
            "schematic" asdRef ddGetObjReadPath(ddGetObj(car(asdEntry['target]))) nil))))))
      ("create_circuit_library"
        asdEntry=aiLibraryPrepares[asdRef]
        aiLibraryCheck(asdEntry) aiCopilotTaskCheck(asdEntry['task])
        return(aiCircuitObject(list(list("ok" t) list("binding" aiLibraryBinding(asdEntry))))))
      ("create_circuit_from_plan"
        asdEntry=aiCreatePrepares[asdRef] asdType="schematic")
      ("edit_circuit"
        asdEntry=aiCreatePrepares[asdRef]
        unless(and(asdEntry asdEntry['targetAction]=="edit") error("Edit preparation missing"))
        asdType="schematic")
      (("create_template_symbol" "draw_symbol")
        asdEntry=aiTemplateSymbolPreviews[asdRef] asdType="schematicSymbol")
      ("run_simulation_setup"
        asdEntry=aiSimulationSetups[asdRef] asdType="maestro")
      ("create_circuit_config" asdType="config")
      ("create_simulation_setup" asdType="maestro")
      (t error("Unknown circuit operation")))
    when(member(asdOperation list("create_circuit_from_plan" "edit_circuit" "create_template_symbol" "draw_symbol" "run_simulation_setup"))
      unless(asdEntry error("Retained preparation/setup missing"))
      asdTask=asdEntry['task] asdDigest=asdEntry['digest]
      asdTarget=if(asdType=="schematicSymbol"
        list(asdEntry['lib] asdEntry['cell] "symbol") car(asdEntry['payload]))
      when(asdOperation=="run_simulation_setup" asdModels=asdEntry['models]))
    aiCopilotTaskCheck(asdTask)
    unless(and(listp(asdTarget) length(asdTarget)==3
      forall(asdName asdTarget aiEntryProjectName(asdName))) error("Invalid target identity"))
    asdPath=ddGetObjReadPath(ddGetObj(car(asdTarget)))
    unless(and(stringp(asdPath) substring(asdPath 1 1)=="/") error("Target library path unavailable"))
    when(asdOperation=="run_simulation_setup"
      asdRunDir=aiCopilotExpandHome(envGetVal("asimenv.startup" "projectDir"))
      unless(and(stringp(asdRunDir) strlen(asdRunDir)>0 substring(asdRunDir 1 1)=="/")
        aiCopilotReject("simulation_project_dir_invalid" "preflight"
          sprintf(nil
            "Simulation projectDir must be an absolute path or ~/... for the current user; observed %L"
            asdRunDir))))
    return(aiCircuitObject(list(list("ok" t)
      list("binding" list(asdTask aiCircuitTasks[asdTask] asdTarget asdType or(asdDigest "") asdPath asdRunDir))
      list("target_intent" if(and(asdEntry asdEntry['targetRecord]) "edit"
        if(asdOperation=="run_simulation_setup" "read" "create")))
      list("model_files" foreach(mapcar asdModel asdModels car(asdModel))))))))

; Compare retained task/target metadata without parsing JSON or following current focus.
procedure(aiCopilotFinal(asfFunction asfValues asfExpected asfClaim)
  prog((asfEntry asfTask asfTarget asfType asfDigest asfKey asfObserved asfRunDir)
    unless(and(listp(asfExpected) length(asfExpected)==7) error("Expected binding missing"))
    case(asfFunction
      ("aiCdfUpdateApply"
        asfEntry=aiCdfUpdates[car(asfValues)] aiCdfUpdateCheck(asfEntry)
        unless(and(equal(asfExpected list(asfEntry['task] asfEntry['mode] asfEntry['target]
          "schematic" car(asfValues) ddGetObjReadPath(ddGetObj(car(asfEntry['target]))) nil))
          cadr(asfClaim)==strcat("cdf-update:" cadr(asfValues)))
          aiCopilotReject("circuit_binding_changed" "binding" "CDF update binding changed"))
        return(t))
      ("aiLibraryCreate"
        asfEntry=aiLibraryPrepares[car(asfValues)]
        aiLibraryCheck(asfEntry) aiCopilotTaskCheck(asfEntry['task])
        unless(and(equal(asfExpected aiLibraryBinding(asfEntry))
          cadr(asfClaim)==strcat("library-create:" cadr(asfValues)))
          aiCopilotReject("circuit_binding_changed" "binding" "Library operation binding changed"))
        return(t))
      ("aiCreateExecute"
        asfEntry=aiCreatePrepares[car(asfValues)] asfType="schematic"
        asfKey=strcat(if(and(asfEntry asfEntry['targetAction]=="edit")
          "edit-execute:" "create-execute:") caddr(asfValues)))
      (("aiTemplateSymbolCreate" "aiDrawSymbol")
        asfEntry=aiTemplateSymbolPreviews[car(asfValues)] asfType="schematicSymbol"
        asfKey=strcat("template-symbol:" caddr(asfValues)))
      ("aiSimRun"
        asfEntry=aiSimulationSetups[car(asfValues)] asfType="maestro"
        asfKey=strcat("simulation-run:" caddr(asfValues))
        asfRunDir=aiCopilotExpandHome(envGetVal("asimenv.startup" "projectDir")))
      ("aiConfigCreate"
        asfTask=car(asfValues) asfTarget=list(nth(2 asfValues) nth(3 asfValues) nth(4 asfValues))
        asfType="config" asfKey=strcat("config:" cadr(asfValues)))
      ("aiSimCreate"
        asfTask=car(asfValues) asfTarget=car(nth(3 asfValues)) asfType="maestro"
        asfKey=strcat("simulation-create:" cadr(asfValues)))
      (t error("Unregistered write operation")))
    when(member(asfFunction list("aiCreateExecute" "aiTemplateSymbolCreate" "aiDrawSymbol" "aiSimRun"))
      unless(asfEntry error("Prepared reference missing"))
      asfTask=asfEntry['task] asfDigest=asfEntry['digest]
      asfTarget=if(asfType=="schematicSymbol" list(asfEntry['lib] asfEntry['cell] "symbol") car(asfEntry['payload])))
    aiCopilotTaskCheck(asfTask)
    unless(and(equal(asfExpected list(asfTask aiCircuitTasks[asfTask] asfTarget asfType
      or(asfDigest "") ddGetObjReadPath(ddGetObj(car(asfTarget))) asfRunDir))
      asfKey==cadr(asfClaim))
      aiCopilotReject("circuit_binding_changed" "binding"
        "Task, output, library, run directory or operation binding changed"))
    asfObserved=aiProjectTarget(asfTarget)
    if(and(asfFunction=="aiCreateExecute" asfEntry['targetRecord]) then
      aiCrTargetCheck(asfEntry)
    else if(asfFunction=="aiSimRun" then
      unless(and(nth(1 asfObserved)==t nth(2 asfObserved)=="maestro"
        nth(3 asfObserved)==t nth(6 asfObserved)!=t) error("Run target changed/modified"))
    else
      unless(and(nth(1 asfObserved)!=t nth(5 asfObserved)!=t nth(4 asfObserved)==t)
        error("Output already exists/open or is not writable"))))
    return(t)))

procedure(aiCopilotDispatch(asxSource asxFunction asxEnvelope @optional asxCid)
  prog((asxRow asxValues asxClaim asxExpected asxResult asxRef asxEntry)
    aiCopilotFailureStage("validate_call" "circuit_invalid_call" "validation")
    asxRow=assoc(asxFunction list(
      list("aiCdfUpdatePrepare" 'aiCdfUpdatePrepare 4 4)
      list("aiCopilotResolveResources" 'aiCopilotResolveResources 3 3)
      list("aiCdfUpdateApply" 'aiCdfUpdateApply 2 2)
      list("aiCircuitBeginTask" 'aiCircuitBeginTask 3 3)
      list("aiLibraryPrepare" 'aiLibraryPrepare 6 6) list("aiLibraryCreate" 'aiLibraryCreate 2 2)
      list("aiCrInspectTarget" 'aiCrInspectTarget 3 3)
      list("aiCreatePrepare" 'aiCreatePrepare 4 7) list("aiCreateExecute" 'aiCreateExecute 3 5)
      list("aiCreateInspect" 'aiCreateInspect 1 1)
      list("aiCrExportSvg" 'aiCrExportSvg 2 2)
      list("aiTemplateSymbolPreview" 'aiTemplateSymbolPreview 11 14)
      list("aiTemplateSymbolCreate" 'aiTemplateSymbolCreate 3 3) list("aiDrawSymbol" 'aiDrawSymbol 3 3)
      list("aiTemplateSymbolInspect" 'aiTemplateSymbolInspect 1 1)
      list("aiTemplateSymbolBinding" 'aiTemplateSymbolBinding 1 1)
      list("aiConfigCreate" 'aiConfigCreate 7 9) list("aiConfigInspect" 'aiConfigInspect 1 1)
      list("aiSimCreate" 'aiSimCreate 5 5) list("aiSimModels" 'aiSimModels 1 1)
      list("aiSimInspect" 'aiSimInspect 2 2) list("aiSimRun" 'aiSimRun 3 3)
      list("aiSimStatus" 'aiSimStatus 1 1) list("aiSimStop" 'aiSimStop 1 1)
      list("aiPdkCdfInspect" 'aiPdkCdfInspect 4 4) list("aiPdkCdfCase" 'aiPdkCdfCase 7 7)
      list("aiCdfRead" 'aiCdfRead 2 2) list("aiCdfDrop" 'aiCdfDrop 1 1)
      list("aiPdkCapture" 'aiPdkCapture 6 8) list("aiPdkRead" 'aiPdkRead 2 2)
      list("aiPdkDrop" 'aiPdkDrop 1 1) list("aiCrBindingGeometry" 'aiCrBindingGeometry 4 4)
      list("aiPdkInitializeLibrary" 'aiPdkInitializeLibrary 1 1)
      list("aiPdkPasDpt" 'aiPdkPasDpt 2 2)
      list("aiPdkSymbolWireProbe" 'aiPdkSymbolWireProbe 7 7)
      list("aiCrListExtensions" 'aiCrListExtensions 4 4)
      list("aiCopilotBindResults" 'aiCopilotBindResults 3 3)
      list("aiResultsCapture" 'aiResultsCapture 3 3) list("aiResultsPage" 'aiResultsPage 2 2)
      list("aiResultsRelease" 'aiResultsRelease 1 1)
      list("aiWaveCapture" 'aiWaveCapture 8 8) list("aiAcCapture" 'aiAcCapture 8 8)
      list("aiWavePage" 'aiWavePage 2 2) list("aiWaveRelease" 'aiWaveRelease 1 1)
      list("aiCopilotDescribe" 'aiCopilotDescribe 4 4) list("aiProjectClaim" 'aiProjectClaim 3 3)
      list("aiCopilotListTargets" 'aiCopilotListTargets 1 1)
      list("aiCopilotListLibraries" 'aiCopilotListLibraries 1 1)
      list("aiCopilotListCells" 'aiCopilotListCells 1 1)
      list("aiCopilotListViews" 'aiCopilotListViews 2 3)
      list("aiCopilotOpenTarget" 'aiCopilotOpenTarget 4 5)
      list("aiCopilotInspectSelected" 'aiCopilotInspectSelected 3 3)
      list("aiCopilotHighlightObject" 'aiCopilotHighlightObject 7 8)
      list("aiCopilotListObjects" 'aiCopilotListObjects 5 5)
      list("aiCopilotListObjectWindows" 'aiCopilotListObjectWindows 3 3)
      list("aiCopilotListWindows" 'aiCopilotListWindows 2 2)))
    unless(and(asxRow listp(asxEnvelope) length(asxEnvelope)==3) error("Invalid call envelope"))
    asxValues=car(asxEnvelope) asxClaim=cadr(asxEnvelope) asxExpected=caddr(asxEnvelope)
    unless(and(listp(asxValues) length(asxValues)>=nth(2 asxRow) length(asxValues)<=nth(3 asxRow))
      error("Invalid call arity"))
    when(asxFunction=="aiCopilotResolveResources"
      return(strcat("{\"valid\":true,\"circuit\":" apply('aiCopilotResolveResources asxValues) "}")))
    when(isCallable('sicoNativeResourceCheck)
      sicoNativeResourceCheck(asxFunction asxValues asxExpected))
    when(member(asxFunction list("aiResultsPage" "aiResultsRelease" "aiWavePage" "aiWaveRelease"))
      aiCopilotEnterFunction(asxFunction)
      asxResult=aiCopilotAnalysisPageCall(asxSource asxFunction asxValues)
      return(strcat("{\"valid\":true,\"circuit\":" asxResult "}")))
    when(aiCopilotSourcelessFunction(asxFunction)
      aiCopilotEnterFunction(asxFunction)
      return(strcat("{\"valid\":true,\"circuit\":" apply(cadr(asxRow) asxValues) "}")))
    aiCopilotFailureStage("source_check" "circuit_context_stale" "binding")
    aiCopilotSourceCheck(asxSource)
    aiCopilotFailureStage("task_check" "circuit_preflight_failed" "preflight")
    let(((aiCopilotSource asxSource))
      when(and(boundp('sicoNativeIdentity) sicoNativeIdentity)
        aiCopilotRetainedCheck(asxFunction asxValues))
      when(member(asxFunction list("aiCrInspectTarget" "aiCreatePrepare" "aiTemplateSymbolPreview"
          "aiLibraryPrepare" "aiCdfUpdatePrepare"))
        asxResult=aiCopilotTaskBindingReply(asxSource
          if(asxFunction=="aiCdfUpdatePrepare" cadr(asxValues) car(asxValues)))
        when(asxResult return(strcat("{\"valid\":true,\"circuit\":" asxResult "}"))))
      when(asxFunction=="aiCdfUpdatePrepare" aiCopilotTaskCheck(cadr(asxValues)))
      when(asxFunction=="aiCircuitBeginTask"
        asxRef=strcat("task:" car(asxValues))
        when(and(aiCircuitTasks[asxRef] !equal(aiCopilotTasks[asxRef] aiCopilotOwner(asxSource)))
          return(strcat("{\"valid\":true,\"circuit\":" aiCopilotTaskBindingReply(asxSource asxRef) "}"))))
      when(and(asxFunction=="aiProjectClaim" aiProjectClaims[cadr(asxValues)])
        error("Existing claim cannot be rebound; query original receipt"))
      when(member(asxFunction list("aiCrInspectTarget" "aiCreatePrepare" "aiTemplateSymbolPreview" "aiLibraryPrepare")) aiCopilotTaskCheck(car(asxValues)))
      when(and(asxFunction=="aiLibraryPrepare" nth(4 asxValues)!=simplifyFilename(getWorkingDir()))
        error("Library workspace differs from captured project"))
      when(asxFunction=="aiSimStop"
        asxEntry=aiSimulationSetups[aiSimulationRuns[car(asxValues)]['setup]]
        unless(asxEntry error("Original simulation is unavailable")) aiCopilotTaskCheck(asxEntry['task]))
      when(asxClaim
        aiCopilotFailureStage("claim_check" "circuit_claim_mismatch" "binding")
        unless(and(listp(asxClaim) length(asxClaim)==3 equal(aiCopilotClaims[cadr(asxClaim)] aiCopilotOwner(asxSource)))
          error("Claim belongs to another logical session"))
        apply('aiProjectAssertClaim asxClaim))
      when(member(asxFunction list("aiCdfUpdateApply" "aiCreateExecute" "aiTemplateSymbolCreate" "aiDrawSymbol" "aiConfigCreate" "aiSimCreate" "aiSimRun" "aiLibraryCreate"))
        aiCopilotFailureStage("final_check" "circuit_preflight_failed" "preflight")
        unless(asxClaim error("Write requires durable claim"))
        aiCopilotFinal(asxFunction asxValues asxExpected asxClaim))
      let(((aiProjectDispatchKey cadr(asxClaim)) (aiProjectDispatchDigest caddr(asxClaim)))
        aiCopilotEnterFunction(asxFunction)
        asxResult=if(member(asxFunction list("aiResultsCapture" "aiWaveCapture" "aiAcCapture"))
          aiCopilotAnalysisCapture(asxFunction asxValues) apply(cadr(asxRow) asxValues)))
      when(and(asxFunction=="aiCircuitBeginTask" aiCircuitTasks[asxRef]==cadr(asxValues) eq(caddr(asxValues) t))
        aiCopilotTasks[asxRef]=aiCopilotOwner(asxSource))
      when(and(asxFunction=="aiProjectClaim" aiProjectClaims[cadr(asxValues)]==caddr(asxValues)
        !aiCopilotClaims[cadr(asxValues)]) aiCopilotClaims[cadr(asxValues)]=aiCopilotOwner(asxSource)))
    return(strcat("{\"valid\":true,\"circuit\":" asxResult "}"))))

; Capture reviewed synchronous workflow diagnostics. Window initialization explicitly
; uses aiCircuitNativeCall; ADE/simulation retain their native ports throughout.
procedure(aiCopilotSynchronousCaptureP(acscFunction)
  member(acscFunction list("aiCrInspectTarget" "aiCreatePrepare"
    "aiCreateExecute" "aiCreateInspect" "aiCrExportSvg" "aiCdfUpdatePrepare" "aiCdfUpdateApply"
    "aiLibraryPrepare" "aiLibraryCreate" "aiTemplateSymbolPreview"
    "aiTemplateSymbolCreate" "aiDrawSymbol" "aiTemplateSymbolInspect"
    "aiTemplateSymbolBinding" "aiConfigCreate" "aiConfigInspect"
    "aiCopilotDescribe" "aiCrListExtensions" "aiCrBindingGeometry"
    "aiPdkCapture" "aiPdkRead" "aiPdkDrop"
    "aiCopilotResolveResources" "aiProjectClaim" "aiPdkCdfInspect" "aiPdkCdfCase"
    "aiPdkPasDpt" "aiPdkSymbolWireProbe"
    "aiCopilotListTargets" "aiCopilotListLibraries" "aiCopilotListCells" "aiCopilotListViews"
    "aiCopilotInspectSelected" "aiCopilotHighlightObject" "aiCopilotListObjects"
    "aiCopilotListObjectWindows" "aiCopilotListWindows")))

procedure(aiCopilotCaptureRevision() "20261021.ai.circuit.capture.v7")

; Return (dispatch-result output output-truncated error). The dispatch result
; remains the normal errset list so callers can preserve the existing protocol.
procedure(aiCopilotCaptureDispatch(acocSource acocFunction acocEnvelope @optional acocCid)
  prog((acocCapture acocOutputResult aiCircuitNativePorts)
    aiCircuitNativePorts=list(poport woport errport)
    acocCapture=aiEvalCaptureCall('aiCopilotDispatch
      list(acocSource acocFunction acocEnvelope acocCid))
    acocOutputResult=aiBoundResultText(cadr(acocCapture))
    return(list(car(acocCapture) car(acocOutputResult) cadr(acocOutputResult) caddr(acocCapture)))))

; Payload is the dispatcher's JSON envelope. Its 180000-byte business budget
; plus at most 80000 escaped output bytes still fits the 262144-byte SiCo line.
procedure(aiCopilotAttachCapturedOutput(acoPayload acoOutput acoTruncated)
  strcat(substring(acoPayload 1 strlen(acoPayload)-1)
      ",\"output\":\"" aiJsonEscape(or(acoOutput ""))
      "\",\"output_truncated\":" if(acoTruncated "true" "false")
      ",\"output_capture\":\"skill_ports\"}"))

procedure(sicoCircuitReply(asrCid asrFields)
  prog((asrSource asrProtected asrPayload asrError asrErrorTruncated asrDiagnostic asrExtra
        asrCapture asrOutput asrOutputTruncated
        asrDiagnosticPairs aiCopilotFailureState)
    unless(length(asrFields)==5 error("Invalid circuit message"))
    aiCopilotFailureState=makeTable("aiCopilotFailureState" nil)
    aiCopilotFailureStage("resolve_source" "circuit_context_stale" "binding")
    asrProtected=errset(progn(
      asrSource=sicoSessionTarget(asrCid caddr(asrFields))
      if(or(asrSource aiCopilotSourcelessFunction(nth(3 asrFields))) then
        aiCopilotFailureStage("decode_call" "circuit_invalid_call" "validation")
        if(aiCopilotSynchronousCaptureP(nth(3 asrFields)) then
          asrCapture=aiCopilotCaptureDispatch(asrSource nth(3 asrFields)
            aiCopilotDecode(nth(4 asrFields)) asrCid)
        else aiCopilotDispatch(asrSource nth(3 asrFields)
            aiCopilotDecode(nth(4 asrFields)) asrCid))
      else
        aiCopilotReject("circuit_requires_target" "preflight" "Select an explicit circuit target"))) nil)
    ; Capture immediately: another errset (including UI callbacks) can overwrite it.
    unless(asrProtected asrError=sprintf(nil "%L" errset.errset))
    when(asrCapture
      asrProtected=car(asrCapture)
      asrOutput=cadr(asrCapture) asrOutputTruncated=caddr(asrCapture)
      asrError=cadddr(asrCapture))
    asrPayload=when(asrProtected car(asrProtected))
    when(and(stringp(asrPayload) strlen(asrPayload)<=180000)
      when(asrCapture
        asrPayload=aiCopilotAttachCapturedOutput(asrPayload asrOutput asrOutputTruncated))
      return(sprintf(nil "{\"id\":\"%s\",\"ok\":true,\"result\":%s}\n"
        car(asrFields) asrPayload)))
    when(asrProtected
      aiCopilotFailureStage("encode_reply"
        if(stringp(asrPayload) "circuit_result_too_large" "circuit_invalid_reply")
        if(aiCopilotFailureState['operationDispatched] "outcome_unknown" "execution"))
      asrError=if(stringp(asrPayload)
        sprintf(nil "Circuit reply exceeds 180000 characters (received %d)" strlen(asrPayload))
        "Circuit function did not return a JSON string"))
    asrError=or(asrError "Circuit function failed")
    asrErrorTruncated=strlen(asrError)>2048
    when(asrErrorTruncated asrError=aiEvalTextPrefix(asrError 2048))
    asrDiagnosticPairs=list(
      list("code" aiCopilotFailureState['code])
      list("category" aiCopilotFailureState['category])
      list("function" nth(3 asrFields)) list("stage" aiCopilotFailureState['stage])
      list("request_id" car(asrFields)) list("target_id" caddr(asrFields))
      list("function_entered" aiCircuitBool(aiCopilotFailureState['functionEntered]))
      list("operation_dispatched" aiCircuitBool(aiCopilotFailureState['operationDispatched]))
      list("automatic_resume_allowed" 'jsonFalse)
      list("raw_error" asrError) list("raw_error_truncated" aiCircuitBool(asrErrorTruncated)))
    when(asrCapture
      asrDiagnosticPairs=append(asrDiagnosticPairs list(
        list("output" or(asrOutput ""))
        list("output_truncated" aiCircuitBool(asrOutputTruncated))
        list("output_capture" "skill_ports"))))
    asrDiagnostic=aiCircuitObject(asrDiagnosticPairs)
    asrExtra=""
    when(aiCopilotFailureState['code]=="circuit_requires_target"
      asrExtra=strcat(",\"family\":" aiCircuitJson(AI_COPILOT_TARGET_FAMILY)
        ",\"candidates\":" aiCopilotJsonList(
          car(errset(aiCopilotOpenTargets(AI_COPILOT_TARGET_FAMILY) nil)))))
    return(sprintf(nil
      "{\"id\":\"%s\",\"ok\":false,\"code\":\"%s\",\"error\":\"%s\",\"diagnostic\":%s%s}\n"
      car(asrFields) aiCopilotFailureState['code] aiJsonEscape(asrError) asrDiagnostic asrExtra))))
