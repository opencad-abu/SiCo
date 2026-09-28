unless(and(boundp('rceSelectNetFromViewVersion)
             rceSelectNetFromViewVersion=="20260902.form.lifecycle.v1")

procedure(UI_rceSelectNetFromView(lib cell view @optional owner)
  let((cvid cv winid openedWin)
    cvid=when(and(lib cell view) dbOpenCellViewByType(lib cell view))
    if(! cvid then
      GUI_messageApp('e "RCE" "Cannot open the requested schematic view.")
    else
      openedWin=schHiOpenOtherView(cvid list("schematic"))
      cv=geGetEditCellView()
      winid=or(openedWin when(cv geGetEditCellViewWindow(cv)))
      if(! winid then
        GUI_messageApp('e "RCE" "Cannot find the schematic editor window.")
      else
        when(isCallable('rceSelectNetOwnerSet)
          rceSelectNetOwnerSet(winid owner))
        hiSetCurrentWindow(winid)
        hiDisplayWindow(winid)
        enterPoints(
          ?prompts list("Select nets from Schematic..." "Select nets from Schematic...")
          ?addPointProc "CMD_rceSelectNetAdd"
          ?doneProc "CMD_rceSelectNetDone"
        )
      )
    )
  );let
);procedure

rceSelectNetFromViewVersion="20260902.form.lifecycle.v1"
)
