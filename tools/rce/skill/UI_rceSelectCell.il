unless(and(boundp('rceSelectCellVersion)
             rceSelectCellVersion=="20260902.form.lifecycle.v1")

procedure(UI_rceSelectCell(form)
  if(and(rceSelectCellFormValidP(form)
         form~>rce_seledCellField
         form~>rce_selCellField) then
    let((old new selected)
      old=or(form~>rce_seledCellField~>choices '())
      selected=car(or(form~>rce_selCellField~>value '()))
      when(and(selected ! member(selected old))
        new=cons(selected old)
        form~>rce_seledCellField~>choices=new
      )
    );let
  else
    nil
  )
);procedure

rceSelectCellVersion="20260902.form.lifecycle.v1"
)
