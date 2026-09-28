unless(and(boundp('rceTermOrderFixtureVersion)
             rceTermOrderFixtureVersion=="20260911.v1")

procedure(rceTermTestAssert(value message)
  unless(value error("RCE_TEST: %s\n" message))
)

procedure(rceTermTestCell(cell @optional (withSymbol t) extra)
  let((rtcCv rtcView rtcPins rtcPin rtcMaster rtcIndex)
    foreach(rtcView if(withSymbol then list("schematic" "symbol") else list("schematic"))
      rtcCv=dbOpenCellViewByType("rce_term_probe" cell rtcView
        if(rtcView=="symbol" then "schematicSymbol" else "schematic") "w")
      rtcPins=if(and(extra rtcView=="symbol")
        then append(rtExpected list("EXTRA")) else rtExpected)
      rtcMaster=dbOpenCellViewByType("basic"
        if(rtcView=="symbol" then "sympin" else "iopin")
        if(rtcView=="symbol" then "symbolNN" else "symbol") "" "r")
      rtcIndex=0
      foreach(rtcPin rtcPins
        if(rtcView=="symbol" then
          schCreateSymbolPinWithLabel(?cellView rtcCv ?master rtcMaster
            ?name rtcPin ?direction "inputOutput" ?origin list(rtcIndex 0.0)
            ?orient "R0" ?flatten t)
        else
          schCreatePin(rtcCv rtcMaster rtcPin "inputOutput" nil list(rtcIndex 0.0) "R0"))
        rtcIndex=rtcIndex+1)
      dbClose(rtcMaster)
      schEditPinOrder(rtcCv if(rtcView=="symbol" then reverse(rtcPins) else rtcPins) nil)
      dbSave(rtcCv)
      dbClose(rtcCv)))
)

procedure(rceTermTestMismatch(cell)
  almSetTerminalList("rce_term_probe" cell reverse(rtExpected) ?tool "auCdl")
  almSetTerminalList("rce_term_probe" cell list("VDD!" "Z" "A<1:0>") ?tool "spectre")
)

procedure(rceTermTestSnapshot(cell)
  rceTermOrderSnapshot("rce_term_probe" cell "schematic")
)

procedure(rceTermTestPrompt(data)
  rtPrompts=rtPrompts+1
  rtPromptData=data
  when(rtAction=='change
    almSetTerminalList(data['lib] data['cell] rtExpected ?tool "spectre"))
  if(rtAction=='change then 'update else rtAction)
)

procedure(rceTermTestRead(lib cell view)
  rtReadCount=rtReadCount+1
  when(rtReadCount==3 error("RCE: injected post-update verification failure\n"))
  apply(rtOriginalSnapshot list(lib cell view))
)

procedure(rceTermTestMain()
  prog((form before after held refs data view runRoot row)
    rceTermOrderVersion="old"
    putd('rceTermOrderRevision lambda(() "old"))
    putd('rceCheckOaTermOrder lambda((form) error("RCE_TEST: stale preflight")))
    load(strcat(getShellEnvVar("CAD_HOME") "/tools/rce/skill++/RCE.ils"))
    rceTermTestAssert(rceTermOrderRevision()=="20260911.view.term.order.v2"
      "Loader did not refresh the OA terminal-order helper")
    rtExpected=list("Z" "A<1:0>" "VDD!")
    ddCreateLib("rce_term_probe" strcat(pwd() "/rce_term_probe"))
    rceTermTestCell("top")
    rceTermTestMismatch("top")
    data=cdfGetBaseCellCDF(ddGetObj("rce_term_probe" "top"))
    data->simInfo->spectre->modelName="keepModel"
    cdfSaveCDF(data)
    before=rceTermTestSnapshot("top")
    rceTermTestAssert(rceTermOrderMismatchP(before) "Missing initial mismatch")

    rtOriginalPrompt=getd('rceTermOrderPrompt)
    putd('rceTermOrderPrompt getd('rceTermTestPrompt))
    rtOriginalMessage=getd('GUI_messageApp)
    putd('GUI_messageApp lambda((severity title message) rtLastMessage=message t))
    rtPrompts=0
    cadDisplayRceForm()
    form=rceForm
    form~>inpType~>value="OA"
    form~>schLib~>value="rce_term_probe"
    form~>schCell~>value="top"
    form~>schView~>value="schematic"
    rtAction='cancel
    rceTermTestAssert(!rceCheckOaTermOrder(form) "Cancel must stop")
    rceTermTestAssert(rceTermOrderUnchangedP(before rceTermTestSnapshot("top")) "Cancel wrote data")
    rtAction='continue
    rceTermTestAssert(rceCheckOaTermOrder(form) "Continue must proceed")
    rceTermTestAssert(rceTermOrderUnchangedP(before rceTermTestSnapshot("top")) "Continue wrote data")

    held=dbOpenCellViewByType("rce_term_probe" "top" "symbol" "" "r")
    refs=held->refCount
    rtAction='update
    rceTermTestAssert(rceCheckOaTermOrder(form) "Update failed")
    rceTermTestAssert(and(held->refCount==refs held->mode=="r" !dbIsCellViewModified(held))
      "Update changed the existing symbol handle")
    dbClose(held)
    rceTermTestAssert(!rceTermOrderMismatchP(rceTermTestSnapshot("top")) "Orders still differ")
    rceTermTestAssert(cdfGetBaseCellCDF(ddGetObj("rce_term_probe" "top"))->simInfo->spectre->modelName=="keepModel"
      "Update changed unrelated CDF metadata")
    refs=rtPrompts
    rceTermTestAssert(rceCheckOaTermOrder(form) "Aligned input rejected")
    rceTermTestAssert(rtPrompts==refs "Aligned input prompted")

    rceTermTestCell("no_symbol" nil)
    form~>schCell~>value="no_symbol"
    rceTermTestAssert(rceCheckOaTermOrder(form) "Missing CDF creation failed")
    rceTermTestAssert(!ddGetObj("rce_term_probe" "no_symbol" "symbol") "Created a missing symbol")
    rceTermTestAssert(!rceTermOrderMismatchP(rceTermTestSnapshot("no_symbol")) "Missing CDF still absent")

    form~>schCell~>value="top"
    almSetTerminalList("rce_term_probe" "top" reverse(rtExpected) ?view "schematic")
    almSetTerminalList("rce_term_probe" "top" reverse(rtExpected) ?view "symbol")
    before=rceTermTestSnapshot("top")
    rceTermTestAssert(rceTermOrderMismatchP(before) "View-specific CDF mismatch missed")
    rtAction='cancel
    rceTermTestAssert(!rceCheckOaTermOrder(form) "View-specific Cancel must stop")
    rceTermTestAssert(rceTermOrderUnchangedP(before rceTermTestSnapshot("top")) "Cancel changed view CDF")
    rtOriginalSnapshot=getd('rceTermOrderSnapshot)
    rtReadCount=0
    putd('rceTermOrderSnapshot getd('rceTermTestRead))
    rtAction='update
    rceTermTestAssert(!rceCheckOaTermOrder(form) "View-specific failed verification accepted")
    putd('rceTermOrderSnapshot rtOriginalSnapshot)
    rceTermTestAssert(rceTermOrderUnchangedP(before rceTermTestSnapshot("top")) "View-specific rollback failed")
    rceTermTestAssert(rceCheckOaTermOrder(form) "View-specific CDF update failed")
    foreach(view list("schematic" "symbol")
      rceTermTestAssert(equal(almGetTerminalList("rce_term_probe" "top" ?view view) rtExpected)
        "View-specific order still differs"))

    rceTermTestCell("extra" t t)
    rceTermTestMismatch("extra")
    form~>schCell~>value="extra"
    before=rceTermTestSnapshot("extra")
    rceTermTestAssert(before['reason] "Different terminal sets must block update")
    rceTermTestAssert(!rceCheckOaTermOrder(form) "Forced update accepted different terminal sets")
    rceTermTestAssert(rceTermOrderUnchangedP(before rceTermTestSnapshot("extra")) "Different terminal sets wrote data")

    form~>schCell~>value="top"
    foreach(view list("schematic" "symbol")
      held=dbOpenCellViewByType("rce_term_probe" "top" view "" "a")
      dbReplaceProp(held "termOrderProbeUnsaved" "string" "user edit")
      refs=held->refCount
      rtAction='cancel
      rceTermTestAssert(!rceCheckOaTermOrder(form) "Unsaved input was accepted")
      rceTermTestAssert(and(dbIsCellViewModified(held) held->refCount==refs
        held->termOrderProbeUnsaved=="user edit") "Unsaved edits were saved or discarded")
      dbSave(held)
      dbClose(held))

    rceTermTestMismatch("top")
    held=dbOpenCellViewByType("rce_term_probe" "top" "symbol" "" "a")
    schEditPinOrder(held reverse(rtExpected) nil) dbSave(held) dbClose(held)
    before=rceTermTestSnapshot("top")
    rtOriginalSnapshot=getd('rceTermOrderSnapshot)
    rtReadCount=0
    putd('rceTermOrderSnapshot getd('rceTermTestRead))
    rtAction='update
    rceTermTestAssert(!rceCheckOaTermOrder(form) "Failed verification reported success")
    putd('rceTermOrderSnapshot rtOriginalSnapshot)
    rceTermTestAssert(rceTermOrderUnchangedP(before rceTermTestSnapshot("top")) "Failed update was not restored")

    rtAction='change
    rceTermTestAssert(!rceCheckOaTermOrder(form) "Stale prompt was applied")
    after=rceTermTestSnapshot("top")
    rceTermTestAssert(equal(cadr(assoc("spectre" after['orders])) rtExpected) "Concurrent change overwritten")
    rceTermTestAssert(equal(before['symbol] after['symbol]) "Stale prompt changed symbol")
    rceTermTestMismatch("top")

    ; Run, Run & Close and multi-cell config share this preflight before launch.
    runRoot=strcat(pwd() "/runs")
    form~>outDirPath~>value=runRoot
    form~>layLib~>value="rce_term_probe"
    form~>layCell~>value="top"
    form~>layView~>value="layout"
    form~>rcTechDirPath~>value=strcat(pwd() "/tech/Typ")
    rceRcTechDirPathCB(form)
    form~>createNetlistView~>value=nil
    rtAction='cancel
    refs=rtPrompts
    rceRunCB(form)
    rceRunCloseCB(form)
    rceTermTestAssert(and(rtPrompts==refs+2 hiIsFormDisplayed(form)) "Run cancellation bypassed preflight")
    row=cadBatchDesignValues(form)
    rceTermTestAssert(!rceBatchSpec(form row "probe" 1) "Batch cancellation generated a spec")
    rceTermTestAssert(!isFile(strcat(runRoot "/rce_term_probe.top/rce.toml")) "Cancel wrote config")
    rtAction='update
    rceTermTestAssert(rcePrint(form) "Updated OA input could not generate config")

    refs=rtPrompts
    form~>inpType~>value="CDL+GDS"
    form~>schCell~>value="missing"
    rceTermTestAssert(rceCheckOaTermOrder(form) "Non-OA input was inspected")
    rceTermTestAssert(refs==rtPrompts "Non-OA input prompted")
    putd('rceTermOrderPrompt rtOriginalPrompt)
    putd('GUI_messageApp rtOriginalMessage)
    hiFormClose(form) hiDeleteForm(form)
    printf("RCE_TERM_ORDER_BEHAVIOR_OK\n")
    return(t)
  )
)

rceTermOrderFixtureVersion="20260911.v1"
)
