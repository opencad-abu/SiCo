; Read-only M1-AI comparator/interface/testbench qualification worker.

procedure(aivwM1JsonString(value)
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
        (t out=strcat(out character)))
    )
    strcat(out "\"")
  )
)

procedure(aivwM1WriteConnections(outputPort instance)
  let((instTerm first terminalName)
    first=t
    foreach(instTerm instance~>instTerms
      unless(first fprintf(outputPort ","))
      first=nil
      terminalName=if(instTerm~>term then instTerm~>term~>name else "")
      fprintf(outputPort "{\"terminal\":%s,\"net\":%s}"
        aivwM1JsonString(terminalName)
        aivwM1JsonString(if(instTerm~>net then instTerm~>net~>name else "")))
  )
)
)

procedure(aivwM1WriteCellView(outputPort libraryName cellName viewName)
  let((cellView first terminal instance portName master)
    fprintf(outputPort
      "{\"library\":%s,\"cell\":%s,\"view\":%s,"
      aivwM1JsonString(libraryName)
      aivwM1JsonString(cellName)
      aivwM1JsonString(viewName))
    cellView=dbOpenCellViewByType(libraryName cellName viewName "" "r")
    if(cellView
      then
        fprintf(outputPort "\"opened\":true,\"terminals\":[")
        first=t
        foreach(terminal cellView~>terminals
          unless(first fprintf(outputPort ","))
          first=nil
          fprintf(outputPort "{\"name\":%s,\"direction\":%s}"
            aivwM1JsonString(terminal~>name)
            aivwM1JsonString(terminal~>direction)))
        fprintf(outputPort "],\"port_order\":[")
        first=t
        foreach(portName cellView~>portOrder
          unless(first fprintf(outputPort ","))
          first=nil
          fprintf(outputPort "%s" aivwM1JsonString(portName)))
        fprintf(outputPort "],\"instances\":[")
        first=t
        foreach(instance cellView~>instances
          unless(first fprintf(outputPort ","))
          first=nil
          master=instance~>master
          fprintf(outputPort
            "{\"name\":%s,\"library\":%s,\"cell\":%s,\"view\":%s,\"connections\":["
            aivwM1JsonString(instance~>name)
            aivwM1JsonString(master~>libName)
            aivwM1JsonString(master~>cellName)
            aivwM1JsonString(master~>viewName))
          aivwM1WriteConnections(outputPort instance)
          fprintf(outputPort "]}"))
        fprintf(outputPort "]}")
        dbClose(cellView)
      else
        fprintf(outputPort
          "\"opened\":false,\"terminals\":[],\"port_order\":[],\"instances\":[]}"))
  )
)

let((outputPath outputPort first cellSpec)
  printf("AIVW_M1_WORKER_START\n")
  outputPath=getShellEnvVar("AIVW_M1_OUTPUT")
  printf("AIVW_M1_OUTPUT=%L\n" outputPath)
  unless(outputPath
    error("AIVW_M1_OUTPUT is not set"))
  outputPort=outfile(outputPath "w")
  unless(outputPort
    error("cannot open AIVW_M1_OUTPUT: %L" outputPath))
  fprintf(outputPort
    "{\"schema_version\":1,\"provider\":\"dbAccess\",\"read_only\":true,\"cellviews\":[")
  first=t
  foreach(cellSpec
    '(("saradc" "comparator" "schematic")
      ("saradc" "comparator" "symbol")
      ("saradcII" "comparator" "schematic")
      ("saradcII" "comparator" "symbol")
      ("saradc" "comparator_new" "schematic")
      ("saradc" "comparator_new" "symbol")
      ("saradc" "comparator_noise_TB_new" "schematic")
      ("saradc" "comparator_offset_TB_new" "schematic"))
    unless(first fprintf(outputPort ","))
    first=nil
    aivwM1WriteCellView(outputPort car(cellSpec) cadr(cellSpec) caddr(cellSpec)))
  fprintf(outputPort "]}\n")
  close(outputPort)
  printf("AIVW_M1_WORKER_DONE\n")
)
exit(0)
