;------------------------------------------------------------------------------
; Flow adapter registry for the shared OA multi-cell batch implementation.
;------------------------------------------------------------------------------

unless(and(boundp('cadBatchAdapterVersion)
             cadBatchAdapterVersion=="20260812.common.v1")

unless(boundp('cadBatchFlowAdapters)
  cadBatchFlowAdapters=makeTable("cadBatchFlowAdapters" nil))

procedure(cadBatchFlowKey(flow)
  upperCase(if(flow then sprintf(nil "%s" flow) else ""))
)

procedure(cadBatchRegisterFlowAdapter(flow pairedP enterMultipleCB
                                      leaveMultipleCB openResultCB pythonExeCB)
  let((key adapter)
    key=cadBatchFlowKey(flow)
    adapter=makeTable(strcat("cadBatchFlowAdapter" key) nil)
    adapter['flow]=key
    adapter['pairedP]=and(pairedP t)
    adapter['enterMultipleCB]=enterMultipleCB
    adapter['leaveMultipleCB]=leaveMultipleCB
    adapter['openResultCB]=openResultCB
    adapter['pythonExeCB]=pythonExeCB
    cadBatchFlowAdapters[key]=adapter
    adapter
  )
)

procedure(cadBatchFlowAdapter(flow)
  cadBatchFlowAdapters[cadBatchFlowKey(flow)]
)

procedure(cadBatchAdapterCallback(flow slot)
  let((adapter)
    adapter=cadBatchFlowAdapter(flow)
    when(adapter adapter[slot])
  )
)

procedure(cadBatchAdapterCall(flow slot args)
  let((callback)
    callback=cadBatchAdapterCallback(flow slot)
    when(and(callback isCallable(callback))
      apply(callback args))
  )
)

procedure(cadBatchFlowPairedP(flow)
  let((adapter)
    adapter=cadBatchFlowAdapter(flow)
    and(adapter adapter['pairedP])
  )
)

procedure(cadBatchAdapterEnterMultiple(form flow)
  cadBatchAdapterCall(flow 'enterMultipleCB list(form))
)

procedure(cadBatchAdapterLeaveMultiple(form flow)
  cadBatchAdapterCall(flow 'leaveMultipleCB list(form))
)

procedure(cadBatchAdapterOpenResult(flow spec)
  cadBatchAdapterCall(flow 'openResultCB list(spec))
)

procedure(cadBatchAdapterPythonExe(flow)
  cadBatchAdapterCall(flow 'pythonExeCB nil)
)

procedure(cadBatchAdapterRevision()
  "20260812.common.v1"
)

cadBatchAdapterVersion="20260812.common.v1"
)
