; Check and save only the two rebound schematics in the scratch saradc library.

procedure(aivwM1GoldenCheckScratch(cellName)
  let((cellView result errorCount warningCount)
    cellView=dbOpenCellViewByType("saradc" cellName "schematic" "" "a")
    unless(cellView
      error("cannot open scratch saradc/%s/schematic" cellName))
    result=schCheck(cellView)
    errorCount=if(result then car(result) else -1)
    warningCount=if(result then cadr(result) else -1)
    printf("AIVW_M1_SCHCHECK %s errors=%L warnings=%L\n"
      cellName errorCount warningCount)
    unless(result && errorCount==0
      dbClose(cellView)
      error("schCheck failed for scratch saradc/%s/schematic: %L"
        cellName result))
    unless(dbSave(cellView)
      dbClose(cellView)
      error("dbSave failed for scratch saradc/%s/schematic" cellName))
    dbClose(cellView)
  )
)

let((expectedPath libraryPath)
  printf("AIVW_M1_SCHCHECK_START\n")
  expectedPath=getShellEnvVar("AIVW_SCRATCH_SARADC")
  libraryPath=ddGetObjReadPath(ddGetObj("saradc"))
  unless(expectedPath && libraryPath==expectedPath
    error("saradc does not resolve to scratch: expected=%L actual=%L"
      expectedPath libraryPath))
  aivwM1GoldenCheckScratch("comparator_noise_TB_new")
  aivwM1GoldenCheckScratch("comparator_offset_TB_new")
  printf("AIVW_M1_SCHCHECK_DONE\n")
)
exit(0)
