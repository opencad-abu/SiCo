; PAS GTE device property table (DPT) reader.
; Only PAS-developed PDKs carry the pdk.dat cellview marker; apart from that the
; DPT field names are PDK-defined, so this reader returns a bounded candidate
; set plus a diagnostic dump for review. Read-only: no OA writes, no callbacks.
procedure(aiPdkPasRevision() "20261009.ai.pdk.pas.v1")

procedure(aiPdkPasDpt(appLib appCell)
  let((appDd appPdk appCdf appDpt appVals appText appKeys)
    appDd=ddGetObj(appLib appCell)
    unless(appDd error("observed cell is unavailable"))
    appPdk=when(appDd ddGetObj(appLib appCell nil "pdk.dat"))
    appCdf=when(appDd car(errset(cdfGetBaseCellCDF(appDd) nil)))
    appDpt=when(and(appCdf isCallable('PasCdfGetDpt))
      car(errset(PasCdfGetDpt(appCdf) nil)))
    appKeys=when(appDpt
      let((appList appOut appCount)
        appList=car(errset(appDpt->? nil))
        when(listp(appList)
          appOut=nil appCount=0
          foreach(appKey appList
            when(appCount<200
              appOut=cons(sprintf(nil "%N" appKey) appOut)
              appCount=appCount+1))
          reverse(appOut))))
    appVals=when(appDpt list(
      list("grid" car(errset(appDpt->grid nil)))
      list("minL" car(errset(appDpt->minL nil))) list("maxL" car(errset(appDpt->maxL nil)))
      list("minW" car(errset(appDpt->minW nil))) list("maxW" car(errset(appDpt->maxW nil)))
      list("minFw" car(errset(appDpt->minFw nil))) list("maxFw" car(errset(appDpt->maxFw nil)))
      list("minFinger" car(errset(appDpt->minFinger nil))) list("maxFinger" car(errset(appDpt->maxFinger nil)))
      list("minFingers" car(errset(appDpt->minFingers nil))) list("maxFingers" car(errset(appDpt->maxFingers nil)))
      list("minNF" car(errset(appDpt->minNF nil))) list("maxNF" car(errset(appDpt->maxNF nil)))
      list("minM" car(errset(appDpt->minM nil))) list("maxM" car(errset(appDpt->maxM nil)))
      list("minSegW" car(errset(appDpt->minSegW nil))) list("maxSegW" car(errset(appDpt->maxSegW nil)))
      list("minSegL" car(errset(appDpt->minSegL nil))) list("maxSegL" car(errset(appDpt->maxSegL nil)))
      list("minSegments" car(errset(appDpt->minSegments nil))) list("maxSegments" car(errset(appDpt->maxSegments nil)))
      list("minR" car(errset(appDpt->minR nil))) list("maxR" car(errset(appDpt->maxR nil)))
      list("minArea" car(errset(appDpt->minArea nil))) list("maxArea" car(errset(appDpt->maxArea nil)))
      list("minRow" car(errset(appDpt->minRow nil))) list("maxRow" car(errset(appDpt->maxRow nil)))
      list("minCol" car(errset(appDpt->minCol nil))) list("maxCol" car(errset(appDpt->maxCol nil)))
      list("minNN" car(errset(appDpt->minNN nil))) list("maxNN" car(errset(appDpt->maxNN nil)))
      list("minFL" car(errset(appDpt->minFL nil))) list("maxFL" car(errset(appDpt->maxFL nil)))
      list("minLf" car(errset(appDpt->minLf nil))) list("maxLf" car(errset(appDpt->maxLf nil)))
      list("minWf" car(errset(appDpt->minWf nil))) list("maxWf" car(errset(appDpt->maxWf nil)))
      list("minLF" car(errset(appDpt->minLF nil))) list("maxLF" car(errset(appDpt->maxLF nil)))
      list("minWF" car(errset(appDpt->minWF nil))) list("maxWF" car(errset(appDpt->maxWF nil)))
      list("minScale" car(errset(appDpt->minScale nil))) list("maxScale" car(errset(appDpt->maxScale nil)))))
    appText=when(appDpt
      let((appPrint) appPrint=sprintf(nil "%L" appDpt)
        if(and(stringp(appPrint) strlen(appPrint)>4096) substring(appPrint 1 4096) appPrint)))
    aiCircuitObject(list(
      list("ok" aiCircuitBool(t))
      list("library" appLib) list("cell" appCell)
      list("pdk_dat" aiCircuitBool(appPdk))
      list("base_cdf" aiCircuitBool(appCdf))
      list("dpt" aiCircuitBool(appDpt))
      list("extension_ref" "pas:device-property-table:v1")
      list("keys" appKeys)
      list("fields" appVals)
      list("dump" appText))))
)
