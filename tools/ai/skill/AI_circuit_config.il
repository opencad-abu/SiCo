; New Spectre config only. All HDB refs/path vectors have explicit ownership.
unless(boundp('aiConfigEntries) aiConfigEntries=makeTable("aiConfigEntries" nil))
procedure(aiConfigRevision() "20261022.ai.config.v3")
procedure(aiConfigRowLess(aclA aclB) alphalessp(car(aclA) car(aclB)))

procedure(aiConfigDataSnapshot(asdCv)
  ; ADE can reformat effective instance CDF values as contexts load. Compare raw OA
  ; properties and direct connectivity here, not transient formatted CDF objects.
  list(
    sort(foreach(mapcar asdInst asdCv~>instances sprintf(nil "%L"
      list(asdInst~>name asdInst~>libName asdInst~>cellName asdInst~>viewName asdInst~>transform
        simplifyFilename(ddGetObjReadPath(ddGetObj(asdInst~>libName)))
        sort(foreach(mapcar asdProp asdInst~>prop sprintf(nil "%L"
          list(asdProp~>name asdProp~>valueType asdProp~>value))) 'alphalessp)
        sort(foreach(mapcar asdTerm asdInst~>instTerms sprintf(nil "%L"
          list(asdTerm~>name asdTerm~>net~>name))) 'alphalessp)))) 'alphalessp)
    sort(foreach(mapcar asdShape asdCv~>shapes sprintf(nil "%L"
      list(asdShape~>objType asdShape~>lpp asdShape~>bBox asdShape~>points asdShape~>theLabel))) 'alphalessp)
    sort(foreach(mapcar asdTerm asdCv~>terminals sprintf(nil "%L"
      list(asdTerm~>name asdTerm~>direction asdTerm~>net~>name))) 'alphalessp)
    ; ADE adds these two environment caches to a source CV during test binding.
    ; Their authoritative settings are checked through maeGetEnvOption/SimOption.
    sort(foreach(mapcar asdProp setof(asdP asdCv~>prop !member(asdP~>name list("spectre" "viewNameList"))) sprintf(nil "%L"
      list(asdProp~>name asdProp~>valueType asdProp~>value))) 'alphalessp)))

procedure(aiConfigRead(acrLib acrCell acrView)
  prog((acrH acrPv acrCv acrTop acrRows acrBinding acrResult)
    unwindProtect(progn(
      acrH=hdbOpen(acrLib acrCell acrView "r")
      unless(acrH error("config unavailable"))
      when(hdbIsConfigModified(acrH) error("config has unsaved changes"))
      acrTop=list(hdbGetTopLibName(acrH) hdbGetTopCellName(acrH) hdbGetTopViewName(acrH))
      acrCv=dbOpenCellViewByType(car(acrTop) cadr(acrTop) caddr(acrTop) "" "r")
      unless(and(acrCv acrCv~>cellViewType=="schematic") error("top schematic missing"))
      when(dbIsCellViewModified(acrCv) error("top schematic has unsaved changes"))
      when(length(acrCv~>instances)>256 error("direct instance limit exceeded"))
      acrPv=hdbCreatePathVector(acrH)
      unless(acrPv error("path vector failed"))
      foreach(acrInst setof(acrI acrCv~>instances !acrI~>pin)
        acrBinding=hdbBind(acrPv acrInst~>libName acrInst~>cellName acrInst~>viewName acrInst~>name t t nil)
        unless(and(acrBinding car(acrBinding) cadr(acrBinding) caddr(acrBinding)
          ddGetObj(car(acrBinding) cadr(acrBinding) caddr(acrBinding))) error("unresolved instance: %s" acrInst~>name))
        acrRows=cons(list(acrInst~>name acrInst~>libName acrInst~>cellName acrInst~>viewName
          car(acrBinding) cadr(acrBinding) caddr(acrBinding)) acrRows))
      acrRows=sort(acrRows 'aiConfigRowLess)
      acrResult=list(acrTop hdbGetDefaultViewListString(acrH) hdbGetDefaultStopListString(acrH)
        acrRows aiConfigDataSnapshot(acrCv)
        simplifyFilename(ddGetObjReadPath(ddGetObj(acrLib)))
        when(hdbConfigHasOccRules(acrH) t)))
      progn(when(acrPv hdbDestroyPathVector(acrPv)) when(acrCv dbClose(acrCv)) when(acrH hdbClose(acrH))))
    return(acrResult)))

procedure(aiConfigReply(acrEntry)
  aiCircuitObject(list(list("ok" aiCircuitBool(!acrEntry['error])) list("config_ref" acrEntry['ref])
    list("target" acrEntry['target]) list("top" acrEntry['top]) list("presentation" acrEntry['mode])
    list("stage" acrEntry['stage]) list("saved" aiCircuitBool(acrEntry['saved]))
    list("switch_views" acrEntry['switches]) list("stop_views" acrEntry['stops])
    list("binding_columns" list("instance" "master_lib" "master_cell" "master_view" "bound_lib" "bound_cell" "bound_view"))
    list("bindings" nth(3 acrEntry['snapshot])) list("hierarchy_complete" 'jsonFalse)
    list("binding_scope" "direct_instances") list("error" acrEntry['error]))))

procedure(aiConfigInterface(aciCv aciBindings)
  prog((aciInst aciMaster aciExpected aciActual)
    foreach(aciRule aciBindings
      aciInst=dbFindAnyInstByName(aciCv car(aciRule))
      unless(aciInst error("explicit binding instance missing: %s" car(aciRule)))
      unless(and(aciInst~>libName==cadr(aciRule) aciInst~>cellName==caddr(aciRule))
        error("schematic instance fixes lib/cell; only alternate views of its master are supported"))
      unwindProtect(progn(
        aciMaster=dbOpenCellViewByType(cadr(aciRule) caddr(aciRule) nth(3 aciRule) "" "r")
        unless(and(aciMaster aciMaster~>cellViewType=="schematic" !dbIsCellViewModified(aciMaster))
          error("bound schematic missing/modified"))
        aciExpected=sort(foreach(mapcar aciT aciInst~>master~>terminals
          sprintf(nil "%L" list(aciT~>name aciT~>direction))) 'alphalessp)
        aciActual=sort(foreach(mapcar aciT aciMaster~>terminals
          sprintf(nil "%L" list(aciT~>name aciT~>direction))) 'alphalessp)
        unless(equal(aciExpected aciActual) error("binding terminal interface mismatch")))
        when(aciMaster dbClose(aciMaster) aciMaster=nil)))
    return(t)))

procedure(aiConfigInspect(aciRef)
  prog((aciEntry aciResult aciTarget aciH aciCv)
    aciEntry=aiConfigEntries[aciRef]
    unless(and(aciEntry aciEntry['saved] !aciEntry['error])
      return(aiCircuitError("config_not_saved"
        "No saved config for this reference; create and save one with create_circuit_config first")))
    aciTarget=aciEntry['target]
    aciResult=errset(progn(
      when(aciEntry['mode]=="foreground"
        unless(and(member(aciEntry['window] hiGetWindowList('all))
          aiCircuitCvMatches(geGetWindowCellView(aciEntry['window]) aciEntry['top])
          geGetWindowCellView(aciEntry['window])==geGetEditCellView(aciEntry['window])) error("config window closed/rebound/EIP"))
        aciH=deGetConfigId(aciEntry['window])
        unless(and(aciH equal(list(hdbGetLibName(aciH) hdbGetCellName(aciH) hdbGetViewName(aciH)) aciTarget))
          error("window config binding changed")))
      unless(equal(aiConfigRead(car(aciTarget) cadr(aciTarget) caddr(aciTarget)) aciEntry['snapshot])
        error("config or top design changed"))
      unwindProtect(progn(
        aciCv=dbOpenCellViewByType(car(aciEntry['top]) cadr(aciEntry['top]) caddr(aciEntry['top]) "" "r")
        aiConfigInterface(aciCv aciEntry['explicitBindings])) when(aciCv dbClose(aciCv)))
      t) t)
    unless(aciResult return(aiCircuitError("config_changed" "Config/top/window differs; inspect CIW")))
    return(aiConfigReply(aciEntry))))

procedure(aiConfigCreate(accTask accRequest accLib accCell accView accTop accBindings
  @optional (accSwitches list("spectre" "schematic")) (accStops list("spectre")))
  prog((accMode accRecord accEntry accResult accH accCv accRead accRow accWindows)
    accMode=aiCircuitTasks[accTask]
    unless(accMode return(aiCircuitError("task_not_found" "Begin task with confirmed mode")))
    unless(and(aiCircuitTargetP(accLib accCell accView "schematic")
      !member(accLib list("gpdk045" "basic" "analogLib")) length(accTop)==3 length(accBindings)<=64)
      return(aiCircuitError("invalid_config" "Invalid config specification")))
    accResult=errset(aiRcReserve(strcat("config:" accRequest)
      list(accTask accLib accCell accView accTop accBindings accSwitches accStops)) nil)
    unless(accResult return(aiCircuitError("request_conflict_or_limit" "Config request conflict/limit")))
    accRecord=car(accResult)
    when(accRecord['entry] return(aiConfigReply(accRecord['entry])))
    accEntry=makeTable("aiConfigEntry" nil) accRecord['entry]=accEntry
    accEntry['ref]=strcat("config:" accRequest) aiConfigEntries[accEntry['ref]]=accEntry
    accEntry['target]=list(accLib accCell accView) accEntry['top]=accTop accEntry['mode]=accMode
    accEntry['explicitBindings]=accBindings accEntry['task]=accTask
    accEntry['switches]=accSwitches accEntry['stops]=accStops
    accEntry['stage]="preflight"
    accResult=errset(unwindProtect(progn(
      unless(and(ddGetObj(accLib accCell) ddIsObjWritable(ddGetObj(accLib accCell))) error("target cell missing/read-only"))
      when(ddGetObj(accLib accCell accView) error("config target already exists"))
      when(and(accMode=="foreground" !aiCircuitGuiP()) error("foreground unavailable"))
      unless(and(accSwitches accStops !member(caddr(accTop) accStops)
        forall(accStop accStops member(accStop accSwitches))) error("invalid Config lists"))
      accCv=dbOpenCellViewByType(car(accTop) cadr(accTop) caddr(accTop) "" "r")
      unless(and(accCv accCv~>cellViewType=="schematic" !dbIsCellViewModified(accCv)) error("saved top schematic required"))
      when(length(accCv~>instances)>256 error("direct instance limit exceeded"))
      aiConfigInterface(accCv accBindings)
      dbClose(accCv) accCv=nil
      accEntry['stage]="create_config"
      accH=hdbOpen(accLib accCell accView "w")
      unless(accH error("config creation failed"))
      unless(hdbSetTopCellViewName(accH car(accTop) cadr(accTop) caddr(accTop)) error("set top failed"))
      unless(hdbSetDefaultViewListString(accH buildString(accSwitches " ")) error("view list failed"))
      unless(hdbSetDefaultStopListString(accH buildString(accStops " ")) error("stop list failed"))
      foreach(accRule accBindings
        unless(hdbSetObjBindRule(accH list(append(accTop list(car(accRule))))
          list('hdbcBindingRule cdr(accRule))) error("explicit binding failed")))
      unless(hdbSave(accH) error("config save failed"))
      hdbClose(accH) accH=nil
      accEntry['stage]="saved_readback"
      accRead=aiConfigRead(accLib accCell accView)
      unless(and(equal(car(accRead) accTop) cadr(accRead)==buildString(accSwitches " ") caddr(accRead)==buildString(accStops " ")) error("config readback mismatch"))
      foreach(accRule accBindings
        accRow=assoc(car(accRule) nth(3 accRead))
        unless(and(accRow equal(nthcdr(4 accRow) cdr(accRule))) error("resolved binding differs from requested target")))
      accEntry['snapshot]=accRead
      when(accMode=="foreground"
        accEntry['stage]="foreground_handoff"
        accEntry['window]=aiCircuitNativeCall('deOpen
          list(list(nil 'libName car(accTop) 'cellName cadr(accTop) 'viewName caddr(accTop)
            'configL list(accLib accCell accView "r")) nil "r"))
        unless(and(accEntry['window] deGetConfigId(accEntry['window])) error("bound schematic window failed")))
      accEntry['saved]=t accEntry['stage]="config_saved" t)
      progn(when(accCv dbClose(accCv)) when(accH hdbClose(accH)))) t)
    unless(accResult accEntry['error]=strcat("Failed at " accEntry['stage] "; partial target retained; inspect CIW"))
    when(isCallable('sicoNativeOutput)
      aiCircuitNativeCall('sicoNativeOutput list(accEntry['window] accTop nil accEntry['saved])))
    return(aiConfigReply(accEntry))))
