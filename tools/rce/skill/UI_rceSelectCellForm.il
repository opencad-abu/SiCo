unless(and(boundp('rceSelectCellFormVersion)
             rceSelectCellFormVersion=="20260902.form.lifecycle.v1")

procedure(rceSelectCellFormValidP(form)
  and(form
      if(isCallable('hiIsForm) then hiIsForm(form) else t))
)

procedure(rceSelectCellOwnerForm(form)
  when(rceSelectCellFormValidP(form)
    get(form 'rceSelectCellOwnerForm))
)

procedure(UI_rceFinishSelectCellCB(form)
  when(rceSelectCellFormValidP(form)
    UI_rceFinishSelectCell(form)
    hiFormClose(form))
  t
)

procedure(UI_rceCleanSelectCellCB(form)
  when(rceSelectCellFormValidP(form)
    UI_rceCleanSelectCell(form))
  t
)

procedure(UI_rceCreateSelCellForm(lib @optional owner)
  let((rce_selLibField rce_selCellField rce_seledCellField rce_selCellForm
       rce_selCellMainForm existing library cells)
    lib=if(stringp(lib) then lib else "")
    existing=when(boundp('UI_rceSelCellForm) UI_rceSelCellForm)
    if(rceSelectCellFormValidP(existing) then
      putpropq(existing
               when(rceSelectCellFormValidP(owner) owner)
               rceSelectCellOwnerForm)
      when(and(lib existing~>rce_selLibField)
        existing~>rce_selLibField~>value=lib
        UI_rceUpdateSelectCell(existing))
      unless(hiIsFormDisplayed(existing)
        hiDisplayForm(existing))
      existing
    else
      when(existing
        UI_rceSelCellForm=nil)
      rce_selLibField=ddHiCreateLibraryComboField(
                        ?name 'rce_selLibField
                        ?prompt "Library: "
                        ?defValue lib
                        ?callback "UI_rceUpdateSelectCell(hiGetCurrentForm())")
      library=ddGetObj(lib)
      cells=when(library library~>cells~>name)
      rce_selCellField=hiCreateListBoxField(
                         ?name 'rce_selCellField
                         ?choices or(cells '())
                         ?doubleClickCB "UI_rceSelectCell(hiGetCurrentForm())")
      rce_seledCellField=hiCreateListBoxField(
                           ?name 'rce_seledCellField
                           ?choices '())
      rce_selCellForm=hiCreateHorizontalBoxLayout(
                         'rce_hardwareForm
                         ?frame "Select Cell"
                         ?items list(rce_selCellField rce_seledCellField)
                         ?horiz_align 'center
                         ?spacing 0)
      rce_selCellMainForm=hiCreateVerticalBoxLayout(
                            'rce_mainForm
                            ?items list(rce_selLibField rce_selCellForm)
                            ?horiz_align 'center
                            ?spacing 10)
      UI_rceSelCellForm=hiCreateLayoutForm(
                            'UI_rceSelCellForm
                            strcat(SICO_logo() "::RCE:Select Cell")
                            rce_selCellMainForm
                            ?sizePolicy    'expanding
                            ?initialSize   400:600
                            ?buttonLayout  list('Empty
                              list('Finish 'UI_rceFinishSelectCellCB)
                              list('Clean 'UI_rceCleanSelectCellCB)
                              list('Close 'hiFormClose)))
      putpropq(UI_rceSelCellForm owner rceSelectCellOwnerForm)
      hiDisplayForm(UI_rceSelCellForm)
      UI_rceSelCellForm)
  )
)

rceSelectCellFormVersion="20260902.form.lifecycle.v1"
)
