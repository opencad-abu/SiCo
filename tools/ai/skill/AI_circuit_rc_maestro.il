; New RC setup only. GUI session is borrowed after the bootstrap save/open handoff.
procedure(aiRcSetupSnapshot(assSession)
  let((assTest)
    assTest=maeGetTestSession("RC" ?session assSession)
  list(list(asiGetDesignLibName(assTest) asiGetDesignCellName(assTest)
      asiGetDesignViewName(assTest) asiGetSimName(assTest))
    maeGetEnvOption("RC" ?session assSession)
    maeGetSetup(?typeName "tests" ?session assSession)
    maeGetSetup(?typeName "tests" ?enabled t ?session assSession)
    maeGetSetup(?typeName "corners" ?session assSession)
    maeGetSetup(?typeName "variables" ?session assSession)
    maeGetEnabledAnalysis("RC" ?session assSession)
    maeGetAnalysis("RC" "dc" ?session assSession)
    maeGetAnalysis("RC" "tran" ?session assSession)
    foreach(mapcar assOutput maeGetTestOutputs("RC" ?session assSession)
      list(assOutput->name copy(assOutput->expression) assOutput->signal
        assOutput->plot assOutput->save assOutput->evalType)))))

procedure(aiRcMaestro(armRef armRequest)
  prog((armEntry armSession armWindow armRecord armResult armTau armOwned armResponse armStep)
    armStep="validate_circuit"
    armResult=errset(progn(
      armEntry=aiRcRequireEntry(armRef)
      armRecord=aiRcReserve(strcat("setup:" armRequest) list(armRef))
      t) nil)
    unless(armResult return(aiCircuitError("invalid_context_or_request" "Circuit/request validation failed")))
    when(armRecord['response] return(armRecord['response]))
    ; Reserve an error before side effects; an interrupted request cannot recreate anything.
    armRecord['response]=aiCircuitError("setup_incomplete" "Setup request started; inspect target before recovery")
    armResult=errset(unwindProtect(progn(
      aiRcValidateSaved(armEntry)
      when(or(armEntry['session] ddGetObj(armEntry['lib] armEntry['cell] "maestro"))
        error("Maestro target already exists"))
      armStep="create_setup"
      armSession=maeOpenSetup(armEntry['lib] armEntry['cell] "maestro" ?application "Assembler" ?mode "a")
      unless(and(armSession !axlIsSessionReadOnly(armSession)) error("setup not writable"))
      armOwned=t armEntry['session]=armSession
      unless(maeCreateTest("RC" ?lib armEntry['lib] ?cell armEntry['cell] ?view "schematic"
        ?simulator "spectre" ?session armSession) error("create test failed"))
      armTau=armEntry['r]*armEntry['c]
      armStep="configure_analyses"
      unless(maeSetAnalysis("RC" "dc" ?enable t ?options list(list("saveOppoint" t))
        ?session armSession) error("DC configuration failed"))
      unless(maeSetAnalysis("RC" "tran" ?enable t ?options list(
        list("stop" sprintf(nil "%.12g" 8*armTau)) list("errpreset" "conservative")
        list("maxstep" sprintf(nil "%.12g" armTau/100))) ?session armSession) error("TRAN configuration failed"))
      armStep="configure_outputs"
      unless(maeAddOutput("OUT" "RC" ?outputType "net" ?signalName "/OUT" ?plot t ?save t
        ?session armSession) error("signal output failed"))
      unless(maeAddOutput("dc_out_v" "RC" ?outputType "point" ?expr "VDC(\"/OUT\")"
        ?plot t ?save t ?session armSession) error("DC scalar output failed"))
      unless(maeAddOutput("tran_tau_v" "RC" ?outputType "point"
        ?expr sprintf(nil "value(VT(\"/OUT\") %.12g)" 2.001*armTau)
        ?plot t ?save t ?session armSession) error("TRAN scalar output failed"))
      armStep="save_setup"
      unless(equal(maeGetSetup(?typeName "tests" ?session armSession) list("RC")) error("test roundtrip failed"))
      unless(and(member("dc" maeGetEnabledAnalysis("RC" ?session armSession))
        member("tran" maeGetEnabledAnalysis("RC" ?session armSession))) error("analysis roundtrip failed"))
      unless(maeSaveSetup(?session armSession) error("save setup failed"))
      when(armEntry['mode]=="foreground"
        armStep="foreground_handoff"
        unless(maeCloseSession(?session armSession) error("bootstrap session close failed"))
        armOwned=nil armSession=nil armEntry['session]=nil
        armWindow=deOpenCellView(armEntry['lib] armEntry['cell] "maestro" "maestro" nil "a")
        unless(armWindow error("Maestro window open failed"))
        aiCircuitPlaceRightHalf(armWindow)
        aiCircuitRaiseWindow(armWindow)
        armEntry['maestroWindow]=armWindow
        armSession=axlGetWindowSession(armWindow)
        unless(armSession error("GUI session not ready"))
        armEntry['session]=armSession
        ; Newly-created owned setup may be normalized on GUI load; save before handoff.
        unless(maeSaveSetup(?session armSession) error("GUI setup save failed")))
      armEntry['setupSnapshot]=aiRcSetupSnapshot(armSession)
      armEntry['maestroSaved]=t armEntry['stage]="maestro_saved"
      t)
      when(and(armOwned !armEntry['maestroSaved] armSession)
        when(car(errset(maeCloseSession(?session armSession ?forceClose t) nil))
          armEntry['session]=nil))) t)
    armResponse=if(armResult then aiRcReply(armEntry)
      else aiCircuitError("setup_failed" strcat("Failed at " armStep "; partial setup retained; inspect CIW") armRef))
    armRecord['response]=armResponse
    return(armResponse)))
