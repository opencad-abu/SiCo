; RCE OA input port-order preflight, based on the schematic pin order.

; A cached revision alone does not prove the helper definitions are present.
unless(and(boundp('rceTermOrderVersion)
             rceTermOrderVersion=="20260911.view.term.order.v2"
             isCallable('rceTermOrderRevision)
             rceTermOrderRevision()=="20260911.view.term.order.v2"
             isCallable('rceTermOrderSamePins)
             isCallable('rceTermOrderSnapshot)
             isCallable('rceTermOrderMismatchP)
             isCallable('rceTermOrderUnchangedP)
             isCallable('rceTermOrderPrompt)
             isCallable('rceTermOrderRestore)
             isCallable('rceTermOrderUpdate)
             isCallable('rceCheckOaTermOrder))

procedure(rceTermOrderSamePins(order terminals)
  and(length(order)==length(terminals)
      equal(sort(copy(order) 'alphalessp)
            sort(copy(terminals) 'alphalessp)))
)

procedure(rceTermOrderSnapshot(lib cell view)
  let((rtsData rtsSch rtsSym rtsCdf rtsTool rtsInfo rtsView rtsViews)
    rtsData=makeTable("rceTermOrderSnapshot" nil)
    rtsData['lib]=lib
    rtsData['cell]=cell
    rtsData['view]=view
    unwindProtect(
      progn(
        unless(ddGetObj(lib cell view)
          error("RCE: schematic view is missing: %s/%s/%s\n" lib cell view))
        rtsSch=dbOpenCellViewByType(lib cell view "" "r")
        unless(and(rtsSch rtsSch->cellViewType=="schematic")
          error("RCE: input must be a schematic cellview: %s/%s/%s\n" lib cell view))
        when(dbIsCellViewModified(rtsSch)
          error("RCE: save the schematic before checking term order: %s/%s/%s\n"
                lib cell view))
        rtsData['schematic]=schGetPinOrder(rtsSch)
        unless(rceTermOrderSamePins(rtsData['schematic] rtsSch~>terminals~>name)
          error("RCE: schematic pin order does not match its terminals: %s/%s/%s\n"
                lib cell view))
        rtsData['symbolExists]=and(ddGetObj(lib cell "symbol") t)
        when(rtsData['symbolExists]
          rtsSym=dbOpenCellViewByType(lib cell "symbol" "" "r")
          unless(and(rtsSym rtsSym->cellViewType=="schematicSymbol")
            error("RCE: cannot read symbol: %s/%s/symbol\n" lib cell))
          rtsData['symbol]=schGetPinOrder(rtsSym)
          rtsData['symbolPins]=rtsSym~>terminals~>name
          rtsData['symbolModified]=dbIsCellViewModified(rtsSym)
          cond(
            (rtsData['symbolModified]
              rtsData['reason]="Save the symbol before updating term order.")
            (!rceTermOrderSamePins(rtsData['schematic] rtsData['symbolPins])
              rtsData['reason]=strcat(
                "Schematic and symbol have different terminal names. "
                "Correct the symbol terminals before updating term order."))))
        rtsCdf=cdfGetBaseCellCDF(ddGetObj(lib cell))
        foreach(rtsTool list("auCdl" "spectre")
          rtsInfo=get(rtsCdf->simInfo concat(rtsTool))
          rtsData['baseOrders]=append1(rtsData['baseOrders]
            list(rtsTool copy(rtsInfo->termOrder)))
          rtsData['orders]=append1(rtsData['orders]
            list(rtsTool copy(almGetTerminalList(lib cell ?tool rtsTool)))))
        rtsViews=list(view)
        foreach(rtsView list("symbol" "auCdl" "spectre")
          when(and(!member(rtsView rtsViews) ddGetObj(lib cell rtsView))
            rtsViews=append1(rtsViews rtsView)))
        foreach(rtsView rtsViews
          when(almHasViewInformation(lib cell rtsView)
            rtsData['viewOrders]=append1(rtsData['viewOrders]
              list(rtsView copy(almGetTerminalList(lib cell ?view rtsView)))))
          foreach(rtsTool list("auCdl" "spectre")
            rtsData['effectiveOrders]=append1(rtsData['effectiveOrders]
              list(rtsView rtsTool copy(almGetTerminalList(
                lib cell ?view rtsView ?tool rtsTool))))))
        rtsData)
      progn(
        when(rtsSym dbClose(rtsSym))
        when(rtsSch dbClose(rtsSch))))
  )
)

procedure(rceTermOrderMismatchP(snapshot)
  let((rtmMismatch rtmEntry)
    rtmMismatch=or(snapshot['reason]
      and(snapshot['symbolExists]
          !equal(snapshot['schematic] snapshot['symbol])))
    foreach(rtmEntry snapshot['orders]
      unless(equal(snapshot['schematic] cadr(rtmEntry)) rtmMismatch=t))
    foreach(rtmEntry snapshot['effectiveOrders]
      unless(equal(snapshot['schematic] caddr(rtmEntry)) rtmMismatch=t))
    rtmMismatch
  )
)

procedure(rceTermOrderUnchangedP(before after)
  let((rtuSame rtuKey)
    rtuSame=t
    foreach(rtuKey list('lib 'cell 'view 'schematic 'symbolExists 'symbol
                        'symbolModified 'baseOrders 'orders 'viewOrders
                        'effectiveOrders 'reason)
      unless(equal(before[rtuKey] after[rtuKey]) rtuSame=nil))
    and(rtuSame rceTermOrderSamePins(before['symbolPins] after['symbolPins]))
  )
)

procedure(rceTermOrderPrompt(snapshot)
  let((rtpText rtpEntry rtpAnswer)
    rtpText=sprintf(nil
      "Terminal order mismatch: %s/%s\n\nSchematic (%s): %L\nSymbol: %s\n"
      snapshot['lib] snapshot['cell] snapshot['view] snapshot['schematic]
      if(snapshot['symbolExists] then sprintf(nil "%L" snapshot['symbol])
         else "No symbol view"))
    foreach(rtpEntry snapshot['orders]
      rtpText=strcat(rtpText sprintf(nil "%s CDF: %L\n"
                                    car(rtpEntry) cadr(rtpEntry))))
    foreach(rtpEntry snapshot['viewOrders]
      rtpText=strcat(rtpText sprintf(nil "View %s CDF: %L\n"
                                    car(rtpEntry) cadr(rtpEntry))))
    info(strcat("[RCE] " rtpText))
    if(snapshot['reason] then
      rtpText=strcat(rtpText "\n" snapshot['reason])
    else
      rtpText=strcat(rtpText
        "\nUpdate the existing symbol, auCdl/spectre CDF and listed view orders "
        "to match the schematic?"))
    rtpAnswer=hiDisplayAppDBox(
      ?name 'rceTermOrderQuestion ?dboxBanner "RCE: OA Terminal Order"
      ?dboxText rtpText ?dialogType hicWarningDialog ?dialogStyle 'modal
      ?buttonLayout 'UserDefined
      ?buttons if(snapshot['reason]
        then list("Continue Without Updating" "Cancel")
        else list("Update and Continue" "Continue Without Updating" "Cancel"))
      ?defaultButton if(snapshot['reason] then 2 else 3))
    if(snapshot['reason] then
      if(rtpAnswer==1 then 'continue else 'cancel)
    else
      case(rtpAnswer (1 'update) (2 'continue) (t 'cancel)))
  )
)

procedure(rceTermOrderRestore(snapshot symbolCv tools symbolTouched @optional views)
  let((rtrOk rtrTool rtrResult rtrVerify rtrView)
    rtrOk=t
    foreach(rtrTool tools
      rtrResult=errset(
        almSetTerminalList(snapshot['lib] snapshot['cell]
          cadr(assoc(rtrTool snapshot['baseOrders])) ?tool rtrTool) t)
      unless(rtrResult rtrOk=nil))
    foreach(rtrView views
      rtrResult=errset(
        almSetTerminalList(snapshot['lib] snapshot['cell]
          cadr(assoc(rtrView snapshot['viewOrders])) ?view rtrView) t)
      unless(rtrResult rtrOk=nil))
    when(and(symbolTouched symbolCv)
      rtrResult=errset(and(schEditPinOrder(symbolCv snapshot['symbol] nil)
                            dbSave(symbolCv)) t)
      unless(and(rtrResult car(rtrResult)) rtrOk=nil))
    errset(ddsRefresh(?cellview nil ?cdf t ?property nil ?techfile nil) t)
    rtrVerify=errset(rceTermOrderSnapshot(
      snapshot['lib] snapshot['cell] snapshot['view]) t)
    and(rtrOk rtrVerify
        rceTermOrderUnchangedP(snapshot car(rtrVerify)))
  )
)

procedure(rceTermOrderUpdate(snapshot)
  let((rtwResult rtwFresh rtwSym rtwOpen rtwMode rtwTools rtwEntry
        rtwSymbolTouched rtwRestored rtwVerify rtwViews)
    rtwResult=errset(
      unwindProtect(
        progn(
          rtwFresh=rceTermOrderSnapshot(
            snapshot['lib] snapshot['cell] snapshot['view])
          unless(rceTermOrderUnchangedP(snapshot rtwFresh)
            error("RCE: term order changed while the question was open; run again.\n"))
          when(snapshot['reason] error("RCE: %s\n" snapshot['reason]))
          when(and(snapshot['symbolExists]
                     !equal(snapshot['schematic] snapshot['symbol]))
            rtwOpen=dbFindOpenCellViewByName(snapshot['lib] snapshot['cell] "symbol")
            rtwMode=when(rtwOpen rtwOpen->mode)
            rtwSym=dbOpenCellViewByType(snapshot['lib] snapshot['cell] "symbol" "" "a")
            unless(and(rtwSym member(rtwSym->mode '("a" "w"))
                         !dbIsCellViewModified(rtwSym))
              error("RCE: symbol is not available for update.\n")))
          foreach(rtwEntry snapshot['orders]
            unless(equal(cadr(rtwEntry) snapshot['schematic])
              rtwTools=cons(car(rtwEntry) rtwTools)
              almSetTerminalList(snapshot['lib] snapshot['cell]
                snapshot['schematic] ?tool car(rtwEntry))
              unless(equal(almGetTerminalList(snapshot['lib] snapshot['cell]
                             ?tool car(rtwEntry)) snapshot['schematic])
                error("RCE: %s CDF term order update failed.\n" car(rtwEntry)))))
          foreach(rtwEntry snapshot['viewOrders]
            unless(equal(cadr(rtwEntry) snapshot['schematic])
              rtwViews=cons(car(rtwEntry) rtwViews)
              almSetTerminalList(snapshot['lib] snapshot['cell]
                snapshot['schematic] ?view car(rtwEntry))))
          when(rtwSym
            rtwSymbolTouched=t
            unless(and(schEditPinOrder(rtwSym snapshot['schematic] nil) dbSave(rtwSym))
              error("RCE: symbol pin order update failed.\n")))
          ddsRefresh(?cellview nil ?cdf t ?property nil ?techfile nil)
          rtwVerify=rceTermOrderSnapshot(
            snapshot['lib] snapshot['cell] snapshot['view])
          when(rceTermOrderMismatchP(rtwVerify)
            error("RCE: terminal orders still differ after update.\n"))
          t)
        progn(
          unless(and(rtwVerify !rceTermOrderMismatchP(rtwVerify))
            when(or(rtwTools rtwViews rtwSymbolTouched)
              rtwRestored=rceTermOrderRestore(snapshot rtwSym rtwTools rtwSymbolTouched rtwViews)
              unless(rtwRestored
                warn("[RCE] Term order rollback failed for %s/%s; inspect CDF and symbol.\n"
                     snapshot['lib] snapshot['cell]))))
          when(rtwSym
            when(rtwMode=="r" errset(dbReopen(rtwSym "r") t))
            dbClose(rtwSym)))) t)
    if(and(rtwResult car(rtwResult)) then
      info(sprintf(nil "[RCE] Synchronized symbol and auCdl/spectre term order: %s/%s\n"
                   snapshot['lib] snapshot['cell]))
      t
    else
      GUI_messageApp('e "RCE"
        strcat("Terminal order update failed for " snapshot['lib] "/" snapshot['cell]
          ". RCE was not started. See CIW for details."
          if(and(or(rtwTools rtwViews rtwSymbolTouched) !rtwRestored)
            then " Some changes may remain; inspect CDF and symbol before retrying."
            else "")))
      nil)
  )
)

procedure(rceCheckOaTermOrder(form)
  prog((rcoResult rcoSnapshot rcoAnswer)
    unless(form~>inpType~>value=="OA" return(t))
    rcoResult=errset(rceTermOrderSnapshot(
      form~>schLib~>value form~>schCell~>value form~>schView~>value) t)
    unless(and(rcoResult car(rcoResult))
      GUI_messageApp('e "RCE"
        "Cannot check OA terminal order. See CIW for the cellview and error; RCE was not started.")
      return(nil))
    rcoSnapshot=car(rcoResult)
    unless(rceTermOrderMismatchP(rcoSnapshot) return(t))
    rcoAnswer=rceTermOrderPrompt(rcoSnapshot)
    case(rcoAnswer
      (update return(rceTermOrderUpdate(rcoSnapshot)))
      (continue
        warn("[RCE] Continuing with unchanged terminal order for %s/%s.\n"
             rcoSnapshot['lib] rcoSnapshot['cell])
        return(t))
      (t return(nil)))
  )
)

procedure(rceTermOrderRevision()
  "20260911.view.term.order.v2"
)

rceTermOrderVersion="20260911.view.term.order.v2"
)
