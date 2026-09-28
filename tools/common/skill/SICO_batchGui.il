;------------------------------------------------------------------------------
; Shared OA multi-cell task editor and Run Scope callbacks.
;------------------------------------------------------------------------------

unless(and(boundp('cadBatchGuiVersion)
             cadBatchGuiVersion=="20260812.common.v1")

unless(boundp('cadBatchTaskContextMenu)
  cadBatchTaskContextMenu=nil)

procedure(cadBatchPairedFlowP(flow)
  cadBatchFlowPairedP(flow)
)

procedure(cadBatchMessage(severity flow message)
  if(isCallable('GUI_messageApp) then
    GUI_messageApp(severity flow message)
  else
    if(severity=='e then warn("%s: %s\n" flow message)
                       else info("%s: %s\n" flow message))
  )
)

procedure(cadBatchCreateLcvEditor(prefix title defaultView)
  let((cv libField cellField viewField)
    cv=geGetEditCellView()
    libField=ddHiCreateLibraryComboField(
      ?name concat(prefix "Lib")
      ?prompt "Library: "
      ?defValue if(cv then cv~>libName else "")
    )
    cellField=ddHiCreateCellComboField(
      ?name concat(prefix "Cell")
      ?prompt "      Cell: "
      ?defValue if(cv then cv~>cellName else "")
    )
    viewField=ddHiCreateViewComboField(
      ?name concat(prefix "View")
      ?prompt "    View: "
      ?defValue defaultView
    )
    hiCreateVerticalBoxLayout(
      concat(prefix "Editor")
      ?frame title
      ?items list(libField cellField viewField)
      ?spacing 4
    )
  )
)

procedure(cadBatchCreateSyncButtons()
  let((schToLay layToSch)
    schToLay=hiCreateButton(
      ?name 'batchSchToLay
      ?buttonText "->"
      ?callback "cadBatchCopyLcvCB(hiGetCurrentForm() \"Schematic\")"
      ?toolTip "Copy Schematic Library/Cell/View to Layout"
    )
    layToSch=hiCreateButton(
      ?name 'batchLayToSch
      ?buttonText "<-"
      ?callback "cadBatchCopyLcvCB(hiGetCurrentForm() \"Layout\")"
      ?toolTip "Copy Layout Library/Cell/View to Schematic"
    )
    hiCreateVerticalBoxLayout(
      'batchSyncButtons
      ?items list(list('stretch_item 1) schToLay layToSch
                  list('stretch_item 1))
      ?spacing 4
    )
  )
)

procedure(cadBatchCreateTaskReport(flow)
  let((headers)
    headers=if(cadBatchPairedFlowP(flow) then
      '(("#" 35 'center 'int nil)
        ("Schematic Library" 105 'left 'string nil)
        ("Schematic Cell" 110 'left 'string nil)
        ("Schematic View" 90 'left 'string nil)
        ("Layout Library" 105 'left 'string nil)
        ("Layout Cell" 110 'left 'string nil)
        ("Layout View" 90 'left 'string nil))
    else
      '(("#" 35 'center 'int nil)
        ("Layout Library" 130 'left 'string nil)
        ("Layout Cell" 150 'left 'string nil)
        ("Layout View" 110 'left 'string nil))
    )
    hiCreateReportField(
      ?name 'batchTaskReport
      ?title "Cell Mappings:"
      ?headers headers
      ?choices nil
      ?selectMode 'single
      ?callback 'cadBatchTaskReportCB
      ?enableDeselectCB t
      ?doubleClickCB 'cadBatchTaskDoubleClickCB
      ?altRowHilight t
    )
  )
)

procedure(cadBatchCreateTaskContextMenu()
  unless(cadBatchTaskContextMenu
    cadBatchTaskContextMenu=hiCreateMenu(
      'cadBatchTaskContextMenu
      "Cell Mapping Actions"
      list(
        hiCreateMenuItem(
          ?name 'cadBatchDeleteSelectedMapping
          ?itemText "Delete Selected Mapping"
          ?callback "cadBatchDeleteSelectedMappingCB()"
        )
      )
    )
  )
  cadBatchTaskContextMenu
)

procedure(cadBatchCreateTaskButtons()
  let((addUpdate)
    addUpdate=hiCreateButton(
      ?name 'batchTaskAddUpdate
      ?buttonText "Add / Update"
      ?callback "cadBatchAddUpdateCB(hiGetCurrentForm())"
    )
    hiCreateHorizontalBoxLayout(
      'batchTaskButtons
      ?items list(addUpdate list('stretch_item 1))
      ?spacing 6
    )
  )
)

procedure(cadBatchCreateTaskLayout(flow)
  let((report editor buttons schEditor layEditor sync)
    report=cadBatchCreateTaskReport(flow)
    buttons=cadBatchCreateTaskButtons()
    if(cadBatchPairedFlowP(flow) then
      schEditor=cadBatchCreateLcvEditor("batchSch" "Schematic" "schematic")
      layEditor=cadBatchCreateLcvEditor("batchLay" "Layout" "layout")
      sync=cadBatchCreateSyncButtons()
      editor=hiCreateHorizontalBoxLayout(
        'batchTaskEditor
        ?items list(list(schEditor 'stretch 1) sync
                    list(layEditor 'stretch 1))
        ?spacing 6
      )
    else
      editor=cadBatchCreateLcvEditor("batchLay" "Layout" "layout")
    )
    hiCreateVerticalBoxLayout(
      'batchTaskLay
      ?frame "Multiple Cell Mappings"
      ?items list(list(report 'stretch 1) editor buttons)
      ?spacing 6
      ?invisible t
    )
  )
)

procedure(cadBatchMultipleP(form)
  and(form~>batchRunScope
        form~>batchRunScope~>value=="Multiple Cells")
)

procedure(cadBatchScopeCB(form)
  let((multiple flow wasMultiple paired)
    multiple=cadBatchMultipleP(form)
    flow=form~>cadBatchFlow
    wasMultiple=and(form~>cadBatchScopeActive t)
    paired=cadBatchFlowPairedP(flow)
    form~>batchTaskLay~>invisible=!multiple
    form~>batchParallelCells~>invisible=!multiple
    when(paired
      form~>inpType~>enabled=!multiple
      if(multiple then
        unless(wasMultiple
          form~>cadBatchSingleInputType=form~>inpType~>value)
        form~>inpType~>value="OA"
      else
        when(and(wasMultiple form~>cadBatchSingleInputType)
          form~>inpType~>value=form~>cadBatchSingleInputType)
      )
    )
    if(multiple then
      unless(wasMultiple
        cadBatchAdapterEnterMultiple(form flow))
    else
      cadBatchAdapterLeaveMultiple(form flow)
    )
    form~>cadBatchScopeActive=multiple
    t
  )
)

procedure(cadBatchBindForm(form flow)
  let((singleValues)
    form~>cadBatchFlow=flow
    form~>cadBatchTasks=nil
    form~>cadBatchEditIndex=nil
    singleValues=cadBatchDesignValues(form)
    cadBatchSetEditorValues(form singleValues)
    if(cadBatchPairedFlowP(flow) then
      ddHiLinkFields(form~>batchSchLib form~>batchSchCell form~>batchSchView)
      ddHiLinkFields(form~>batchLayLib form~>batchLayCell form~>batchLayView)
    else
      ddHiLinkFields(form~>batchLayLib form~>batchLayCell form~>batchLayView)
    )
    form~>batchTaskReport~>hiContextMenu=cadBatchCreateTaskContextMenu()
    form~>batchTaskReport~>hiShowContextMenuCallback=
      'cadBatchTaskContextMenuCB
    hiSetFieldMinSize(form 'batchTaskReport ?widgetHeight 165)
    cadBatchScopeCB(form)
    form
  )
)

procedure(cadBatchGuiSharedRevision()
  "20260812.common.v1"
)

cadBatchGuiVersion="20260812.common.v1"
)
