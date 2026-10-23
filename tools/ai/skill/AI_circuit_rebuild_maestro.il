; Reuse the qualified reference lifecycle with explicit rebuilt design binding.
procedure(aiRebuildBindTests(arbSetup)
  let((arbEntry arbCell arbSession arbTestSession)
    arbEntry=aiRebuildEntries[arbSetup['rebuild]] arbSession=arbSetup['session]
    aiRebuildValidate(arbEntry)
    foreach(arbTest list("AC" "TRAN")
      arbCell=strcat(arbEntry['cell] if(arbTest=="AC" "_ac_tb" "_tran_tb"))
      unless(maeSetDesign(arbTest arbEntry['lib] arbCell "config" ?session arbSession) error("test rebind failed"))
      arbTestSession=maeGetTestSession(arbTest ?session arbSession)
      unless(and(asiGetDesignLibName(arbTestSession)==arbEntry['lib]
        asiGetDesignCellName(arbTestSession)==arbCell asiGetDesignViewName(arbTestSession)=="config")
        error("test design readback mismatch"))) t))

procedure(aiRebuildSimulationValidate(arvSetup)
  let((arvEntry arvTarget)
    arvEntry=aiRebuildEntries[arvSetup['rebuild]]
    unless(and(arvEntry arvEntry['saved] !arvEntry['error]) error("rebuild unavailable"))
    aiRebuildValidate(arvEntry)
    foreach(arvTest list("AC" "TRAN")
      unless(and(equal(cadr(assoc("modelFiles" maeGetEnvOption(arvTest ?session arvSetup['session])))
        list(list(strcat(arvSetup['root] "/models/spectre/gpdk045.scs") "mc")))
        cadr(assoc("includePath" maeGetEnvOption(arvTest ?session arvSetup['session])))==strcat(arvSetup['root] "/models/spectre"))
        error("rebuilt model paths differ")))
    unless(length(arvSetup['configs])==2 error("config snapshots missing"))
    foreach(arvConfig arvSetup['configs]
      arvTarget=car(arvConfig)
      unless(equal(aiConfigRead(car(arvTarget) cadr(arvTarget) caddr(arvTarget)) cadr(arvConfig))
        error("rebuild config changed"))) t))

procedure(aiRebuildMaestro(armRef armRequest armRoot)
  prog((armEntry armRecord armResult armCell armCfg armSetup armSource)
    armEntry=aiRebuildEntries[armRef]
    unless(and(armEntry armEntry['saved] !armEntry['error] armEntry['root]==armRoot)
      return(aiCircuitError("dut_not_saved" "Saved rebuild with matching model root required")))
    armResult=errset(aiRcReserve(strcat("rebuild-maestro:" armRequest) list(armRef armRoot)) nil)
    unless(armResult return(aiCircuitError("request_conflict_or_limit" "Maestro request conflict/limit")))
    armRecord=car(armResult) when(armRecord['reply] return(armRecord['reply]))
    armResult=errset(progn(
      aiRebuildValidate(armEntry)
      when(ddGetObj(armEntry['lib] strcat(armEntry['cell] "_sim")) error("simulation target exists"))
      foreach(armSuffix list("_ac_tb" "_tran_tb")
        when(ddGetObj(armEntry['lib] strcat(armEntry['cell] armSuffix) "config") error("config target exists")))
      foreach(armSuffix list("_ac_tb" "_tran_tb")
        armCell=strcat(armEntry['cell] armSuffix)
        aiConfigCreate(armEntry['task] strcat(armRequest armSuffix) armEntry['lib] armCell "config"
          list(armEntry['lib] armCell "schematic")
          list(list(strcat("I_" armEntry['cell] "_0") armEntry['lib] armEntry['cell] "schematic")))
        armCfg=aiConfigEntries[strcat("config:" armRequest armSuffix)]
        unless(and(armCfg armCfg['saved] !armCfg['error]) error("config failed")))
      armSource=armEntry['sourcePath]
      aiOpampCreate(armEntry['task] strcat("rebuild_" armRequest) armEntry['lib]
        strcat(armEntry['cell] "_sim") armRoot armSource armRef)
      armSetup=aiOpampEntries[strcat("opamp:rebuild_" armRequest)]
      unless(and(armSetup armSetup['saved] !armSetup['error]) error("Maestro failed"))
      armSetup['configs]=foreach(mapcar armSuffix list("_ac_tb" "_tran_tb")
        armCell=strcat(armEntry['cell] armSuffix)
        list(list(armEntry['lib] armCell "config") aiConfigRead(armEntry['lib] armCell "config")))
      aiOpampValidate(armSetup)
      armRecord['reply]=aiOpampReply(armSetup) t) t)
    unless(armResult
      when(armSetup armSetup['error]="Rebuild Maestro validation failed")
      armRecord['reply]=aiCircuitError("rebuild_maestro_failed" "Partial targets retained; inspect CIW; identical request will not rerun"))
    return(armRecord['reply])))

procedure(aiRebuildRun(arrRef arrRequest arrRoot)
  if(and(aiOpampEntries[arrRef] aiOpampEntries[arrRef]['rebuild]) then
    aiOpampRun(arrRef arrRequest arrRoot)
  else aiCircuitError("rebuild_setup_required" "Use a setup from create_opamp_rebuild_maestro")))
