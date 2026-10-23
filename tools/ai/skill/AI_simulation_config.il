; Config inputs are retained, verified creation records, not arbitrary HDB targets.
procedure(aiSimConfigEntry(ascTest)
  prog((ascEntry ascTarget)
    ascEntry=aiConfigEntries[aiSimConfigRef(ascTest)] ascTarget=cadr(ascTest)
    unless(and(ascEntry ascEntry['saved] !ascEntry['error]
      equal(ascEntry['target] ascTarget)) error("Config reference missing/incomplete or target mismatch"))
    unless(and(equal(nth(7 ascTest) ascEntry['switches]) equal(nth(8 ascTest) ascEntry['stops]))
      error("test switch/stop lists must match Config"))
    unless(equal(aiConfigRead(car(ascTarget) cadr(ascTarget) caddr(ascTarget)) ascEntry['snapshot])
      error("Config or top changed since creation"))
    return(ascEntry)))
procedure(aiSimConfigWalk(acwVector acwTarget acwPath acwAncestors acwBudget)
  prog((acwCv acwRows acwBound acwPushed acwStop acwChild acwName acwMaster acwInterface acwResult acwFailure)
    when(or(length(acwPath)>8 member(acwTarget acwAncestors)) error("Config recursion/depth limit"))
    unwindProtect(progn(acwResult=errset(progn(
      acwCv=dbOpenCellViewByType(car(acwTarget) cadr(acwTarget) caddr(acwTarget) "" "r")
      unless(and(acwCv acwCv~>cellViewType=="schematic" !dbIsCellViewModified(acwCv)
        aiSimSourceReadyP(acwCv))
        error("bound schematic requires saved data without error markers: %L" acwTarget))
      acwRows=list(list(acwPath acwTarget simplifyFilename(ddGetObjReadPath(ddGetObj(car(acwTarget))))
        aiSimSourceSnapshot(acwCv)))
      foreach(acwInst sort(copy(acwCv~>instances) 'aiSimConfigInstLess)
        ; schCreatePin's graphical instance is represented by the parent terminal snapshot.
        unless(acwInst~>pin
          acwBudget['count]=acwBudget['count]+1
          when(acwBudget['count]>512 error("Config traversal exceeds 512 instances"))
          acwName=acwInst~>name acwChild=append(acwPath list(acwName))
          acwBound=hdbBind(acwVector acwInst~>libName acwInst~>cellName acwInst~>viewName acwName t t nil)
          unless(and(acwBound car(acwBound) cadr(acwBound) caddr(acwBound)
            apply('ddGetObj list(car(acwBound) cadr(acwBound) caddr(acwBound))))
            error("unresolved Config instance: %L" acwChild))
          acwBound=list(car(acwBound) cadr(acwBound) caddr(acwBound))
          unless(hdbPushCell(acwVector acwName car(acwBound) cadr(acwBound) caddr(acwBound)) error("Config push failed"))
          acwPushed=t acwStop=hdbIsAtStopPoint(acwVector)
          acwRows=append(acwRows list(list(acwChild "binding" acwBound when(acwStop t)
            simplifyFilename(ddGetObjReadPath(ddGetObj(car(acwBound)))))))
          if(acwStop then
            unless(caddr(acwBound)=="spectre" error("only Spectre primitive stop views supported"))
          else
            acwMaster=dbOpenCellViewByType(car(acwBound) cadr(acwBound) caddr(acwBound) "" "r")
            unless(and(acwMaster acwMaster~>cellViewType=="schematic") error("non-schematic/sub-config binding unsupported"))
            acwInterface=sort(foreach(mapcar acwTerm acwInst~>master~>terminals
              sprintf(nil "%L" list(acwTerm~>name acwTerm~>direction))) 'alphalessp)
            unless(equal(acwInterface sort(foreach(mapcar acwTerm acwMaster~>terminals
              sprintf(nil "%L" list(acwTerm~>name acwTerm~>direction))) 'alphalessp)) error("resolved interface differs"))
            dbClose(acwMaster) acwMaster=nil
            acwRows=append(acwRows aiSimConfigWalk(acwVector acwBound acwChild cons(acwTarget acwAncestors) acwBudget)))
          unless(hdbPopCell(acwVector) error("Config pop failed")) acwPushed=nil))
      t) t)
      unless(acwResult acwFailure=aiSimFailText()))
      progn(when(acwPushed hdbPopCell(acwVector)) when(acwMaster dbClose(acwMaster)) when(acwCv dbClose(acwCv))))
    unless(acwResult error("%s" acwFailure))
    return(acwRows)))
procedure(aiSimConfigInstLess(aciA aciB) alphalessp(aciA~>name aciB~>name))
procedure(aiSimConfigSource(acsTest)
  prog((acsEntry acsTarget acsH acsPv acsRows acsBudget acsResult acsFailure)
    acsEntry=aiSimConfigEntry(acsTest) acsTarget=acsEntry['target]
    acsBudget=makeTable("configTraversalBudget" nil) acsBudget['count]=0
    unwindProtect(progn(acsResult=errset(progn(
      acsH=hdbOpen(car(acsTarget) cadr(acsTarget) caddr(acsTarget) "r")
      unless(and(acsH !hdbIsConfigModified(acsH) !hdbConfigHasOccRules(acsH))
        error("Config modified or occurrence rules unsupported"))
      acsPv=hdbCreatePathVector(acsH) unless(acsPv error("Config path vector unavailable"))
      acsRows=aiSimConfigWalk(acsPv acsEntry['top] nil nil acsBudget)) t)
      unless(acsResult acsFailure=aiSimFailText()))
      progn(when(acsPv hdbDestroyPathVector(acsPv)) when(acsH hdbClose(acsH))))
    unless(acsResult error("%s" acsFailure))
    return(list("config" aiSimConfigRef(acsTest) acsTarget acsEntry['snapshot] acsRows))))
procedure(aiSimConfigSummary(acmEntry)
  prog((acmRows acmSource acmTraversal acmViews acmBindings)
    foreach(acmTest nth(3 acmEntry['payload])
      when(aiSimConfigRef(acmTest)
        acmSource=car(setof(acmS acmEntry['sources] and(car(acmS)=="config" cadr(acmS)==aiSimConfigRef(acmTest))))
        when(acmSource
          acmTraversal=nth(4 acmSource) acmViews=nil acmBindings=nil
          foreach(acmRow acmTraversal
            if(cadr(acmRow)=="binding" then
              acmBindings=cons(acmRow acmBindings)
            else acmViews=cons(list(car(acmRow) cadr(acmRow) caddr(acmRow)) acmViews)))
          acmRows=cons(aiCircuitObject(list(list("test" car(acmTest)) list("config_ref" aiSimConfigRef(acmTest))
            list("target" cadr(acmTest)) list("schematic_sources" reverse(acmViews))
            list("binding_columns" list("instance_path" "kind" "target" "stop" "library_path"))
            list("effective_bindings" reverse(acmBindings))
            list("scope" "bounded_schematic_hierarchy_to_spectre_stops"))) acmRows))))
    return(reverse(acmRows))))
