; Acceptance-only live-process export, avoiding per-device hex transport overhead.
procedure(pdkPoolNativeWrite(ppnPath ppnValue)
  let((ppnPort)
    ppnPort=outfile(ppnPath "w")
    unless(ppnPort error("acceptance output unavailable"))
    unwindProtect(fprintf(ppnPort "%s" aiPdkJson(ppnValue)) close(ppnPort))))

procedure(pdkPoolNativeBaseline(ppnRoot ppnLibrary)
  let((ppnCount)
    ppnCount=0
    foreach(ppnCell ddGetObj(ppnLibrary)~>cells
      when(ddGetObj(ppnLibrary ppnCell~>name "symbol")
        pdkPoolNativeWrite(strcat(ppnRoot "/" ppnCell~>name ".json")
          aiPdkObj(list(list("context" aiPdkContext(nil))
            list("data" aiPdkDevice(ppnLibrary ppnCell~>name "symbol")))))
        ppnCount=ppnCount+1))
    aiPdkJson(aiPdkObj(list(list("ok" t) list("count" ppnCount))))))
