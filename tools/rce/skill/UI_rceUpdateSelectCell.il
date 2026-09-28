unless(and(boundp('rceUpdateSelectCellVersion)
             rceUpdateSelectCellVersion=="20260902.form.lifecycle.v1")

procedure(UI_rceUpdateSelectCell(form)
  if(and(rceSelectCellFormValidP(form)
         form~>rce_selCellField
         form~>rce_selLibField) then
    let((library cells)
      library=ddGetObj(form~>rce_selLibField~>value)
      cells=when(library library~>cells~>name)
      form~>rce_selCellField~>choices=or(cells '())
      cells
    );let
  else
    nil
  )
);procedure

rceUpdateSelectCellVersion="20260902.form.lifecycle.v1"
)
