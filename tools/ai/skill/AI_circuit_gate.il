; Gate lifecycle. Explicit task; new cells only; retained partial state and dedup.
unless(boundp('aiGateCircuits) aiGateCircuits=makeTable("aiGateCircuits" nil))

procedure(aiGateReply(agrEntry)
  aiCircuitObject(list(list("ok" aiCircuitBool(!agrEntry['error]))
    list("circuit_ref" agrEntry['ref]) list("task_ref" agrEntry['task])
    list("gate" agrEntry['gate]) list("presentation" agrEntry['mode])
    list("stage" agrEntry['stage]) list("saved" aiCircuitBool(agrEntry['saved]))
    list("targets" foreach(mapcar agrRow agrEntry['views] car(agrRow)))
    list("checks" agrEntry['checks]) list("cdf_readback" agrEntry['cdf])
    list("interface_check" agrEntry['interfaceCheck])
    list("preview_digest" agrEntry['digest]) list("error" agrEntry['error])
    list("simulation_qualified" 'jsonFalse))))

procedure(aiGateInspect(agiRef)
  prog((agiEntry agiResult)
    agiEntry=aiGateCircuits[agiRef]
    unless(and(agiEntry agiEntry['saved]) return(aiCircuitError("gate_not_saved" "Unknown/incomplete gate")))
    agiResult=errset(aiGateValidate(agiEntry) t)
    unless(agiResult return(aiCircuitError("gate_changed" "Gate context or saved data changed; inspect CIW")))
    return(aiGateReply(agiEntry))))

procedure(aiGateBuildView(agbEntry agbCell agbView agbType agbSpec)
  prog((agbCv agbWindow agbTarget agbCheck agbSnapshot agbResult)
    agbTarget=list(agbEntry['lib] agbCell agbView)
    agbEntry['stage]=strcat(agbCell "/" agbView)
    unwindProtect(progn(
      if(agbEntry['mode]=="foreground" then
        agbWindow=deNewCellView(agbEntry['lib] agbCell agbView agbType nil)
        unless(agbWindow error("foreground view create failed"))
        aiCircuitRaiseWindow(agbWindow)
        agbCv=geGetWindowCellView(agbWindow)
        unless(agbCv==geGetEditCellView(agbWindow) error("unexpected EIP"))
      else agbCv=dbOpenCellViewByType(agbEntry['lib] agbCell agbView agbType "a"))
      unless(and(agbCv agbCv~>mode!="r") error("new view unavailable"))
      if(agbView=="symbol" then
        aiGateSymbol(agbCv agbEntry['symbolPorts] agbEntry['gate])
        aiGateVerifyPorts(agbCv agbEntry['symbolPorts]) agbCheck="ports_verified"
      else
        agbCheck=aiGateSchematic(agbCv agbSpec agbEntry['gate])
        aiGateVerifySchematic(agbCv agbSpec))
      when(agbView=="schematic"
        foreach(agbInst agbCv~>instances
          when(agbInst~>libName=="gpdk045"
            agbEntry['cdf]=cons(list(agbInst~>name setof(agbParam aiGateParams(agbInst)
              member(car(agbParam) list("model" "l" "w" "fw" "fingers" "m" "ad" "as" "pd" "ps")))) agbEntry['cdf]))))
      unless(dbSave(agbCv) error("view save failed"))
      agbSnapshot=aiGateSnapshot(agbCv)
      agbEntry['views]=append(agbEntry['views] list(list(agbTarget agbSnapshot agbSpec agbWindow)))
      agbEntry['checks]=append(agbEntry['checks] list(list(agbTarget agbCheck)))
      when(agbWindow hiZoomIn(agbWindow agbCv~>bBox))
      agbResult=t)
      when(and(agbCv agbEntry['mode]=="background") dbClose(agbCv)))
    return(agbResult)))

procedure(aiGateCreate(agcTask agcRequest agcLib agcCell agcGate agcPdk agcDigest agcDut agcPorts agcTb)
  prog((agcMode agcRecord agcResult agcEntry agcSignature agcRead)
    agcMode=aiCircuitTasks[agcTask]
    unless(agcMode return(aiCircuitError("task_not_found" "Begin a task with confirmed mode")))
    unless(and(aiCircuitTargetP(agcLib agcCell "schematic" "schematic")
      !member(agcLib list("gpdk045" "basic" "analogLib"))
      aiCircuitTextP(agcRequest) member(agcGate list("INV" "NAND2")) stringp(agcPdk))
      return(aiCircuitError("invalid_gate" "Invalid gate target/profile")))
    agcSignature=list(agcTask agcLib agcCell agcGate agcPdk agcDigest agcDut agcPorts agcTb)
    agcResult=errset(aiRcReserve(strcat("gate:" agcRequest) agcSignature) nil)
    unless(agcResult return(aiCircuitError("request_conflict_or_limit" "Gate request conflict/limit")))
    agcRecord=car(agcResult)
    when(agcRecord['entry] return(aiGateReply(agcRecord['entry])))
    agcEntry=makeTable("aiGateEntry" nil) agcRecord['entry]=agcEntry
    agcEntry['ref]=strcat("gate:" agcRequest) agcEntry['task]=agcTask agcEntry['mode]=agcMode
    agcEntry['lib]=agcLib agcEntry['cell]=agcCell agcEntry['gate]=agcGate
    agcEntry['digest]=agcDigest agcEntry['symbolPorts]=agcPorts agcEntry['stage]="preflight"
    aiGateCircuits[agcEntry['ref]]=agcEntry
    agcResult=errset(progn(
      unless(and(ddGetObj(agcLib) ddIsObjWritable(ddGetObj(agcLib))) error("target library missing/read-only"))
      when(or(ddGetObj(agcLib agcCell) ddGetObj(agcLib strcat(agcCell "_tb")))
        error("target DUT or testbench cell already exists; never overwrite"))
      unless(and(ddGetObj("gpdk045") simplifyFilename(ddGetObjReadPath(ddGetObj("gpdk045")))==simplifyFilename(agcPdk))
        error("loaded gpdk045 path differs from qualified profile; use separate session"))
      when(and(agcMode=="foreground" !aiCircuitGuiP()) error("foreground unavailable"))
      aiGateBuildView(agcEntry agcCell "schematic" "schematic" agcDut)
      aiGateBuildView(agcEntry agcCell "symbol" "schematicSymbol" nil)
      aiGateBuildView(agcEntry strcat(agcCell "_tb") "schematic" "schematic" agcTb)
      agcEntry['stage]="saved_readback"
      aiGateValidate(agcEntry)
      ; schVIC checks the saved DUT against the new symbol without saving user data.
      unwindProtect(progn(
        agcRead=dbOpenCellViewByType(agcLib agcCell "schematic" "" "r")
        agcEntry['interfaceCheck]=schVIC(agcRead)
        unless(equal(agcEntry['interfaceCheck] list(0 0)) error("symbol/schematic interface check failed")))
        when(agcRead dbClose(agcRead)))
      agcEntry['saved]=t agcEntry['stage]="dut_symbol_tb_saved" t) t)
    unless(agcResult agcEntry['error]=strcat("Failed at " agcEntry['stage]
      "; partial target retained. Inspect CIW; identical request will not rebuild."))
    return(aiGateReply(agcEntry))))
