; Read back a disposable text view only through DD/OA APIs.
procedure(aivwProbeJsonString(value)
  let((result index character)
    result="\""
    for(index 1 strlen(or(value ""))
      character=substring(value index 1)
      result=strcat(result
        cond(
          (character=="\\" "\\\\")
          (character=="\"" "\\\"")
          (character=="\n" "\\n")
          (t character))))
    strcat(result "\"")
  )
)

let((libraryName cellName viewName viewType primaryName reportPath
      viewObject primaryObject primaryPath cellView terminalNames report)
  libraryName=getShellEnvVar("AIVW_PROBE_LIBRARY")
  cellName=getShellEnvVar("AIVW_PROBE_CELL")
  viewName=getShellEnvVar("AIVW_PROBE_VIEW")
  viewType=getShellEnvVar("AIVW_PROBE_VIEW_TYPE")
  primaryName=getShellEnvVar("AIVW_PROBE_PRIMARY")
  reportPath=getShellEnvVar("AIVW_PROBE_REPORT")
  unless(and(libraryName cellName viewName viewType primaryName reportPath)
    error("AIVW text-view readback environment is incomplete"))
  viewObject=ddGetObj(libraryName cellName viewName)
  primaryObject=ddGetObj(libraryName cellName viewName primaryName)
  primaryPath=when(primaryObject ddGetObjReadPath(primaryObject))
  cellView=car(errset(
    dbOpenCellViewByType(libraryName cellName viewName nil "r") nil))
  terminalNames=if(cellView then
    foreach(mapcar terminal cellView~>terminals terminal~>name)
    else nil)
  report=outfile(reportPath "w")
  unless(report error("Cannot create AIVW text-view readback report"))
  fprintf(report
    "{\"schema_version\":1,\"library\":%s,\"cell\":%s,\"view\":%s,\"requested_view_type\":%s,\"view_exists\":%s,\"primary_exists\":%s,\"primary_path\":%s,\"shadow_opened\":%s,\"shadow_cell_view_type\":%s,\"terminal_count\":%d}\n"
    aivwProbeJsonString(libraryName)
    aivwProbeJsonString(cellName)
    aivwProbeJsonString(viewName)
    aivwProbeJsonString(viewType)
    if(viewObject then "true" else "false")
    if(primaryObject then "true" else "false")
    aivwProbeJsonString(or(primaryPath ""))
    if(cellView then "true" else "false")
    aivwProbeJsonString(if(cellView then or(cellView~>cellViewType "") else ""))
    length(terminalNames))
  close(report)
  when(cellView dbClose(cellView))
  printf("AIVW_TEXT_VIEW_READBACK view=%s primary=%s shadow=%s terminals=%d\n"
    if(viewObject then "yes" else "no")
    if(primaryObject then "yes" else "no")
    if(cellView then "yes" else "no")
    length(terminalNames))
)
exit(0)
