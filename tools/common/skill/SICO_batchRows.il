;------------------------------------------------------------------------------
; Shared multi-cell row editing and report-field callbacks.
;------------------------------------------------------------------------------

unless(and(boundp('cadBatchRowsVersion)
             cadBatchRowsVersion=="20260812.common.v1")

unless(boundp('cadBatchTaskContextForm)
  cadBatchTaskContextForm=nil)

procedure(cadBatchFieldValue(form name)
  let((field)
    field=get(form name)
    if(field then or(field~>value "") else "")
  )
)

procedure(cadBatchCopyLcvCB(form source)
  let((fromPrefix toPrefix suffix)
    if(source=="Schematic" then
      fromPrefix="batchSch"
      toPrefix="batchLay"
    else
      fromPrefix="batchLay"
      toPrefix="batchSch"
    )
    foreach(suffix '("Lib" "Cell" "View")
      get(form concat(toPrefix suffix))~>value=
        cadBatchFieldValue(form concat(fromPrefix suffix))
    )
  )
)

procedure(cadBatchEditorValues(form)
  if(cadBatchPairedFlowP(form~>cadBatchFlow) then
    list(cadBatchFieldValue(form 'batchSchLib)
         cadBatchFieldValue(form 'batchSchCell)
         cadBatchFieldValue(form 'batchSchView)
         cadBatchFieldValue(form 'batchLayLib)
         cadBatchFieldValue(form 'batchLayCell)
         cadBatchFieldValue(form 'batchLayView))
  else
    list(cadBatchFieldValue(form 'batchLayLib)
         cadBatchFieldValue(form 'batchLayCell)
         cadBatchFieldValue(form 'batchLayView))
  )
)

procedure(cadBatchSetEditorValues(form row)
  let((names index)
    names=if(cadBatchPairedFlowP(form~>cadBatchFlow) then
      '(batchSchLib batchSchCell batchSchView
        batchLayLib batchLayCell batchLayView)
    else
      '(batchLayLib batchLayCell batchLayView)
    )
    index=0
    foreach(name names
      get(form name)~>value=or(nth(index row) "")
      index=index+1
    )
  )
)

procedure(cadBatchSetDesignValues(form row)
  if(cadBatchPairedFlowP(form~>cadBatchFlow) then
    form~>schLib~>value=nth(0 row)
    form~>schCell~>value=nth(1 row)
    form~>schView~>value=nth(2 row)
    form~>layLib~>value=nth(3 row)
    form~>layCell~>value=nth(4 row)
    form~>layView~>value=nth(5 row)
  else
    form~>layLib~>value=nth(0 row)
    form~>layCell~>value=nth(1 row)
    form~>layView~>value=nth(2 row)
  )
)

procedure(cadBatchDesignValues(form)
  if(cadBatchPairedFlowP(form~>cadBatchFlow) then
    list(form~>schLib~>value form~>schCell~>value form~>schView~>value
         form~>layLib~>value form~>layCell~>value form~>layView~>value)
  else
    list(form~>layLib~>value form~>layCell~>value form~>layView~>value)
  )
)

procedure(cadBatchRowValidP(form row)
  let((valid offset libName cellName viewName)
    valid=t
    foreach(offset if(cadBatchPairedFlowP(form~>cadBatchFlow)
                       then '(0 3) else '(0))
      libName=nth(offset row)
      cellName=nth(offset+1 row)
      viewName=nth(offset+2 row)
      unless(and(libName strlen(libName)>0
                   cellName strlen(cellName)>0
                   viewName strlen(viewName)>0
                   ddGetObj(libName cellName viewName))
        valid=nil
      )
    )
    valid
  )
)

procedure(cadBatchRowKey(form row)
  if(cadBatchPairedFlowP(form~>cadBatchFlow) then
    list(nth(3 row) nth(4 row))
  else
    list(nth(0 row) nth(1 row))
  )
)

procedure(cadBatchDuplicateRowP(form row ignoredIndex)
  let((duplicate index existing key)
    duplicate=nil
    index=0
    key=cadBatchRowKey(form row)
    foreach(existing form~>cadBatchTasks
      when(and(index!=ignoredIndex equal(key cadBatchRowKey(form existing)))
        duplicate=t
      )
      index=index+1
    )
    duplicate
  )
)

procedure(cadBatchReplaceAt(items target replacement)
  let((result index item)
    result=nil
    index=0
    foreach(item items
      result=append1(result if(index==target then replacement else item))
      index=index+1
    )
    result
  )
)

procedure(cadBatchRemoveAt(items target)
  let((result index item)
    result=nil
    index=0
    foreach(item items
      unless(index==target
        result=append1(result item))
      index=index+1
    )
    result
  )
)

procedure(cadBatchIndexValidP(index items)
  and(integerp(index) index>=0 index<length(items))
)

procedure(cadBatchRefreshReport(form @optional selectedIndex)
  let((choices index row)
    choices=nil
    index=1
    foreach(row form~>cadBatchTasks
      choices=append1(choices cons(index row))
      index=index+1
    )
    form~>batchTaskReport~>choices=choices
    hiReportDeselectAllItems(form~>batchTaskReport nil)
    when(cadBatchIndexValidP(selectedIndex choices)
      hiReportSelectItem(form~>batchTaskReport selectedIndex nil))
    choices
  )
)

procedure(cadBatchSelectedIndex(form)
  car(hiReportGetSelectedItems(form~>batchTaskReport))
)

procedure(cadBatchTaskReportCB(fieldName selectedItems)
  let((form selected)
    form=hiGetCurrentForm()
    selected=car(selectedItems)
    when(and(form form~>cadBatchEditIndex!=nil
                   selected!=form~>cadBatchEditIndex)
      form~>cadBatchEditIndex=nil)
    t
  )
)

procedure(cadBatchTaskDoubleClickCB(fieldName itemIndex)
  let((form row)
    form=hiGetCurrentForm()
    when(and(form cadBatchIndexValidP(itemIndex form~>cadBatchTasks))
      row=nth(itemIndex form~>cadBatchTasks)
      form~>cadBatchEditIndex=itemIndex
      cadBatchSetEditorValues(form row)
      hiReportDeselectAllItems(form~>batchTaskReport nil)
      hiReportSelectItem(form~>batchTaskReport itemIndex nil)
    )
    t
  )
)

procedure(cadBatchTaskContextMenuCB(menu form fieldName)
  cadBatchTaskContextForm=form
  t
)

procedure(cadBatchDeleteSelectedMappingCB()
  when(cadBatchTaskContextForm
    cadBatchRemoveCB(cadBatchTaskContextForm)
    cadBatchTaskContextForm=nil)
  t
)

procedure(cadBatchAddUpdateCB(form)
  let((row editIndex)
    row=cadBatchEditorValues(form)
    editIndex=form~>cadBatchEditIndex
    unless(cadBatchIndexValidP(editIndex form~>cadBatchTasks)
      editIndex=nil)
    cond(
      (!cadBatchRowValidP(form row)
        cadBatchMessage('e form~>cadBatchFlow
          "Select existing OA Library/Cell/View values for the mapping row."))
      (cadBatchDuplicateRowP(form row editIndex)
        cadBatchMessage('e form~>cadBatchFlow
          "The Layout Library/Cell already exists in another mapping row."))
      (editIndex!=nil
        form~>cadBatchTasks=cadBatchReplaceAt(
          form~>cadBatchTasks editIndex row)
        form~>cadBatchEditIndex=nil
        cadBatchRefreshReport(form editIndex))
      (t
        form~>cadBatchTasks=append1(form~>cadBatchTasks row)
        form~>cadBatchEditIndex=nil
        cadBatchRefreshReport(form sub1(length(form~>cadBatchTasks))))
    )
  )
)

procedure(cadBatchRemoveCB(form)
  let((selected nextIndex)
    selected=cadBatchSelectedIndex(form)
    form~>cadBatchEditIndex=nil
    when(cadBatchIndexValidP(selected form~>cadBatchTasks)
      form~>cadBatchTasks=cadBatchRemoveAt(form~>cadBatchTasks selected)
      nextIndex=if(selected<length(form~>cadBatchTasks)
                   then selected else sub1(length(form~>cadBatchTasks)))
      cadBatchRefreshReport(form if(nextIndex>=0 then nextIndex else nil))
    )
  )
)

procedure(cadBatchClearCB(form)
  form~>cadBatchTasks=nil
  form~>cadBatchEditIndex=nil
  cadBatchRefreshReport(form)
)

procedure(cadBatchValidateTasks(form)
  let((valid row)
    valid=and(cadBatchMultipleP(form) form~>cadBatchTasks)
    unless(valid
      cadBatchMessage('e form~>cadBatchFlow
        "Add at least one cell mapping row."))
    when(valid
      foreach(row form~>cadBatchTasks
        unless(cadBatchRowValidP(form row)
          valid=nil)
      )
      unless(valid
        cadBatchMessage('e form~>cadBatchFlow
          "One or more mapping rows no longer resolve to existing OA views."))
    )
    valid
  )
)

procedure(cadBatchRowsRevision()
  "20260812.common.v1"
)

cadBatchRowsVersion="20260812.common.v1"
)
