unless(and(boundp('rceCleanSelectCellVersion)
             rceCleanSelectCellVersion=="20260902.form.lifecycle.v1")

procedure(UI_rceCleanSelectCell(form)
  if(and(rceSelectCellFormValidP(form)
         form~>rce_seledCellField) then
    let(()
      form~>rce_seledCellField~>choices='()
    );let
  else
    nil
  )
);procedure

rceCleanSelectCellVersion="20260902.form.lifecycle.v1"
)
