; Read-only dbAccess catalog worker for the LDO source library.
;
; The controller supplies the library and a comma-separated cell list.  This
; worker deliberately emits bounded metadata only; it never creates or saves
; a cellView and does not make a verdict about analog behavior.

procedure(aivwLdoJsonString(value)
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
          out=strcat(out "\\u00" getchar(digits quotient(code 16)+1) getchar(digits mod(code 16)+1)))
        (t out=strcat(out character))
      )
    )
    strcat(out "\"")
  )
)

procedure(aivwLdoCellJson(outputPort libraryName cellName firstCell)
  let((cellView terminal instance firstTerminal firstInstance terminalName master)
    unless(firstCell fprintf(outputPort ","))
    fprintf(outputPort "{\"name\":%s,\"views\":[" aivwLdoJsonString(cellName))
    let((cell firstView)
      cell=ddGetObj(libraryName cellName)
      firstView=t
      when(cell
        foreach(view ddGetObjChildren(cell)
          when(equal(ddGetObjType(view) 'ddViewType)
            unless(firstView fprintf(outputPort ","))
            firstView=nil
            fprintf(outputPort "%s" aivwLdoJsonString(ddGetObjName(view)))
          )
        )
      )
    )
    fprintf(outputPort "],\"schematic\":")
    cellView=dbOpenCellViewByType(libraryName cellName "schematic" "" "r")
    if(cellView
      then
        fprintf(outputPort "{\"opened\":true,\"terminal_count\":%d,\"instance_count\":%d,\"modified\":false,\"terminals\":[" length(cellView~>terminals) length(cellView~>instances))
        firstTerminal=t
        foreach(terminal cellView~>terminals
          unless(firstTerminal fprintf(outputPort ","))
          firstTerminal=nil
          fprintf(outputPort "{\"name\":%s,\"direction\":%s}"
            aivwLdoJsonString(terminal~>name)
            aivwLdoJsonString(terminal~>direction))
        )
        fprintf(outputPort "],\"instances\":[")
        firstInstance=t
        foreach(instance cellView~>instances
          unless(firstInstance fprintf(outputPort ","))
          firstInstance=nil
          master=instance~>master
          fprintf(outputPort "{\"name\":%s,\"master\":{\"library\":%s,\"cell\":%s,\"view\":%s}}"
            aivwLdoJsonString(instance~>name)
            aivwLdoJsonString(if(master then master~>libName else ""))
            aivwLdoJsonString(if(master then master~>cellName else ""))
            aivwLdoJsonString(if(master then master~>viewName else "")))
        )
        fprintf(outputPort "]}")
        dbClose(cellView)
      else
        fprintf(outputPort "{\"opened\":false,\"terminal_count\":0,\"instance_count\":0,\"modified\":null,\"terminals\":[],\"instances\":[]}")
    )
    fprintf(outputPort "}")
    t
  )
)

let((outputPath outputPort libraryName rawCells cells firstCell)
  outputPath=getShellEnvVar("AIVW_LDO_CATALOG_OUTPUT")
  libraryName=getShellEnvVar("AIVW_LDO_LIBRARY")
  rawCells=getShellEnvVar("AIVW_LDO_CELLS")
  unless(and(outputPath libraryName rawCells)
    printf("AIVW_LDO_CATALOG_OUTPUT, AIVW_LDO_LIBRARY, and AIVW_LDO_CELLS are required\n")
    exit(2)
  )
  cells=when(rawCells parseString(rawCells ","))
  outputPort=outfile(outputPath "w")
  fprintf(outputPort "{\"schema_version\":1,\"provider\":\"dbAccess\",\"authority\":\"dbAccess-read-only-worker\",\"read_only\":true,\"authoritative\":true,\"library\":%s,\"cells\":[" aivwLdoJsonString(libraryName))
  firstCell=t
  foreach(cellName cells
    aivwLdoCellJson(outputPort libraryName cellName firstCell)
    firstCell=nil
  )
  fprintf(outputPort "]}\n")
  close(outputPort)
)
exit(0)
