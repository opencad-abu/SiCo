; Rebind the two scratch comparator testbenches to comparator_new.

procedure(aivwM1GoldenJsonString(value)
  let((text out index character code digits)
    text=if(value then sprintf(nil "%s" value) else "")
    out="\""
    digits="0123456789abcdef"
    for(index 1 strlen(text)
      character=substring(text index 1)
      code=charToInt(getchar(text index))
      cond(
        (character=="\\" out=strcat(out "\\\\"))
        (character=="\"" out=strcat(out "\\\""))
        (character=="\n" out=strcat(out "\\n"))
        (character=="\r" out=strcat(out "\\r"))
        (character=="\t" out=strcat(out "\\t"))
        (and(numberp(code) code<32)
          out=strcat(
            out "\\u00"
            getchar(digits quotient(code 16)+1)
            getchar(digits mod(code 16)+1)))
        (t out=strcat(out character))))
    strcat(out "\"")
  )
)

procedure(aivwM1GoldenRebind(
    outputPort cellName instanceName expectedLib expectedCell)
  let((cellView instance header candidate headerCount oldLib oldCell oldView result)
    cellView=dbOpenCellViewByType("saradc" cellName "schematic" "" "a")
    unless(cellView
      error("cannot open scratch saradc/%s/schematic for append" cellName))
    instance=dbFindAnyInstByName(cellView instanceName)
    unless(instance
      dbClose(cellView)
      error("cannot find instance %s in saradc/%s/schematic" instanceName cellName))
    oldLib=instance~>master~>libName
    oldCell=instance~>master~>cellName
    oldView=instance~>master~>viewName
    unless(oldLib==expectedLib && oldCell==expectedCell && oldView=="symbol"
      dbClose(cellView)
      error("unexpected current master for %s/%s: %s/%s/%s"
        cellName instanceName oldLib oldCell oldView))
    header=instance~>instHeader
    headerCount=0
    foreach(candidate cellView~>instances
      when(candidate~>instHeader==header
        headerCount=headerCount+1))
    unless(headerCount==1
      dbClose(cellView)
      error("instance header for %s/%s has %d instances"
        cellName instanceName headerCount))
    result=dbSetInstHeaderMasterName(
      header "saradc" "comparator_new" "symbol")
    unless(result
      dbClose(cellView)
      error("dbSetInstHeaderMasterName failed for %s/%s" cellName instanceName))
    unless(dbSave(cellView)
      dbClose(cellView)
      error("dbSave failed for scratch saradc/%s/schematic" cellName))
    dbClose(cellView)

    cellView=dbOpenCellViewByType("saradc" cellName "schematic" "" "r")
    instance=dbFindAnyInstByName(cellView instanceName)
    result=instance && instance~>master~>libName=="saradc" &&
      instance~>master~>cellName=="comparator_new" &&
      instance~>master~>viewName=="symbol"
    unless(result
      dbClose(cellView)
      error("readback verification failed for %s/%s" cellName instanceName))
    fprintf(outputPort
      "{\"testbench\":%s,\"instance\":%s,\"old_master\":%s,\"new_master\":\"saradc/comparator_new/symbol\",\"readback_verified\":true}"
      aivwM1GoldenJsonString(cellName)
      aivwM1GoldenJsonString(instanceName)
      aivwM1GoldenJsonString(strcat(oldLib "/" oldCell "/" oldView)))
    dbClose(cellView)
  )
)

let((expectedPath libraryPath outputPath outputPort)
  printf("AIVW_M1_REBIND_START\n")
  expectedPath=getShellEnvVar("AIVW_SCRATCH_SARADC")
  libraryPath=ddGetObjReadPath(ddGetObj("saradc"))
  unless(expectedPath && libraryPath==expectedPath
    error("saradc does not resolve to scratch: expected=%L actual=%L"
      expectedPath libraryPath))
  outputPath=getShellEnvVar("AIVW_REBIND_OUTPUT")
  outputPort=outfile(outputPath "w")
  unless(outputPort
    error("cannot open AIVW_REBIND_OUTPUT: %L" outputPath))
  fprintf(outputPort
    "{\"schema_version\":1,\"provider\":\"dbAccess\",\"scratch_only\":true,\"library_path\":%s,\"bindings\":["
    aivwM1GoldenJsonString(libraryPath))
  aivwM1GoldenRebind(
    outputPort "comparator_noise_TB_new" "I9" "saradcII" "comparator")
  fprintf(outputPort ",")
  aivwM1GoldenRebind(
    outputPort "comparator_offset_TB_new" "I0" "saradc" "comparator")
  fprintf(outputPort "]}\n")
  close(outputPort)
  printf("AIVW_M1_REBIND_DONE\n")
)
exit(0)
