; Explicit analogLib-only reference setup. No circuit/test defaults in product tools.
resultSession=maeOpenSetup("waveWork" "measurements" "maestro" ?application "Assembler" ?mode "a")
maeCreateTest("response_unique" ?lib "waveWork" ?cell "network" ?view "schematic"
  ?simulator "spectre" ?session resultSession)
maeSetAnalysis("response_unique" "dc" ?enable t
  ?options list(list("saveOppoint" t)) ?session resultSession)
maeSetAnalysis("response_unique" "tran" ?enable t
  ?options list(list("stop" "8u") list("maxstep" "10n") list("errpreset" "conservative"))
  ?session resultSession)
maeAddOutput("plateau" "response_unique" ?outputType "point" ?expr "VDC(\"/OUT\")"
  ?save t ?session resultSession)
maeAddOutput("sample_at_t" "response_unique" ?outputType "point"
  ?expr "value(VT(\"/OUT\") 2.001e-6)" ?save t ?session resultSession)
maeAddOutput("whole_trace" "response_unique" ?outputType "point" ?expr "VT(\"/OUT\")"
  ?save t ?session resultSession)
maeSetCorner("cool" ?enabled t ?session resultSession)
maeSetCorner("warm" ?enabled t ?session resultSession)
maeSetVar("temperature" "10" ?typeName "corner" ?typeValue list("cool") ?session resultSession)
maeSetVar("temperature" "60" ?typeName "corner" ?typeValue list("warm") ?session resultSession)
maeSetCorner("Nominal" ?enabled nil ?session resultSession)
maeSaveSetup(?session resultSession)
maeCloseSession(?session resultSession)
resultWindow=deOpenCellView("waveWork" "measurements" "maestro" "maestro" nil "a")
resultSession=axlGetWindowSession(resultWindow)
procedure(resultDone(rfdSession rfdRun) resultFinished=t)
resultFinished=nil
resultHistory=maeRunSimulation(?session resultSession ?callback "resultDone" ?waitUntilDone nil)
