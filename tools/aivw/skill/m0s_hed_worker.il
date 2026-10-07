; Read-only HED worker for the M0-S PLL configuration qualification.

procedure(aivwM0sJsonString(value)
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

let((outputPath outputPort config configLib configCell configView
     topLib topCell topView traversalSource traversalLoad traversalStatus)
  outputPath=getShellEnvVar("AIVW_HED_OUTPUT")
  configLib=getShellEnvVar("AIVW_CONFIG_LIB")
  configCell=getShellEnvVar("AIVW_CONFIG_CELL")
  configView=getShellEnvVar("AIVW_CONFIG_VIEW")
  traversalSource=strcat(getShellEnvVar("CDSHOME") "/tools/hdb/examples/hdbTraverse.il")
  config=hdbOpen(configLib configCell configView "r" "CDBA")
  unless(config
    outputPort=outfile(outputPath "w")
    fprintf(outputPort
      "{\"schema_version\":1,\"provider\":\"Virtuoso/HED\",\"config_opened\":false}\n")
    close(outputPort)
    exit(2)
  )
  topLib=hdbGetTopLibName(config)
  topCell=hdbGetTopCellName(config)
  topView=hdbGetTopViewName(config)
  hdbClose(config)

  traversalLoad=errset(load(traversalSource) t)
  if(traversalLoad
    then
      traversalStatus=hdbtTraverseConfigStart(
        "" "  " configLib configCell configView "Min" "1" "CDBA")
    else
      traversalStatus="LOAD_FAILED"
  )

  outputPort=outfile(outputPath "w")
  fprintf(outputPort
    "{\"schema_version\":1,\"provider\":\"Virtuoso/HED\",\"config_opened\":true,")
  fprintf(outputPort
    "\"config\":{\"library\":%s,\"cell\":%s,\"view\":%s},"
    aivwM0sJsonString(configLib)
    aivwM0sJsonString(configCell)
    aivwM0sJsonString(configView))
  fprintf(outputPort
    "\"top\":{\"library\":%s,\"cell\":%s,\"view\":%s},"
    aivwM0sJsonString(topLib)
    aivwM0sJsonString(topCell)
    aivwM0sJsonString(topView))
  fprintf(outputPort
    "\"traversal\":{\"status\":%s,\"node_count\":%d,\"source\":%s}}\n"
    aivwM0sJsonString(traversalStatus)
    if(numberp(gNodeCount) then gNodeCount else 0)
    aivwM0sJsonString(traversalSource))
  close(outputPort)
  if(equal(traversalStatus "OK") then exit(0) else exit(3))
)
