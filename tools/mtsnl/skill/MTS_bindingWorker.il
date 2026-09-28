procedure(mtsRuntimeBinding(library cell ports overwrite report)
let((mtsLib mtsCell mtsPorts mtsCV mtsSym mtsCDF mtsUserCDF mtsOldCDF mtsNet mtsTerm mtsFig mtsMembers mtsReport mtsOld)
  mtsLib=library
  mtsCell=cell
  mtsPorts=ports
  when(ddGetObj(mtsLib mtsCell "spectreText") error("MTS conflicting spectreText view; choose a separate target cell"))
  mtsOld=ddGetObj(mtsLib mtsCell "spectre")
  when(mtsOld && !overwrite error("MTS stopping view already exists"))
  mtsSym=dbOpenCellViewByType(mtsLib mtsCell "symbol" "" "r")
  when(mtsSym
    mtsMembers=nil
    foreach(mtsT mtsSym~>terminals
      mtsMembers=append(mtsMembers dbProduceMemName(mtsT~>name))
    )
    unless(equal(sort(copy(mtsMembers) 'alphalessp) sort(copy(mtsPorts) 'alphalessp))
      error("MTS symbol terminals do not match corner library ports"))
  )
  mtsCDF=cdfGetBaseCellCDF(ddGetObj(mtsLib mtsCell))
  mtsUserCDF=cdfGetUserCellCDF(ddGetObj(mtsLib mtsCell))
  when((mtsCDF && cdfFindParamByName(mtsCDF "model") || mtsUserCDF && cdfFindParamByName(mtsUserCDF "model"))
    error("MTS target CDF has a model parameter which can override the library binding; choose a separate target cell"))
  when((mtsCDF || mtsUserCDF) && !overwrite
    error("MTS target already has CDF data; explicit netlist overwrite is required"))
  when(mtsCDF && mtsCDF->simInfo && mtsCDF->simInfo->spectre && !overwrite
    error("MTS target already has Spectre CDF; explicit netlist overwrite is required"))
  if(mtsSym then
    ; A symbol view is the authoritative graphical representation.  Copying
    ; it through OA preserves all shapes, labels, pins, terminal directions,
    ; and symbol properties in the simulator stopping view.  The destination
    ; view remains a separate ``spectre`` view, so the CDF below still binds
    ; it to the selected external corner-library subckt.
    mtsCV=dbCopyCellView(mtsSym mtsLib mtsCell "spectre" nil nil t)
    unless(mtsCV error("MTS cannot copy symbol to stopping view"))
    dbClose(mtsCV)
    dbClose(mtsSym)
  else
    ; Keep the legacy no-symbol path usable: create a minimal stopping view
    ; with one pin shape per validated top-level port.
    mtsCV=dbOpenCellViewByType(mtsLib mtsCell "spectre" "schematicSymbol" "w")
    unless(mtsCV error("MTS cannot open stopping view for writing"))
    foreach(mtsName mtsPorts
      mtsNet=dbCreateNet(mtsCV mtsName)
      mtsTerm=dbCreateTerm(mtsNet mtsName "inputOutput")
      mtsFig=dbCreateRect(mtsCV list("pin" "drawing") list(0:0 0.1:0.1))
      unless(dbCreatePin(mtsNet mtsFig mtsName mtsTerm) error("MTS cannot create stopping terminal"))
    )
    unless(dbSave(mtsCV) error("MTS cannot save stopping view"))
    dbClose(mtsCV)
  )
  when(overwrite
    foreach(mtsOldCDF list(mtsCDF mtsUserCDF)
      when(mtsOldCDF
        unless(cdfDeleteCDF(mtsOldCDF) error("MTS cannot replace existing binding CDF"))))
    mtsCDF=nil
  )
  unless(mtsCDF mtsCDF=cdfCreateBaseCellCDF(ddGetObj(mtsLib mtsCell)))
  unless(mtsCDF error("MTS cannot create base CDF"))
  unless(mtsCDF->simInfo mtsCDF->simInfo=list(nil))
  mtsCDF->simInfo->spectre=list(nil 'termOrder mtsPorts 'componentName cell)
  unless(cdfSaveCDF(mtsCDF) error("MTS cannot save binding CDF"))
  mtsCDF=cdfGetBaseCellCDF(ddGetObj(mtsLib mtsCell))
  unless(equal(mtsCDF->simInfo->spectre->termOrder mtsPorts) && mtsCDF->simInfo->spectre->componentName==cell
    error("MTS binding CDF readback mismatch"))
  mtsReport=outfile(report "w")
  fprintf(mtsReport "binding=succeeded\n")
  fprintf(mtsReport "component=%s\n" mtsCDF->simInfo->spectre->componentName)
  fprintf(mtsReport "ports=%L\n" mtsCDF->simInfo->spectre->termOrder)
  fprintf(mtsReport "view_source=%s\n" if(mtsSym then "symbol-copy" else "minimal-pins"))
  close(mtsReport)
)
t)
