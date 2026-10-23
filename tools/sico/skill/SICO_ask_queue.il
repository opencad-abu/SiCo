/* Instance-wide single-flight admission, independent of session execution.
 * Frozen envelopes wait here only until a durable terminal receipt arrives. */
unless(boundp('sicoAskWaiting) sicoAskWaiting=nil)
unless(boundp('sicoAskFlight) sicoAskFlight=nil)

procedure(sicoAskAvailable()
  and(length(sicoAskWaiting)<64
    !and(sicoAskFlight sicoQuickRequests[sicoAskFlight]['unknown])))

procedure(sicoAskEnqueue(aqeKey aqePayload aqeCommand)
  prog((aqeComposer)
    aqeComposer=sicoQuickRequests[aqeKey]
    unless(sicoAskAvailable()
      sicoQuickRejectedHost(aqeKey "queue_full") return(nil))
    aqeComposer['envelope]=aqePayload aqeComposer['hostCommand]=aqeCommand
    sicoAskWaiting=append1(sicoAskWaiting aqeKey)
    sicoAskPump()
    return(t)))

procedure(sicoAskPump()
  prog((aqpKey aqpComposer aqpChild aqpSent aqpStarted)
    when(or(sicoAskFlight !sicoAskWaiting) return(nil))
    aqpKey=car(sicoAskWaiting) sicoAskWaiting=cdr(sicoAskWaiting)
    aqpComposer=sicoQuickRequests[aqpKey]
    sicoAskFlight=aqpKey
    aqpStarted=errset(ipcBeginProcess(aqpComposer['hostCommand] ""
      'sicoQuickHostData 'sicoError 'sicoQuickHostExit) t)
    aqpChild=when(aqpStarted car(aqpStarted))
    unless(aqpChild sicoQuickRejectedHost(aqpKey "unavailable") return(nil))
    sicoQuickHostChildren[aqpChild]=aqpKey
    aqpSent=errset(ipcWriteProcess(aqpChild strcat(aqpComposer['envelope] "\n")) nil)
    unless(and(aqpSent car(aqpSent))
      errset(ipcCloseProcess(aqpChild) nil)
      sicoQuickUnknownHost(aqpKey))
    return(t)))

procedure(sicoAskSettled(aqsKey)
  when(sicoAskFlight==aqsKey
    sicoAskFlight=nil
    sicoAskPump()))

/* An unknown receipt holds the slot. This explicit query never resubmits. */
procedure(sicoAskQuery(aqqKey)
  prog((aqqComposer aqqCommand aqqChild aqqStarted aqqSent)
    aqqComposer=sicoQuickRequests[aqqKey]
    unless(and(sicoAskFlight==aqqKey aqqComposer['unknown]) return(nil))
    when(aqqComposer['queryChild] return(nil))
    aqqCommand=sicoLaunchCommand("host-query")
    unless(aqqCommand sicoQuickUnknownHost(aqqKey) return(nil))
    aqqStarted=errset(ipcBeginProcess(aqqCommand ""
      'sicoQuickHostData 'sicoError 'sicoQuickHostExit) t)
    aqqChild=when(aqqStarted car(aqqStarted))
    unless(aqqChild sicoQuickUnknownHost(aqqKey) return(nil))
    aqqComposer['queryChild]=aqqChild
    sicoQuickHostChildren[aqqChild]=aqqKey
    aqqSent=errset(ipcWriteProcess(aqqChild strcat(aqqComposer['envelope] "\n")) t)
    unless(and(aqqSent car(aqqSent))
      errset(ipcCloseProcess(aqqChild) nil)
      aqqComposer['queryChild]=nil sicoQuickUnknownHost(aqqKey))
    return(t)))

/* Reopening Ask always exposes the original unknown request, without a retry. */
procedure(sicoAskRestoreUnknown()
  prog((aruComposer aruCommand aruStarted aruChild)
    unless(and(sicoAskFlight sicoQuickRequests[sicoAskFlight]['unknown]) return(nil))
    aruComposer=sicoQuickRequests[sicoAskFlight]
    when(and(aruComposer['child] !aruComposer['closing]
             ipcIsAliveProcess(aruComposer['child]))
      sicoQuickSend(aruComposer "show") return(aruComposer))
    aruCommand=sicoLaunchCommand("quick-input")
    unless(aruCommand error("Ask 原请求 %s 待核对；无法打开查询窗口。" sicoAskFlight))
    aruStarted=errset(ipcBeginProcess(aruCommand ""
      'sicoQuickData 'sicoError 'sicoQuickChildExit) t)
    aruChild=when(aruStarted car(aruStarted))
    unless(aruChild error("Ask 原请求 %s 待核对；无法打开查询窗口。" sicoAskFlight))
    aruComposer['child]=aruChild aruComposer['closing]=nil aruComposer['buffer]=nil
    sicoQuickChildren[aruChild]=aruComposer
    sicoQuickWindows[aruComposer['target]['window]]=aruComposer
    ipcWriteProcess(aruChild sprintf(nil
      "{\"kind\":\"open\",\"source\":\"%s\",\"destination\":\"原请求待核对\",\"request\":%s}\n"
      aiJsonEscape(aruComposer['source]) aruComposer['envelope]))
    return(aruComposer)))
