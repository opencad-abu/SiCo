; Parameter probe: new background SicoTest target, no circuit/netlist qualification.
unless(boundp('aiCdfProbes) aiCdfProbes=makeTable("aiCdfProbes" nil))
procedure(aiCdfProbeRevision() "20260924.ai.cdf.probe.v4")
procedure(aiCdfProbeCheckpoint(acpCdf acpLabel)
  let((acpCheckpoint acpSize)
    when(and(boundp('aiCdfProbeActive) aiCdfProbeActive)
      when(length(aiCdfProbeActive['trace])>=48 error("48 CDF checkpoints maximum"))
      acpCheckpoint=list(acpLabel aiCdfValues(acpCdf)) acpSize=strlen(aiCircuitJson(acpCheckpoint))
      when(acpSize>65536 error("CDF checkpoint exceeds 64 KiB"))
      aiCdfProbeActive['traceBytes]=or(aiCdfProbeActive['traceBytes] 0)+acpSize
      when(aiCdfProbeActive['traceBytes]>524288 error("CDF trace exceeds 512 KiB"))
      aiCdfProbeActive['trace]=append(aiCdfProbeActive['trace] list(acpCheckpoint))) t))
procedure(aiCdfSourceState(acpCdf)
  list(aiCdfParamRows(acpCdf nil) aiCdfTyped(acpCdf~>formInitProc)
    aiCdfTyped(acpCdf~>doneProc) aiCdfTyped(acpCdf~>simInfo)))
procedure(aiCdfProbeReply(acpEntry)
  aiCircuitObject(list(list("ok" aiCircuitBool(!acpEntry['error]))
    list("probe_ref" acpEntry['ref]) list("stage" acpEntry['stage]) list("error" acpEntry['error])
    list("target" acpEntry['target]) list("master" acpEntry['masterTarget])
    list("cdf_ref" acpEntry['cdfRef]) list("task_ref" acpEntry['task])
    list("presentation" acpEntry['mode]) list("extension_ref" acpEntry['extensionRef])
    list("extension_revision" acpEntry['extensionRevision])
    list("requested" or(acpEntry['activeRows] acpEntry['rows]))
    list("initial_requested" acpEntry['rows]) list("transition" acpEntry['transition]) list("view_created" aiCircuitBool(acpEntry['created]))
    list("saved" aiCircuitBool(acpEntry['saved])) list("readback_verified" aiCircuitBool(acpEntry['verified]))
    list("cdf_values_restored" acpEntry['restored]) list("cdf_metadata_unchanged" acpEntry['metadataUnchanged])
    list("callback_execution_attempted" aiCircuitBool(acpEntry['callbackAttempted]))
    list("output" or(acpEntry['output] ""))
    list("output_truncated" aiCircuitBool(acpEntry['outputTruncated]))
    list("output_capture" if(acpEntry['output] "skill_ports" "ciw"))
    list("trace" acpEntry['trace]) list("parameters" acpEntry['parameters])
    list("raw_properties" acpEntry['raw]) list("requested_matches" acpEntry['matches])
    list("window_open" aiCircuitBool(and(acpEntry['window] member(acpEntry['window] hiGetWindowList('all)))))
    list("electrical_checked" 'jsonFalse) list("simulation_qualified" 'jsonFalse)
    list("side_effect_scope" "effective_cell_parameter_values; arbitrary callback effects not rolled back"))))
procedure(aiCdfProbeCapture(acpEntry acpInst)
  prog((acpCdf acpParam acpMatches)
    acpCdf=cdfGetInstCDF(acpInst)
    acpEntry['parameters]=aiCdfParamRows(acpCdf acpInst)
    acpEntry['raw]=sort(foreach(mapcar acpProp acpInst~>prop
      list(acpProp~>name acpProp~>valueType aiCdfTyped(acpProp~>value))) 'acdValueLess)
    when(strlen(aiCircuitJson(list(acpEntry['parameters] acpEntry['raw])))>786432
      error("probe parameter/property evidence exceeds 768 KiB"))
    foreach(acpRow or(acpEntry['activeRows] acpEntry['rows])
      acpParam=cdfFindParamByName(acpCdf car(acpRow))
      acpMatches=cons(list(car(acpRow) aiCircuitBool(and(acpParam
        if(acpEntry['extensionRef]=="builtin:cdf-callbacks:v1"
          aiCdfCallbackEqual(acpParam~>value caddr(acpRow)) equal(acpParam~>value caddr(acpRow)))))) acpMatches))
    acpEntry['matches]=reverse(acpMatches)
    return(list(acpEntry['parameters] acpEntry['raw]))))
procedure(aiCdfProbeRun(acpEntry)
  prog((acpResult acpCapture acpTarget acpMaster acpCdf acpCv acpInst acpRow acpExt acpSaved
        acpBefore acpMetadata acpValues acpCheck aiCdfProbeActive)
    aiCdfProbeActive=acpEntry acpEntry['stage]="preflight"
    acpTarget=acpEntry['target]
    acpResult=errset(unwindProtect(progn(
      acpCapture=aiCdfCaptures[acpEntry['cdfRef]]
      unless(and(acpCapture !cadr(acpCapture) nth(10 acpCapture)=="schematicSymbol")
        error("probe requires an effective-cell symbol CDF capture"))
      unless(member(aiCircuitTasks[acpEntry['task]] list("foreground" "background")) error("task not found"))
      unless(and(car(acpTarget)=="SicoTest" caddr(acpTarget)=="schematic")
        error("probe target must be a new SicoTest schematic"))
      acpEntry['masterTarget]=nth(2 acpCapture)
      unless(equal(aiCdfSnapshot(car(acpCapture) nil) acpCapture) error("CDF/master context changed; inspect again"))
      when(apply('ddGetObj acpTarget) error("probe target exists; never overwrite"))
      foreach(acpOpen dbGetOpenCellViews()
        when(aiCircuitCvMatches(acpOpen acpTarget) error("probe target already open")))
      acpMaster=dbOpenCellViewByType(car(acpEntry['masterTarget]) cadr(acpEntry['masterTarget]) caddr(acpEntry['masterTarget]) "" "r")
      unless(and(acpMaster !dbIsCellViewModified(acpMaster) !acpMaster~>isParamCell
        !acpMaster~>superMaster !acpMaster~>instances) error("probe v1 requires saved static flat symbol"))
      acpCdf=cdfGetCellCDF(ddGetObj(acpMaster~>libName acpMaster~>cellName))
      foreach(acpP acpCdf~>parameters
        unless(or(null(acpP~>value) eq(acpP~>value t) numberp(acpP~>value)
          and(stringp(acpP~>value) strlen(acpP~>value)<=8192))
          error("probe restoration currently requires scalar CDF values")))
      acpRow=list("I_CDF_PROBE_0" acpEntry['masterTarget] nth(3 acpCapture) 0:0 "R0" acpEntry['rows]
        nil nil nil nil "device" acpEntry['extensionRef])
      acpExt=aiCrExtensionValidate(acpMaster acpRow)
      unless(acpExt aiCrSafeCdf(acpCdf))
      foreach(acpR acpEntry['rows] aiCrParam(acpCdf acpR acpExt))
      acpEntry['extensionRevision]=when(acpExt car(acpExt))
      acpMetadata=aiCdfSourceState(acpCdf) acpBefore=aiCdfValues(acpCdf)
      acpSaved=foreach(mapcar acpP acpCdf~>parameters list(acpP acpP~>value))
      acpEntry['stage]="test_workspace"
      aiTestWorkspaceLibrary()
      acpEntry['stage]="create_view"
      acpCv=dbOpenCellViewByType(car(acpTarget) cadr(acpTarget) caddr(acpTarget) "schematic" "a")
      unless(acpCv error("probe view creation failed")) acpEntry['created]=t
      unless(and(!acpCv~>instances !acpCv~>shapes !acpCv~>terminals acpCv~>mode!="r") error("probe target not empty/writable"))
      acpEntry['stage]="apply_parameters"
      aiCdfProbeCheckpoint(acpCdf "cell_before")
      unwindProtect(progn(
        if(acpExt then
          acpEntry['callbackAttempted]=t
          acpInst=apply(nth(5 acpExt) list(acpCv acpMaster "I_CDF_PROBE_0" 0:0 "R0" acpEntry['rows]))
        else
          acpInst=schCreateInst(acpCv acpMaster "I_CDF_PROBE_0" 0:0 "R0")
          foreach(acpR acpEntry['rows]
            unless(dbReplaceProp(acpInst car(acpR) aiCrParam(cdfGetInstCDF(acpInst) acpR) caddr(acpR)) error("parameter write failed"))
            aiCdfProbeCheckpoint(cdfGetInstCDF(acpInst) strcat("set:" car(acpR)))))
        unless(and(acpInst acpInst~>cellView==acpCv acpInst~>master==acpMaster acpInst~>name=="I_CDF_PROBE_0"
          length(acpCv~>instances)==1) error("extension returned wrong/multiple instances"))
        aiCdfProbeCheckpoint(cdfGetInstCDF(acpInst) "instance_final"))
        foreach(acpPair acpSaved car(acpPair)~>value=cadr(acpPair)))
      acpEntry['restored]=aiCircuitBool(equal(acpBefore aiCdfValues(acpCdf)))
      acpEntry['metadataUnchanged]=aiCircuitBool(equal(acpMetadata aiCdfSourceState(acpCdf)))
      unless(and(eq(acpEntry['restored] t) eq(acpEntry['metadataUnchanged] t)) error("source CDF restoration mismatch"))
      when(acpEntry['transition]
        unless(acpEntry['extensionRef]=="builtin:cdf-callbacks:v1"
          error("Mode transitions require the built-in executor"))
        acpEntry['stage]="transition" acpEntry['activeRows]=acpEntry['transition]
        aiCdfProbeCheckpoint(cdfGetInstCDF(acpInst) "transition:before")
        aiCdfCallbackApply(acpInst acpMaster acpEntry['transition])
        aiCdfProbeCheckpoint(cdfGetInstCDF(acpInst) "transition:after"))
      acpEntry['stage]="capture" acpValues=aiCdfProbeCapture(acpEntry acpInst)
      acpEntry['stage]="save"
      unless(dbSave(acpCv) error("probe save failed")) acpEntry['saved]=t
      dbClose(acpCv) acpCv=nil
      acpEntry['stage]="saved_readback"
      acpCv=dbOpenCellViewByType(car(acpTarget) cadr(acpTarget) caddr(acpTarget) "" "r")
      unless(equal(aiCdfProbeCapture(acpEntry dbFindAnyInstByName(acpCv "I_CDF_PROBE_0")) acpValues)
        error("saved probe differs"))
      acpEntry['verified]=t acpEntry['stage]="probe_saved")
      progn(acpEntry['failureDetail]=aiCrErrorText()
      when(acpSaved
        acpCheck=errset(progn(
          foreach(acpPair acpSaved car(acpPair)~>value=cadr(acpPair))
          acpEntry['restored]=aiCircuitBool(equal(acpBefore aiCdfValues(acpCdf)))
          acpEntry['metadataUnchanged]=aiCircuitBool(equal(acpMetadata aiCdfSourceState(acpCdf)))) t)
        unless(acpCheck acpEntry['restored]='jsonFalse))
      when(acpCv
        if(acpEntry['saved] then dbClose(acpCv)
          else acpEntry['partialCv]=acpCv))
      when(acpMaster dbClose(acpMaster)))) t)
    when(or(eq(acpEntry['restored] 'jsonFalse) eq(acpEntry['metadataUnchanged] 'jsonFalse)) acpResult=nil)
    unless(acpResult acpEntry['error]=strcat("Failed at " acpEntry['stage] ": "
      or(acpEntry['failureDetail] aiCrErrorText()) "; diagnostic target retained"))
    return(t)))
procedure(aiCdfProbe(acpRef acpTask acpRequest acpTarget acpRows acpExtension acpProbeRef @optional acpTransition)
  prog((acpReserve acpEntry acpCaptured acpOutput aiCircuitNativePorts)
    acpReserve=errset(aiRcReserve(strcat("cdf-probe:" acpRequest) list(acpRef acpTask acpTarget acpRows acpExtension acpTransition)) nil)
    unless(acpReserve return(aiCircuitError("request_conflict_or_limit" "Probe request conflicts or session limit reached")))
    acpReserve=car(acpReserve)
    when(acpReserve['entry] return(aiCdfProbeReply(acpReserve['entry])))
    acpEntry=makeTable("aiCdfProbe" nil)
    acpEntry['ref]=acpProbeRef acpEntry['cdfRef]=acpRef acpEntry['task]=acpTask
    acpEntry['mode]="background"
    acpEntry['transition]=acpTransition
    acpEntry['target]=acpTarget acpEntry['rows]=acpRows acpEntry['extensionRef]=acpExtension
    acpReserve['entry]=acpEntry aiCdfProbes[acpProbeRef]=acpEntry
    ; Keep diagnostics in the immutable outcome, before chunking/JSON encoding.
    ; A repeat or get_cdf_probe reads this evidence without rerunning callbacks.
    aiCircuitNativePorts=list(poport woport errport)
    acpCaptured=aiEvalCaptureCall('aiCdfProbeRun list(acpEntry))
    acpOutput=aiBoundResultText(cadr(acpCaptured))
    acpEntry['output]=car(acpOutput) acpEntry['outputTruncated]=cadr(acpOutput)
    unless(car(acpCaptured)
      acpEntry['error]=or(acpEntry['error] caddr(acpCaptured)))
    return(aiCdfProbeReply(acpEntry))))
procedure(aiCdfProbeGet(acpRef)
  if(aiCdfProbes[acpRef] then aiCdfProbeReply(aiCdfProbes[acpRef])
    else aiCircuitError("cdf_probe_not_found" "Unknown probe in this session")))
