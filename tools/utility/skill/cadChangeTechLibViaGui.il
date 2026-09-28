;==============================================================================
; Via mapping callbacks for cadChangeTechLib.
;==============================================================================

unless(and(boundp('sicoChangeTechLibViaGuiVersion)
             sicoChangeTechLibViaGuiVersion=="20260811.viagui.v5"
             isCallable('cadChangeTechLibGuiViaAddMappingCB)
             isCallable('cadChangeTechLibGuiViaSourceLibCB))

procedure(cadChangeTechLibGuiLayoutViewP(viewName)
  if(member(viewName list("layout" "maskLayout" "maskLayoutXL" "maskLayoutEXL"))
    then t else nil)
)

procedure(cadChangeTechLibGuiViaEnabledP(form)
  and(cadChangeTechLibGuiLayoutViewP(
        cadChangeTechLibGuiValue(form~>cadCtlDesignView~>value))
      form~>cadCtlUpdateViaMaster~>value)
)

procedure(cadChangeTechLibGuiUpdateViaControls(form)
  let((layoutP viaEnabled)
    when(form
      layoutP=cadChangeTechLibGuiLayoutViewP(
        cadChangeTechLibGuiValue(form~>cadCtlDesignView~>value))
      when(form~>cadCtlUpdateViaMaster
        when(!layoutP
          form~>cadCtlUpdateViaMaster~>value=nil
        )
        form~>cadCtlUpdateViaMaster~>invisible=!layoutP
        form~>cadCtlUpdateViaMaster~>enabled=layoutP
        viaEnabled=and(layoutP form~>cadCtlUpdateViaMaster~>value)
      )
      hiSetStackedLayoutCurrentIndex(
        if(viaEnabled then 1 else 0)
        form
        'cadChangeTechLibViaStackLayout)
    )
    layoutP
  )
)

procedure(cadChangeTechLibGuiDesignViewCB(form)
  cadChangeTechLibGuiUpdateViaControls(form)
  t
)

procedure(cadChangeTechLibGuiViaToggleCB(form)
  cadChangeTechLibGuiUpdateViaControls(form)
  t
)

procedure(cadChangeTechLibGuiViaDefNames(libName)
  let((libObj techId protected names)
    names=nil
    libObj=if(or(libName=="" libName=="*")
      then nil else ddGetObj(libName))
    protected=if(libObj then errset(techGetTechFile(libObj) nil) else nil)
    techId=if(protected then car(protected) else nil)
    protected=if(techId then errset(techId~>viaDefs~>name nil) else nil)
    when(and(protected listp(car(protected)))
      names=car(protected)
    )
    names
  )
)

procedure(cadChangeTechLibGuiViaSourceLibCB(form)
  let((libName names choices current changed)
    when(form
      libName=cadChangeTechLibGuiValue(form~>cadCtlViaSourceLib~>value)
      names=cadChangeTechLibGuiViaDefNames(libName)
      choices=append(names list("*"))
      current=cadChangeTechLibGuiValue(form~>cadCtlViaSourceDef~>value)
      changed=libName!=form~>cadViaSourceChoiceLib
      form~>cadCtlViaSourceDef~>items=choices
      when(changed
        unless(and(current!="" current!="*" member(current choices))
          form~>cadCtlViaSourceDef~>value=
            if(names then car(names) else "*")
        )
      )
      form~>cadViaSourceChoiceLib=libName
    )
    t
  )
)

procedure(cadChangeTechLibGuiViaTargetLibCB(form)
  let((libName names choices current changed)
    when(form
      libName=cadChangeTechLibGuiValue(form~>cadCtlViaTargetLib~>value)
      names=cadChangeTechLibGuiViaDefNames(libName)
      choices=append(names list("*"))
      current=cadChangeTechLibGuiValue(form~>cadCtlViaTargetDef~>value)
      changed=libName!=form~>cadViaTargetChoiceLib
      form~>cadCtlViaTargetDef~>items=choices
      when(and(changed !member(current choices))
        form~>cadCtlViaTargetDef~>value=
          if(names then car(names) else "*")
      )
      form~>cadViaTargetChoiceLib=libName
    )
    t
  )
)

procedure(cadChangeTechLibGuiRefreshViaDefChoices(form)
  cadChangeTechLibGuiViaSourceLibCB(form)
  cadChangeTechLibGuiViaTargetLibCB(form)
  t
)

procedure(cadChangeTechLibGuiViaRowText(row)
  sprintf(nil "%s/%s  ->  %s/%s"
    cadChangeTechLibRowField(row 0)
    cadChangeTechLibRowField(row 1)
    cadChangeTechLibRowField(row 2)
    cadChangeTechLibRowField(row 3))
)

procedure(cadChangeTechLibGuiViaSelectedIndex(form)
  let((value)
    value=form~>cadCtlViaMappingList~>value
    if(value then
      if(listp(value) then car(value) else value)
    else nil)
  )
)

procedure(cadChangeTechLibGuiViaRefreshList(form)
  let((choices index)
    choices=foreach(mapcar row form~>cadViaMappings
      cadChangeTechLibGuiViaRowText(row))
    form~>cadCtlViaMappingList~>choices=choices
    index=form~>cadViaSelectedIndex
    if(and(index index<=length(choices)) then
      form~>cadCtlViaMappingList~>value=list(index)
    else
      form~>cadCtlViaMappingList~>value=nil
    )
    choices
  )
)

procedure(cadChangeTechLibGuiViaSetRowFields(form row)
  if(row then
    form~>cadCtlViaSourceLib~>value=cadChangeTechLibRowField(row 0)
    form~>cadCtlViaTargetLib~>value=cadChangeTechLibRowField(row 2)
    cadChangeTechLibGuiRefreshViaDefChoices(form)
    form~>cadCtlViaSourceDef~>value=cadChangeTechLibRowField(row 1)
    form~>cadCtlViaTargetDef~>value=cadChangeTechLibRowField(row 3)
  else
    form~>cadCtlViaSourceLib~>value=""
    form~>cadCtlViaTargetLib~>value=""
    cadChangeTechLibGuiRefreshViaDefChoices(form)
    form~>cadCtlViaSourceDef~>value=""
    form~>cadCtlViaTargetDef~>value=""
  )
)

procedure(cadChangeTechLibGuiViaReadRow(form)
  let((row invalidField)
    row=list(
      cadChangeTechLibGuiValue(form~>cadCtlViaSourceLib~>value)
      cadChangeTechLibGuiValue(form~>cadCtlViaSourceDef~>value)
      cadChangeTechLibGuiValue(form~>cadCtlViaTargetLib~>value)
      cadChangeTechLibGuiValue(form~>cadCtlViaTargetDef~>value))
    invalidField=car(setof(value row
      or(value=="" rexMatchp("[ \t\r\n]" value))))
    if(invalidField then
      hiDisplayAppDBox(
        ?name 'cadChangeTechLibViaMappingError
        ?dboxBanner strcat(cadChangeTechLibBrand() "::Change Tech Library")
        ?dboxText "Each via mapping field is required and cannot contain whitespace."
        ?buttonLayout 'Close)
      nil
    else
      row
    )
  )
)

procedure(cadChangeTechLibGuiViaSameSourceP(row other)
  and(cadChangeTechLibRowField(row 0)==cadChangeTechLibRowField(other 0)
      cadChangeTechLibRowField(row 1)==cadChangeTechLibRowField(other 1))
)

procedure(cadChangeTechLibGuiViaFindSourceIndex(rows row)
  let((index result)
    index=0
    result=nil
    foreach(item rows
      index=index+1
      when(and(!result cadChangeTechLibGuiViaSameSourceP(item row))
        result=index
      )
    )
    result
  )
)

procedure(cadChangeTechLibGuiViaSelectMappingCB(form)
  let((index row)
    index=cadChangeTechLibGuiViaSelectedIndex(form)
    form~>cadViaSelectedIndex=index
    row=if(index then
      cadChangeTechLibGuiListAt(form~>cadViaMappings index) else nil)
    cadChangeTechLibGuiViaSetRowFields(form row)
    when(row
      cadChangeTechLibGuiSetStatus(form sprintf(nil
        "Editing via mapping row %d" index))
    )
  )
)

procedure(cadChangeTechLibGuiViaAddMappingCB(form)
  let((row index duplicateIndex rows)
    row=cadChangeTechLibGuiViaReadRow(form)
    when(row
      rows=form~>cadViaMappings
      duplicateIndex=cadChangeTechLibGuiViaFindSourceIndex(rows row)
      if(duplicateIndex then
        form~>cadViaSelectedIndex=duplicateIndex
        cadChangeTechLibGuiViaRefreshList(form)
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Via source mapping already exists in row %d; use Update"
          duplicateIndex))
      else
        rows=append1(rows row)
        index=length(rows)
        form~>cadViaMappings=rows
        form~>cadViaSelectedIndex=index
        cadChangeTechLibGuiViaRefreshList(form)
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Via mapping row %d added" index))
      )
    )
  )
)

procedure(cadChangeTechLibGuiViaUpdateMappingCB(form)
  let((row index duplicateIndex rows)
    index=cadChangeTechLibGuiViaSelectedIndex(form)
    rows=form~>cadViaMappings
    if(!and(index index<=length(rows)) then
      cadChangeTechLibGuiSetStatus(form "Select a via mapping row to update")
      nil
    else
      row=cadChangeTechLibGuiViaReadRow(form)
      when(row
        duplicateIndex=cadChangeTechLibGuiViaFindSourceIndex(rows row)
        if(and(duplicateIndex duplicateIndex!=index) then
          cadChangeTechLibGuiSetStatus(form sprintf(nil
            "Via source mapping already exists in row %d" duplicateIndex))
        else
          form~>cadViaMappings=cadChangeTechLibGuiReplaceAt(rows index row)
          form~>cadViaSelectedIndex=index
          cadChangeTechLibGuiViaRefreshList(form)
          cadChangeTechLibGuiSetStatus(form sprintf(nil
            "Via mapping row %d updated" index))
        )
      )
    )
  )
)

procedure(cadChangeTechLibGuiViaRemoveMappingCB(form)
  let((index rows)
    index=cadChangeTechLibGuiViaSelectedIndex(form)
    rows=form~>cadViaMappings
    if(and(index index<=length(rows)) then
      rows=cadChangeTechLibGuiRemoveAt(rows index)
      form~>cadViaMappings=rows
      form~>cadViaSelectedIndex=if(rows then min(index length(rows)) else nil)
      cadChangeTechLibGuiViaRefreshList(form)
      cadChangeTechLibGuiViaSetRowFields(
        form if(form~>cadViaSelectedIndex
                then cadChangeTechLibGuiListAt(
                  rows form~>cadViaSelectedIndex)
                else nil))
      cadChangeTechLibGuiSetStatus(form "Via mapping row removed")
    else
      cadChangeTechLibGuiSetStatus(form "Select a via mapping row first")
    )
  )
)

procedure(cadChangeTechLibGuiViaClearMappingCB(form)
  form~>cadViaMappings=nil
  form~>cadViaSelectedIndex=nil
  cadChangeTechLibGuiViaRefreshList(form)
  cadChangeTechLibGuiViaSetRowFields(form nil)
  cadChangeTechLibGuiSetStatus(form "Via mapping rows cleared")
)

procedure(cadChangeTechLibGuiViaLoadMappingCB(form)
  let((path result mappings errors)
    path=cadChangeTechLibGuiValue(form~>cadCtlViaMappingFile~>value)
    if(path=="" then
      cadChangeTechLibGuiSetStatus(form "Choose a via mapping file to load")
    else
      result=cadChangeTechLibReadViaMappingFile(path)
      mappings=car(result)
      errors=cadr(result)
      if(errors then
        hiDisplayAppDBox(
          ?name 'cadChangeTechLibViaMappingLoadError
          ?dboxBanner strcat(cadChangeTechLibBrand() "::Change Tech Library")
          ?dboxText buildString(errors "\n")
          ?buttonLayout 'Close)
        cadChangeTechLibGuiSetStatus(form "Via mapping file has invalid rows")
      else
        form~>cadViaMappings=mappings
        form~>cadViaSelectedIndex=nil
        cadChangeTechLibGuiViaRefreshList(form)
        cadChangeTechLibGuiViaSetRowFields(form nil)
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Loaded %d via mapping row(s)" length(mappings)))
      )
    )
  )
)

procedure(cadChangeTechLibGuiViaSaveMappingCB(form)
  let((path)
    path=cadChangeTechLibGuiValue(form~>cadCtlViaMappingFile~>value)
    if(path=="" then
      cadChangeTechLibGuiSetStatus(form "Choose a via mapping file to save")
    else
      if(cadChangeTechLibWriteViaMappingFile(path form~>cadViaMappings) then
        cadChangeTechLibGuiSetStatus(form sprintf(nil
          "Saved %d via mapping row(s) to %s"
          length(form~>cadViaMappings) path))
      else
        cadChangeTechLibGuiSetStatus(form
          "Via mapping file could not be written")
      )
    )
  )
)

procedure(cadChangeTechLibViaGuiRevision()
  sicoChangeTechLibViaGuiVersion
)

sicoChangeTechLibViaGuiVersion="20260811.viagui.v5"
)
