;==============================================================================
; GUI for cadChangeTechLib.
;==============================================================================

unless(and(boundp('sicoChangeTechLibCoreVersion)
           sicoChangeTechLibCoreVersion=="20260811.mapping.v7"
           isCallable('cadChangeTechLib)
           isCallable('cadChangeTechLibMatchingCellNames))
  cond(
    (and(isCallable('cadLoadRelative)
         cadLoadRelative("utility/skill/cadChangeTechLib.il"))
      t)
    (and(isCallable('witMenuLoadRelative)
         witMenuLoadRelative("utility/skill/cadChangeTechLib.il"))
      t)
    (and(isFile("utility/skill/cadChangeTechLib.il")
         errset(load("utility/skill/cadChangeTechLib.il") nil))
      t)
    (and(isCallable('SICO_installationRoot)
      let((corePath)
        corePath=strcat(SICO_installationRoot()
                        "/tools/utility/skill/cadChangeTechLib.il")
        when(isFile(corePath)
          errset(load(corePath) nil))))
      t)
    (t
      warn("cadChangeTechLib GUI requires SICO_HOME or sicoToolRegister.il.\n"))
  )
)

unless(and(boundp('sicoChangeTechLibGuiVersion)
             sicoChangeTechLibGuiVersion=="20260902.form.lifecycle.v1"
             isCallable('cadChangeTechLibGuiRowText))

unless(boundp('cadChangeTechLibForm)
  cadChangeTechLibForm=nil
)

procedure(cadChangeTechLibBrand()
  cond(
    (isCallable('SICO_logo) SICO_logo())
    (isCallable('witMenuLogo) witMenuLogo())
    (t "SiCo")
  )
)

procedure(cadChangeTechLibGuiValue(value)
  if(value then sprintf(nil "%s" value) else "")
)

procedure(cadChangeTechLibGuiReportValue(report key defaultValue)
  let((entry)
    entry=member(key report)
    if(entry then cadr(entry) else defaultValue)
  )
)

procedure(cadChangeTechLibGuiRowText(row)
  sprintf(nil "%s/%s/%s  ->  %s/%s/%s"
    cadChangeTechLibRowField(row 0)
    cadChangeTechLibRowField(row 1)
    cadChangeTechLibRowField(row 2)
    cadChangeTechLibRowField(row 3)
    cadChangeTechLibRowField(row 4)
    cadChangeTechLibRowField(row 5))
)

procedure(cadChangeTechLibGuiListAt(items index)
  let((result count)
    result=nil
    count=0
    foreach(item items
      count=count+1
      when(and(!result count==index)
        result=item
      )
    )
    result
  )
)

procedure(cadChangeTechLibGuiReplaceAt(items index replacement)
  let((result count)
    result=nil
    count=0
    foreach(item items
      count=count+1
      result=append1(result if(count==index then replacement else item))
    )
    result
  )
)

procedure(cadChangeTechLibGuiRemoveAt(items index)
  let((result count)
    result=nil
    count=0
    foreach(item items
      count=count+1
      unless(count==index
        result=append1(result item)
      )
    )
    result
  )
)

procedure(cadChangeTechLibGuiSelectedIndex(form)
  let((value)
    value=form~>cadCtlMappingList~>value
    if(value then
      if(listp(value) then car(value) else value)
    else nil)
  )
)

procedure(cadChangeTechLibGuiSetStatus(form text)
  when(form~>cadCtlStatus
    form~>cadCtlStatus~>value=text
  )
  text
)

procedure(cadChangeTechLibGuiRefreshList(form)
  let((choices index)
    choices=foreach(mapcar row form~>cadMappings
      cadChangeTechLibGuiRowText(row))
    form~>cadCtlMappingList~>choices=choices
    index=form~>cadSelectedIndex
    if(and(index index<=length(choices)) then
      form~>cadCtlMappingList~>value=list(index)
    else
      form~>cadCtlMappingList~>value=nil
    )
    choices
  )
)

procedure(cadChangeTechLibGuiSetRowFields(form row)
  if(row then
    form~>cadCtlSourceLib~>value=cadChangeTechLibRowField(row 0)
    form~>cadCtlSourceCell~>value=cadChangeTechLibRowField(row 1)
    form~>cadCtlSourceView~>value=cadChangeTechLibRowField(row 2)
    form~>cadCtlTargetLib~>value=cadChangeTechLibRowField(row 3)
    form~>cadCtlTargetCell~>value=cadChangeTechLibRowField(row 4)
    form~>cadCtlTargetView~>value=cadChangeTechLibRowField(row 5)
  else
    form~>cadCtlSourceLib~>value=""
    form~>cadCtlSourceCell~>value=""
    form~>cadCtlSourceView~>value=""
    form~>cadCtlTargetLib~>value=""
    form~>cadCtlTargetCell~>value=""
    form~>cadCtlTargetView~>value=""
  )
)

procedure(cadChangeTechLibGuiReadRow(form)
  let((row invalidField)
    row=list(
      cadChangeTechLibGuiValue(form~>cadCtlSourceLib~>value)
      cadChangeTechLibGuiValue(form~>cadCtlSourceCell~>value)
      cadChangeTechLibGuiValue(form~>cadCtlSourceView~>value)
      cadChangeTechLibGuiValue(form~>cadCtlTargetLib~>value)
      cadChangeTechLibGuiValue(form~>cadCtlTargetCell~>value)
      cadChangeTechLibGuiValue(form~>cadCtlTargetView~>value))
    invalidField=car(setof(value row
      or(value=="" rexMatchp("[ \t\r\n]" value))))
    if(invalidField then
      hiDisplayAppDBox(
        ?name 'cadChangeTechLibMappingError
        ?dboxBanner strcat(cadChangeTechLibBrand() "::Change Tech Library")
        ?dboxText "Each mapping field is required and cannot contain whitespace."
        ?buttonLayout 'Close)
      nil
    else
      row
    )
  )
)

procedure(cadChangeTechLibGuiSameSourceP(row other)
  and(cadChangeTechLibRowField(row 0)==cadChangeTechLibRowField(other 0)
      cadChangeTechLibRowField(row 1)==cadChangeTechLibRowField(other 1)
      cadChangeTechLibRowField(row 2)==cadChangeTechLibRowField(other 2))
)

procedure(cadChangeTechLibGuiFindSourceIndex(rows row)
  let((index result)
    index=0
    result=nil
    foreach(item rows
      index=index+1
      when(and(!result cadChangeTechLibGuiSameSourceP(item row))
        result=index
      )
    )
    result
  )
)

procedure(cadChangeTechLibGuiSelectMappingCB(form)
  let((index row)
    index=cadChangeTechLibGuiSelectedIndex(form)
    form~>cadSelectedIndex=index
    row=if(index then cadChangeTechLibGuiListAt(form~>cadMappings index) else nil)
    cadChangeTechLibGuiSetRowFields(form row)
    when(row
      cadChangeTechLibGuiSetStatus(form sprintf(nil "Editing mapping row %d" index))
    )
  )
)

procedure(cadChangeTechLibGuiAddMappingCB(form)
  let((row index duplicateIndex rows)
    row=cadChangeTechLibGuiReadRow(form)
    when(row
      rows=form~>cadMappings
      duplicateIndex=cadChangeTechLibGuiFindSourceIndex(rows row)
      if(duplicateIndex then
        form~>cadSelectedIndex=duplicateIndex
        cadChangeTechLibGuiRefreshList(form)
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Source mapping already exists in row %d; use Update"
          duplicateIndex))
      else
        rows=append1(rows row)
        index=length(rows)
        form~>cadMappings=rows
        form~>cadSelectedIndex=index
        cadChangeTechLibGuiRefreshList(form)
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Mapping row %d added" index))
      )
    )
  )
)

procedure(cadChangeTechLibGuiUpdateMappingCB(form)
  let((row index duplicateIndex rows)
    index=cadChangeTechLibGuiSelectedIndex(form)
    rows=form~>cadMappings
    if(!and(index index<=length(rows)) then
      cadChangeTechLibGuiSetStatus(form "Select a mapping row to update")
      nil
    else
      row=cadChangeTechLibGuiReadRow(form)
      when(row
        duplicateIndex=cadChangeTechLibGuiFindSourceIndex(rows row)
        if(and(duplicateIndex duplicateIndex!=index) then
          cadChangeTechLibGuiSetStatus(form sprintf(nil
            "Source mapping already exists in row %d" duplicateIndex))
        else
          form~>cadMappings=cadChangeTechLibGuiReplaceAt(rows index row)
          form~>cadSelectedIndex=index
          cadChangeTechLibGuiRefreshList(form)
          cadChangeTechLibGuiSetStatus(form sprintf(nil
            "Mapping row %d updated" index))
        )
      )
    )
  )
)

procedure(cadChangeTechLibGuiRemoveMappingCB(form)
  let((index rows)
    index=cadChangeTechLibGuiSelectedIndex(form)
    rows=form~>cadMappings
    if(and(index index<=length(rows)) then
      rows=cadChangeTechLibGuiRemoveAt(rows index)
      form~>cadMappings=rows
      form~>cadSelectedIndex=if(rows then min(index length(rows)) else nil)
      cadChangeTechLibGuiRefreshList(form)
      cadChangeTechLibGuiSetRowFields(
        form if(form~>cadSelectedIndex
                then cadChangeTechLibGuiListAt(rows form~>cadSelectedIndex)
                else nil))
      cadChangeTechLibGuiSetStatus(form "Mapping row removed")
    else
      cadChangeTechLibGuiSetStatus(form "Select a mapping row first")
    )
  )
)

procedure(cadChangeTechLibGuiClearMappingCB(form)
  form~>cadMappings=nil
  form~>cadSelectedIndex=nil
  cadChangeTechLibGuiRefreshList(form)
  cadChangeTechLibGuiSetRowFields(form nil)
  cadChangeTechLibGuiSetStatus(form "Mapping rows cleared")
)

procedure(cadChangeTechLibGuiLoadMappingCB(form)
  let((path result mappings errors)
    path=cadChangeTechLibGuiValue(form~>cadCtlMappingFile~>value)
    if(path=="" then
      cadChangeTechLibGuiSetStatus(form "Choose a mapping file to load")
    else
      result=cadChangeTechLibReadMappingFile(path)
      mappings=car(result)
      errors=cadr(result)
      if(errors then
        hiDisplayAppDBox(
          ?name 'cadChangeTechLibMappingLoadError
          ?dboxBanner strcat(cadChangeTechLibBrand() "::Change Tech Library")
          ?dboxText buildString(errors "\n")
          ?buttonLayout 'Close)
        cadChangeTechLibGuiSetStatus(form "Mapping file has invalid rows")
      else
        form~>cadMappings=mappings
        form~>cadSelectedIndex=nil
        cadChangeTechLibGuiRefreshList(form)
        cadChangeTechLibGuiSetRowFields(form nil)
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Loaded %d mapping row(s)" length(mappings)))
      )
    )
  )
)

procedure(cadChangeTechLibGuiSaveMappingCB(form)
  let((path)
    path=cadChangeTechLibGuiValue(form~>cadCtlMappingFile~>value)
    if(path=="" then
      cadChangeTechLibGuiSetStatus(form "Choose a mapping file to save")
    else
      if(cadChangeTechLibWriteMappingFile(path form~>cadMappings) then
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Saved %d mapping row(s) to %s" length(form~>cadMappings) path))
      else
        cadChangeTechLibGuiSetStatus(form "Mapping file could not be written")
      )
    )
  )
)

procedure(cadChangeTechLibGuiApplyCB(form)
  let((designLib designCell designView path viaPath instanceReport viaReport
        instanceSuccess viaSuccess success instanceMatched instanceChanged
        viaMatched viaChanged viaEnabled combinedReport)
    designLib=cadChangeTechLibGuiValue(form~>cadCtlDesignLib~>value)
    designCell=cadChangeTechLibGuiValue(form~>cadCtlDesignCell~>value)
    designView=cadChangeTechLibGuiValue(form~>cadCtlDesignView~>value)
    path=cadChangeTechLibGuiValue(form~>cadCtlMappingFile~>value)
    viaPath=cadChangeTechLibGuiValue(form~>cadCtlViaMappingFile~>value)
    viaEnabled=cadChangeTechLibGuiViaEnabledP(form)
    cond(
      (or(designLib=="" designCell=="" designView=="")
        cadChangeTechLibGuiSetStatus(form "Choose the design library, cell, and view")
      )
      (and(!form~>cadMappings !viaEnabled)
        cadChangeTechLibGuiSetStatus(form
          "Add at least one mapping row or enable Update Via Master")
      )
      (and(viaEnabled !form~>cadViaMappings)
        cadChangeTechLibGuiSetStatus(form
          "Add at least one via mapping row")
      )
      (and(form~>cadMappings path=="")
        cadChangeTechLibGuiSetStatus(form
          "Choose a mapping file for instance mappings")
      )
      (and(viaEnabled viaPath=="")
        cadChangeTechLibGuiSetStatus(form
          "Choose a mapping file for via mappings")
      )
      (t
        instanceReport=nil
        viaReport=nil
        if(form~>cadMappings then
          if(cadChangeTechLibWriteMappingFile(path form~>cadMappings) then
            instanceReport=cadChangeTechLib(
              designLib designCell designView path)
          else
            instanceReport=list('success nil 'changed 0 'matched 0
              'errors list("Mapping file could not be written"))
          )
        )
        instanceSuccess=if(instanceReport
          then cadChangeTechLibGuiReportValue(instanceReport 'success nil)
          else t)
        when(and(viaEnabled instanceSuccess)
          if(cadChangeTechLibWriteViaMappingFile(
               viaPath form~>cadViaMappings) then
            viaReport=cadChangeTechLibReplaceViaMasters(
              designLib designCell designView viaPath)
          else
            viaReport=list('success nil 'changed 0 'matched 0
              'errors list("Via mapping file could not be written"))
          )
        )
        viaSuccess=if(viaReport
          then cadChangeTechLibGuiReportValue(viaReport 'success nil)
          else t)
        success=and(instanceSuccess viaSuccess)
        instanceMatched=cadChangeTechLibGuiReportValue(
          instanceReport 'matched 0)
        instanceChanged=cadChangeTechLibGuiReportValue(
          instanceReport 'changed 0)
        viaMatched=cadChangeTechLibGuiReportValue(viaReport 'matched 0)
        viaChanged=cadChangeTechLibGuiReportValue(viaReport 'changed 0)
        combinedReport=list(
          'success success
          'matched instanceMatched+viaMatched
          'changed instanceChanged+viaChanged
          'errors append(
            cadChangeTechLibGuiReportValue(instanceReport 'errors nil)
            cadChangeTechLibGuiReportValue(viaReport 'errors nil)))
        form~>cadLastReport=combinedReport
        if(and(viaEnabled instanceReport !instanceSuccess) then
          cadChangeTechLibGuiSetStatus(form
            "Instance mapping failed; via update was skipped")
        else
          cadChangeTechLibGuiSetStatus(form sprintf(nil
            "Instances matched=%d changed=%d; vias matched=%d changed=%d; success=%L"
            instanceMatched instanceChanged viaMatched viaChanged success))
        )
      )
    )
    form~>cadLastReport
  )
)

procedure(cadChangeTechLibGuiCloseCB(form)
  ; A button callback must not delete its owning form.  Close only unmaps it;
  ; keeping the handle allows the next invocation to reuse its fields safely.
  when(and(form
           if(isCallable('hiIsForm) then hiIsForm(form) else t)
           hiIsFormDisplayed(form))
    hiFormClose(form))
  t
)

procedure(cadChangeTechLibGuiRevision()
  sicoChangeTechLibGuiVersion
)

sicoChangeTechLibGuiVersion="20260902.form.lifecycle.v1"
)

unless(and(boundp('sicoChangeTechLibViaGuiVersion)
           sicoChangeTechLibViaGuiVersion=="20260811.viagui.v5"
           isCallable('cadChangeTechLibGuiViaAddMappingCB)
           isCallable('cadChangeTechLibGuiViaSourceLibCB))
  cond(
    (and(isCallable('cadLoadRelative)
         cadLoadRelative("utility/skill/cadChangeTechLibViaGui.il"))
      t)
    (and(isCallable('witMenuLoadRelative)
         witMenuLoadRelative("utility/skill/cadChangeTechLibViaGui.il"))
      t)
    (and(isFile("utility/skill/cadChangeTechLibViaGui.il")
         errset(load("utility/skill/cadChangeTechLibViaGui.il") nil))
      t)
    (and(isCallable('SICO_installationRoot)
      let((viaGuiPath)
        viaGuiPath=strcat(SICO_installationRoot()
                          "/tools/utility/skill/cadChangeTechLibViaGui.il")
        when(isFile(viaGuiPath)
          errset(load(viaGuiPath) nil))))
      t)
    (t
      warn("cadChangeTechLib via GUI support could not be loaded.\n"))
  )
)
