; Read-only invariants; no Check & Save during inspection of user edits.
procedure(aiGateVerifyPorts(agvCv agvPorts)
  prog((agvTerm)
    unless(length(agvCv~>terminals)==length(agvPorts) error("port count changed"))
    foreach(agvPort agvPorts
      agvTerm=car(setof(agvT agvCv~>terminals agvT~>name==car(agvPort)))
      unless(and(agvTerm agvTerm~>direction==cadr(agvPort) agvTerm~>net~>name==car(agvPort))
        error("port name/direction/net mismatch: %s" car(agvPort))))
    return(t)))

procedure(aiGateVerifySchematic(agvCv agvSpec)
  prog((agvInst agvTerm)
    unless(length(agvCv~>instances)==length(car(agvSpec))+length(cadr(agvSpec))
      error("instance count mismatch"))
    aiGateVerifyPorts(agvCv cadr(agvSpec))
    foreach(agvDev car(agvSpec)
      agvInst=dbFindAnyInstByName(agvCv car(agvDev))
      unless(and(agvInst agvInst~>libName==cadr(agvDev) agvInst~>cellName==caddr(agvDev)
        agvInst~>viewName=="symbol") error("device master mismatch"))
      unless(length(agvInst~>instTerms)==length(nth(6 agvDev)) error("device terminal count mismatch"))
      foreach(agvConn nth(6 agvDev)
        agvTerm=car(setof(agvT agvInst~>instTerms agvT~>name==car(agvConn)))
        unless(and(agvTerm agvTerm~>net~>name==cadr(agvConn))
          error("connection mismatch %s.%s: expected %s, got %L"
            car(agvDev) car(agvConn) cadr(agvConn) agvTerm~>net~>name))))
    return(t)))

procedure(aiGateSnapshot(agsCv)
  list(aiRcGeometrySnapshot(agsCv)
    sort(foreach(mapcar agsInst agsCv~>instances
      sprintf(nil "%L" list(agsInst~>name aiGateParams(agsInst)))) 'alphalessp)
    sort(foreach(mapcar agsTerm agsCv~>terminals
      sprintf(nil "%L" list(agsTerm~>name agsTerm~>direction agsTerm~>net~>name))) 'alphalessp)))

procedure(aiGateValidate(agvEntry)
  let((agvCv agvTarget agvWindow)
    foreach(agvRow agvEntry['views]
      agvTarget=car(agvRow) agvWindow=nth(3 agvRow)
      when(agvEntry['mode]=="foreground"
        unless(and(member(agvWindow hiGetWindowList('all))
          aiCircuitCvMatches(geGetWindowCellView(agvWindow) agvTarget)
          geGetWindowCellView(agvWindow)==geGetEditCellView(agvWindow))
          error("foreground target closed/rebound/EIP")))
      agvCv=nil
      unwindProtect(progn(
        agvCv=dbOpenCellViewByType(car(agvTarget) cadr(agvTarget) caddr(agvTarget) "" "r")
        unless(agvCv error("saved view missing"))
        when(dbIsCellViewModified(agvCv) error("view has unsaved changes"))
        unless(equal(aiGateSnapshot(agvCv) cadr(agvRow)) error("saved geometry/CDF/interface changed"))
        if(caddr(agvTarget)=="symbol" then aiGateVerifyPorts(agvCv agvEntry['symbolPorts])
          else aiGateVerifySchematic(agvCv nth(2 agvRow))))
        when(agvCv dbClose(agvCv))))
    t))
