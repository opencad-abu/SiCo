; Read-only HED worker for one LDO config view.
;
; The official hdb traversal example is loaded from the qualified IC install.
; Traversal text is intentionally written to the Virtuoso console log so it
; remains raw evidence and is not copied into agent context.

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

procedure(aivwLdoJsonBool(value)
  if(value then "true" else "false")
)

let((outputPath outputPort configLib configCell configView traversalSource config secondConfig topLib topCell topView secondTopLib secondTopCell secondTopView loaded traversalStatus sourceGeneration doubleRead)
  outputPath=getShellEnvVar("AIVW_LDO_HED_OUTPUT")
  configLib=getShellEnvVar("AIVW_LDO_CONFIG_LIBRARY")
  configCell=getShellEnvVar("AIVW_LDO_CONFIG_CELL")
  configView=getShellEnvVar("AIVW_LDO_CONFIG_VIEW")
  unless(and(outputPath configLib configCell configView)
    printf("AIVW_LDO_HED_OUTPUT, AIVW_LDO_CONFIG_LIBRARY, AIVW_LDO_CONFIG_CELL, and AIVW_LDO_CONFIG_VIEW are required\n")
    exit(2)
  )
  traversalSource=strcat(getShellEnvVar("CDSHOME") "/tools/hdb/examples/hdbTraverse.il")
  config=hdbOpen(configLib configCell configView "r" "CDBA")
  unless(config
    outputPort=outfile(outputPath "w")
    fprintf(outputPort "{\"schema_version\":1,\"provider\":\"Virtuoso/HED\",\"authority\":\"hdb-read-only-worker\",\"config_opened\":false,\"config\":{\"library\":%s,\"cell\":%s,\"view\":%s}}\n" aivwLdoJsonString(configLib) aivwLdoJsonString(configCell) aivwLdoJsonString(configView))
    close(outputPort)
    exit(2)
  )
  topLib=hdbGetTopLibName(config)
  topCell=hdbGetTopCellName(config)
  topView=hdbGetTopViewName(config)
  hdbClose(config)
  secondConfig=hdbOpen(configLib configCell configView "r" "CDBA")
  secondTopLib=when(secondConfig hdbGetTopLibName(secondConfig))
  secondTopCell=when(secondConfig hdbGetTopCellName(secondConfig))
  secondTopView=when(secondConfig hdbGetTopViewName(secondConfig))
  when(secondConfig hdbClose(secondConfig))
  doubleRead=and(secondConfig equal(topLib secondTopLib) equal(topCell secondTopCell) equal(topView secondTopView))
  sourceGeneration=getShellEnvVar("AIVW_LDO_SOURCE_GENERATION")
  loaded=errset(load(traversalSource) t)
  if(loaded
    then traversalStatus=hdbtTraverseConfigStart("" "  " configLib configCell configView "Full" "4" "CDBA")
    else traversalStatus="LOAD_FAILED"
  )
  outputPort=outfile(outputPath "w")
  fprintf(outputPort "{\"schema_version\":1,\"provider\":\"Virtuoso/HED\",\"authority\":\"authenticated-cdns-ipc-read-only\",\"config_opened\":true,\"config\":{\"library\":%s,\"cell\":%s,\"view\":%s},\"top\":{\"library\":%s,\"cell\":%s,\"view\":%s},\"traversal\":{\"status\":%s,\"source\":%s},\"formal_saved_state\":true,\"double_read_equal\":%s,\"source_generation\":%s}\n"
    aivwLdoJsonString(configLib) aivwLdoJsonString(configCell) aivwLdoJsonString(configView)
    aivwLdoJsonString(topLib) aivwLdoJsonString(topCell) aivwLdoJsonString(topView)
    aivwLdoJsonString(traversalStatus) aivwLdoJsonString(traversalSource)
    aivwLdoJsonBool(doubleRead)
    if(sourceGeneration then aivwLdoJsonString(sourceGeneration) else "null"))
  close(outputPort)
  if(and(equal(traversalStatus "OK") doubleRead) then exit(0) else exit(3))
)
