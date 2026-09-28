;------------------------------------------------------------------------------
; Shared modeless status monitor for OA multi-cell batches.
;------------------------------------------------------------------------------

unless(and(boundp('sicoBatchMonitorAuditVersion)
             sicoBatchMonitorAuditVersion=="20260911.flow.audit.v1")

procedure(cadBatchMonitorFormValidP(form)
  and(form
      if(isCallable('hiIsForm) then hiIsForm(form) else t))
)

procedure(cadBatchMonitorDropStaleForm(batchId form)
  when(and(form !cadBatchMonitorFormValidP(form))
    remove(batchId cadBatchMonitorForms))
  nil
)

unless(boundp('cadBatchMonitorForms)
  cadBatchMonitorForms=makeTable("cadBatchMonitorForms" nil))

procedure(cadBatchMonitorReadRows(path)
  let((input line fields rows)
    rows=nil
    when(and(path isFile(path))
      input=infile(path)
      when(input
        gets(line input)
        while(gets(line input)
          fields=parseString(line "\t\r\n" t)
          when(length(fields)>=14
            rows=append1(rows fields))
        )
        close(input)
      )
    )
    rows
  )
)

procedure(cadBatchMonitorInitialRows(meta)
  let((rows index spec)
    rows=nil
    index=1
    foreach(spec meta['specs]
      rows=append1(rows
        list(sprintf(nil "%d" index) spec['id] spec['label] "pending" "-"
             spec['runDir] spec['config] spec['launchLog] "-" "-" "-"
             spec['resultPath] "-" "-"))
      index=index+1
    )
    rows
  )
)

procedure(cadBatchMonitorChoices(rows)
  let((choices row)
    choices=nil
    foreach(row rows
      choices=append1(choices
        list(atoi(nth(0 row)) nth(2 row)
             if(nth(3 row)=="succeeded_with_warnings" then "LVS Warning" else nth(3 row))
             nth(4 row) nth(10 row) nth(11 row)))
    )
    choices
  )
)

procedure(cadBatchMonitorSummary(rows)
  let((pending running waiting succeeded failed canceled row status warnings)
    pending=0
    running=0
    waiting=0
    succeeded=0
    failed=0
    canceled=0
    warnings=0
    foreach(row rows
      status=nth(3 row)
      case(status
        ("pending" pending=pending+1)
        ("running" running=running+1)
        ("awaiting_publication" waiting=waiting+1)
        ("succeeded" succeeded=succeeded+1)
        ("succeeded_with_warnings" warnings=warnings+1)
        ("failed" failed=failed+1)
        ("canceled" canceled=canceled+1)
      )
    )
    strcat(sprintf(nil
      "Total %d   Pending %d   Running %d   Publishing %d   Passed %d   Failed %d   Canceled %d"
      length(rows) pending running waiting succeeded failed canceled)
      if(warnings>0 then sprintf(nil "   LVS Warnings %d" warnings) else ""))
  )
)

procedure(cadBatchMonitorSelectedRow(form)
  let((selected)
    selected=car(hiReportGetSelectedItems(form~>cadBatchMonitorReport))
    when(selected!=nil nth(selected form~>cadBatchMonitorRows))
  )
)

procedure(cadBatchMonitorSelectedSpec(form)
  let((row)
    row=cadBatchMonitorSelectedRow(form)
    when(row cadBatchSpecById(form~>cadBatchMeta nth(1 row)))
  )
)

procedure(cadBatchMonitorOpenResultCB(form)
  if(cadBatchMonitorFormValidP(form) then
    let((meta spec result)
      meta=form~>cadBatchMeta
      when(meta
        spec=cadBatchMonitorSelectedSpec(form)
        when(spec
          result=cadBatchAdapterOpenResult(meta['flow] spec)
          unless(result
            cadBatchMessage('w meta['flow]
              "The selected task result is not available yet."))
        )
      )
    )
  else
    nil
  )
)

procedure(cadBatchMonitorOpenLogCB(form)
  if(cadBatchMonitorFormValidP(form) then
    let((meta spec path)
      meta=form~>cadBatchMeta
      when(meta
        spec=cadBatchMonitorSelectedSpec(form)
        path=if(spec then spec['launchLog] else meta['batchLog])
        if(and(path strlen(path)>0) then
          GUI_flowLogOpen(strcat(meta['flow] " Batch Task") path)
        else
          cadBatchMessage('w meta['flow] "The selected task log is unavailable."))
      )
    )
  else
    nil
  )
)

procedure(cadBatchMonitorCancelCB(form)
  if(cadBatchMonitorFormValidP(form) then
    let((meta answer)
      meta=form~>cadBatchMeta
      when(and(meta !meta['done])
        answer=GUI_askApp(strcat(meta['flow] " Batch")
          "Cancel pending and running cells in this batch?")
        when(answer=="YES"
          cadBatchCancel(meta))
      )
    )
  else
    nil
  )
)

procedure(cadBatchMonitorRefreshCB(form)
  when(cadBatchMonitorFormValidP(form)
    cadBatchMonitorRefresh(form~>cadBatchMeta))
)

procedure(cadBatchMonitorRefresh(meta @optional final)
  let((form rows choices selected)
    form=cadBatchMonitorForms[meta['id]]
    when(cadBatchMonitorFormValidP(form)
      rows=cadBatchMonitorReadRows(meta['statusTsv])
      unless(rows
        rows=cadBatchMonitorInitialRows(meta))
      selected=car(hiReportGetSelectedItems(form~>cadBatchMonitorReport))
      choices=cadBatchMonitorChoices(rows)
      form~>cadBatchMonitorRows=rows
      form~>cadBatchMonitorReport~>choices=choices
      form~>cadBatchMonitorStatus~>labelText=cadBatchMonitorSummary(rows)
      hiReportDeselectAllItems(form~>cadBatchMonitorReport nil)
      when(and(selected!=nil selected<length(choices))
        hiReportSelectItem(form~>cadBatchMonitorReport selected nil))
      hiSetFormButtonEnabled(form concat("Cancel Batch")
        and(!meta['done] !meta['cancelRequested]))
      when(final
        hiSetFormButtonEnabled(form concat("Cancel Batch") nil))
    )
    rows
  )
)

procedure(cadBatchMonitorTimer(batchId)
  let((meta form)
    meta=cadBatchRuns[batchId]
    form=cadBatchMonitorForms[batchId]
    when(and(meta cadBatchMonitorFormValidP(form)
             hiIsFormDisplayed(form))
      cadBatchMonitorRefresh(meta)
      unless(meta['done]
        hiRegTimer(sprintf(nil "cadBatchMonitorTimer(%L)" batchId) 10))
    )
  )
)

procedure(cadBatchMonitorDisplay(meta)
  let((form report status layout formName)
    form=cadBatchMonitorForms[meta['id]]
    when(and(form !cadBatchMonitorFormValidP(form))
      remove(meta['id] cadBatchMonitorForms)
      form=nil)
    unless(form
      report=hiCreateReportField(
        ?name 'cadBatchMonitorReport
        ?title "Cells:"
        ?headers '(("#" 35 'center 'int nil)
                    ("Cell Mapping" 245 'left 'string nil)
                    ("Status" 125 'left 'string nil)
                    ("Exit" 45 'center 'string nil)
                    ("Seconds" 65 'right 'string nil)
                    ("Result" 240 'left 'string nil))
        ?choices nil
        ?selectMode 'single
        ?altRowHilight t)
      status=hiCreateLabel(
        ?name 'cadBatchMonitorStatus
        ?labelText "Starting batch..."
        ?justification 'left)
      layout=hiCreateVerticalBoxLayout(
        concat("cadBatchMonitorLayout_" cadBatchSafeToken(meta['id]))
        ?items list(status list(report 'stretch 1))
        ?margins list(10 10 10 10)
        ?spacing 8)
      formName=concat("cadBatchMonitorForm_" cadBatchSafeToken(meta['id]))
      form=hiCreateLayoutForm(
        formName
        strcat(cond(
                 (isCallable('SICO_logo) SICO_logo())
                 (isCallable('witMenuLogo) witMenuLogo())
                 (t "SiCo"))
                "::" meta['flow] " Multi-Cell Summary")
        layout
        ?buttonLayout list(
          'Empty
          list(concat("Open Result") 'cadBatchMonitorOpenResultCB)
          list(concat("Open Log") 'cadBatchMonitorOpenLogCB)
          list(concat("Refresh") 'cadBatchMonitorRefreshCB)
          list(concat("Cancel Batch") 'cadBatchMonitorCancelCB)
          list('Close 'hiFormClose))
        ?dialogStyle 'modeless
        ?initialSize list(840 420)
        ?minSize list(680 320)
        ?sizePolicy 'expanding)
      form~>cadBatchMeta=meta
      form~>cadBatchMonitorRows=nil
      cadBatchMonitorForms[meta['id]]=form
    )
    cadBatchMonitorRefresh(meta)
    hiDisplayForm(form)
    unless(meta['done]
      hiRegTimer(sprintf(nil "cadBatchMonitorTimer(%L)" meta['id]) 10))
    form
  )
)

procedure(cadBatchMonitorRevision()
  "20260902.form.lifecycle.v1"
)

procedure(cadBatchMonitorAuditRevision()
  "20260911.flow.audit.v1"
)

sicoBatchMonitorAuditVersion="20260911.flow.audit.v1"

sicoBatchMonitorVersion="20260902.form.lifecycle.v1"
)
