unless(and(boundp('rceFinishSelectCellVersion)
             rceFinishSelectCellVersion=="20260902.form.lifecycle.v1")

procedure(UI_rceFinishSelectCell(form)
  if(and(rceSelectCellFormValidP(form)
         form~>rce_seledCellField) then
    let((tmp owner)
      tmp=buildString(or(form~>rce_seledCellField~>choices '()) " ")
      owner=get(form 'rceSelectCellOwnerForm)
      when(and(rceSelectCellFormValidP(owner)
               owner~>cellSelFile)
        owner~>cellSelFile~>value=tmp
      )
      tmp
    )
  else
    nil
  )
);procedure

rceFinishSelectCellVersion="20260902.form.lifecycle.v1"
)
