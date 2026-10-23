; Bounded symbol wire acceptance: one background SicoTest instance, escape stubs
; drawn at caller-supplied coordinates, connectivity and saved read-back.
; Verification only: the target must be a new SicoTest schematic and no PDK or
; user library is ever written. Window opening is never requested.

procedure(aiPdkSymbolWireRevision() "20261010.ai.pdk.symbolwire.v1")

procedure(aiPdkSymbolWireProbe(appLib appCell appProbeCell appX appY appOrient appWires)
  let((pwMaster pwCv pwInst pwCheck pwSegs pwRows pwTerms pwWires pwNets pwSaved pwRead
       pwResult pwStage pwDetail pwAfter)
    pwStage="open_master"
    pwDetail=nil
    pwResult=errset(unwindProtect(progn(
      pwMaster=dbOpenCellViewByType(appLib appCell "symbol" "" "r")
      unless(pwMaster error("observed symbol is unavailable"))
      pwStage="test_workspace"
      aiTestWorkspaceLibrary()
      pwStage="create_view"
      when(ddGetObj("SicoTest" appProbeCell "schematic") error("probe target exists; never overwrite"))
      pwCv=dbOpenCellViewByType("SicoTest" appProbeCell "schematic" "schematic" "a")
      unless(pwCv error("probe view creation failed"))
      unless(and(!pwCv~>instances !pwCv~>shapes !pwCv~>terminals pwCv~>mode!="r")
        error("probe target not empty/writable"))
      pwStage="create_instance"
      pwInst=schCreateInst(pwCv pwMaster "PRB0" list(appX appY) appOrient)
      unless(and(pwInst pwInst~>cellView==pwCv pwInst~>master==pwMaster pwInst~>name=="PRB0"
        length(pwCv~>instances)==1) error("instance creation failed"))
      pwStage="draw_wires"
      pwWires=nil
      pwRows=appWires
      while(pwRows
        let((pwTerm pwX1 pwY1 pwX2 pwY2 pwPoints)
          pwTerm=car(pwRows) pwX1=cadr(pwRows) pwY1=caddr(pwRows)
          pwX2=cadddr(pwRows) pwY2=nth(4 pwRows)
          unless(and(stringp(pwTerm) numberp(pwX1) numberp(pwY1) numberp(pwX2) numberp(pwY2))
            error("wire row requires term and two points"))
          pwSegs=car(errset(schCreateWire(pwCv "draw" "full"
            list(list(pwX1 pwY1) list(pwX2 pwY2)) 0.0625 0.0625 0.0) t))
          unless(pwSegs error(strcat("wire creation failed for " pwTerm)))
          pwPoints=nil
          foreach(pwSeg pwSegs
            foreach(pwPoint pwSeg~>points
              pwPoints=tconc(pwPoints list(car(pwPoint) cadr(pwPoint)))))
          pwPoints=when(pwPoints car(pwPoints))
          pwWires=cons(list(pwTerm pwX1 pwY1 pwX2 pwY2 pwPoints) pwWires))
        pwRows=nthcdr(5 pwRows))
      pwStage="check"
      pwCheck=schCheck(pwCv)
      pwStage="read_terms"
      pwTerms=nil
      foreach(pwTermDef pwMaster~>terminals
        let((pwBox pwLower pwUpper)
          foreach(pwPin pwTermDef~>pins
            when(pwPin~>fig
              pwBox=if(pwBox
                list(list(min(caar(pwBox) caar(pwPin~>fig~>bBox)) min(cadar(pwBox) cadar(pwPin~>fig~>bBox)))
                  list(max(caadr(pwBox) caadr(pwPin~>fig~>bBox)) max(cadadr(pwBox) cadadr(pwPin~>fig~>bBox))))
                pwPin~>fig~>bBox)))
          when(pwBox
            pwLower=dbTransformPoint(car(pwBox) pwInst~>transform)
            pwUpper=dbTransformPoint(cadr(pwBox) pwInst~>transform)
            pwTerms=cons(list(pwTermDef~>name
              (car(pwLower)+car(pwUpper))/2.0 (cadr(pwLower)+cadr(pwUpper))/2.0
              pwTermDef~>direction) pwTerms))))
      pwTerms=reverse(pwTerms)
      pwNets=nil
      foreach(pwIt pwInst~>instTerms
        pwNets=cons(list(pwIt~>name
          if(pwIt~>net if(pwIt~>net~>name pwIt~>net~>name "unnamed") nil)) pwNets))
      pwNets=reverse(pwNets)
      pwStage="save"
      unless(dbSave(pwCv) error("probe save failed"))
      pwSaved=t
      dbClose(pwCv) pwCv=nil
      pwStage="saved_readback"
      pwCv=dbOpenCellViewByType("SicoTest" appProbeCell "schematic" "" "r")
      unless(pwCv error("saved probe reopen failed"))
      pwAfter=nil
      pwInst=dbFindAnyInstByName(pwCv "PRB0")
      unless(pwInst error("saved probe instance missing"))
      foreach(pwIt pwInst~>instTerms
        pwAfter=cons(list(pwIt~>name
          if(pwIt~>net if(pwIt~>net~>name pwIt~>net~>name "unnamed") nil)) pwAfter))
      pwAfter=reverse(pwAfter)
      pwStage="done") progn(
        when(and(pwCv pwSaved) dbClose(pwCv))
        when(pwMaster dbClose(pwMaster)))) t)
    if(pwResult
      aiCircuitObject(list(list("ok" aiCircuitBool(t))
        list("library" appLib) list("cell" appCell)
        list("probe_target" list("SicoTest" appProbeCell "schematic"))
        list("instance" "PRB0") list("instance_xy" list(appX appY)) list("orientation" appOrient)
        list("terminals" pwTerms) list("wires" reverse(pwWires)) list("nets" pwNets)
        list("saved_nets" pwAfter) list("check" pwCheck) list("saved" aiCircuitBool(pwSaved))
        list("extension_ref" "pdk:symbol-wire-probe:v1")))
      aiCircuitObject(list(list("ok" 'jsonFalse) list("stage" pwStage)
        list("message" if(pwDetail pwDetail aiCrErrorText())))))))
