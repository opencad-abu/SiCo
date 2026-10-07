; Read-only dbAccess catalog and top-schematic worker for M0-S.

procedure(aivwM0sCatalogJsonString(value)
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
            out
            "\\u00"
            getchar(digits quotient(code 16)+1)
            getchar(digits mod(code 16)+1)
          )
        )
        (t out=strcat(out character))
      )
    )
    strcat(out "\"")
  )
)

procedure(aivwM0sWriteLibrary(outputPort libraryName firstLibrary)
  let((library cell view firstCell firstView readPath)
    library=ddGetObj(libraryName)
    when(library
      unless(firstLibrary fprintf(outputPort ","))
      readPath=ddGetObjReadPath(library)
      fprintf(outputPort "{\"name\":%s,\"read_path\":%s,\"cells\":["
        aivwM0sCatalogJsonString(libraryName)
        aivwM0sCatalogJsonString(readPath))
      firstCell=t
      foreach(cell ddGetObjChildren(library)
        when(equal(ddGetObjType(cell) 'ddCellType)
          unless(firstCell fprintf(outputPort ","))
          firstCell=nil
          fprintf(outputPort "{\"name\":%s,\"views\":["
            aivwM0sCatalogJsonString(ddGetObjName(cell)))
          firstView=t
          foreach(view ddGetObjChildren(cell)
            when(equal(ddGetObjType(view) 'ddViewType)
              unless(firstView fprintf(outputPort ","))
              firstView=nil
              fprintf(outputPort "%s"
                aivwM0sCatalogJsonString(ddGetObjName(view)))
            )
          )
          fprintf(outputPort "]}")
        )
      )
      fprintf(outputPort "]}")
      t
    )
  )
)

procedure(aivwM0sWriteTopSchematic(outputPort)
  let((cellView terminal instance instTerm first terminalName master)
    cellView=dbOpenCellViewByType(
      "zambezi45_sim" "pll_sim" "schematic" "" "r")
    if(cellView
      then
        fprintf(outputPort "{\"opened\":true,\"terminals\":[")
        first=t
        foreach(terminal cellView~>terminals
          unless(first fprintf(outputPort ","))
          first=nil
          fprintf(outputPort "{\"name\":%s,\"direction\":%s}"
            aivwM0sCatalogJsonString(terminal~>name)
            aivwM0sCatalogJsonString(terminal~>direction))
        )
        fprintf(outputPort "],\"instances\":[")
        first=t
        foreach(instance cellView~>instances
          unless(first fprintf(outputPort ","))
          first=nil
          master=instance~>master
          fprintf(outputPort
            "{\"name\":%s,\"master\":{\"library\":%s,\"cell\":%s,\"view\":%s},\"connections\":["
            aivwM0sCatalogJsonString(instance~>name)
            aivwM0sCatalogJsonString(master~>libName)
            aivwM0sCatalogJsonString(master~>cellName)
            aivwM0sCatalogJsonString(master~>viewName))
          let((firstConnection)
            firstConnection=t
            foreach(instTerm instance~>instTerms
              unless(firstConnection fprintf(outputPort ","))
              firstConnection=nil
              terminalName=if(instTerm~>term then instTerm~>term~>name else "")
              fprintf(outputPort "{\"terminal\":%s,\"net\":%s}"
                aivwM0sCatalogJsonString(terminalName)
                aivwM0sCatalogJsonString(
                  if(instTerm~>net then instTerm~>net~>name else "")))
            )
          )
          fprintf(outputPort "]}")
        )
        fprintf(outputPort "]}")
        dbClose(cellView)
      else
        fprintf(outputPort "{\"opened\":false,\"terminals\":[],\"instances\":[]}")
    )
  )
)

let((outputPath outputPort firstLibrary wrote)
  outputPath=getShellEnvVar("AIVW_CATALOG_OUTPUT")
  outputPort=outfile(outputPath "w")
  fprintf(outputPort
    "{\"schema_version\":1,\"provider\":\"dbAccess\",\"authoritative\":true,\"libraries\":[")
  firstLibrary=t
  foreach(libraryName '("zambezi45_sim" "zambezi45" "gsclib045")
    wrote=aivwM0sWriteLibrary(outputPort libraryName firstLibrary)
    when(wrote firstLibrary=nil)
  )
  fprintf(outputPort "],\"top_schematic\":")
  aivwM0sWriteTopSchematic(outputPort)
  fprintf(outputPort "}\n")
  close(outputPort)
)
exit(0)
