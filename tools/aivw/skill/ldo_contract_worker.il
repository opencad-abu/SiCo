; Read-only TEST_LDO stimulus/CDF and interface evidence. No ADE state load/save.
procedure(aivwContractWrite(aivwDiscoveryRoot aivwDiscoveryLabel aivwDiscoveryText)
  let((aivwDiscoveryStream)
    aivwDiscoveryStream=outfile(strcat(aivwDiscoveryRoot "/" aivwDiscoveryLabel ".json") "w")
    unless(aivwDiscoveryStream error("Cannot write contract evidence"))
    fprintf(aivwDiscoveryStream "%s\n" aivwDiscoveryText)
    close(aivwDiscoveryStream)
  )
)

procedure(aivwContractProperties(contractCv)
  let((contractBudget contractRecords contractRecord contractFields contractCdfData contractFirstParam)
    contractBudget=aiInspectNewSnapshotBudget()
    contractRecords=""
    unless(length(contractCv~>instances)<=200 error("Too many stimulus instances"))
    foreach(contractInst contractCv~>instances
      contractFields=""
      printf("AIVW_CONTRACT_CDF %s\n" contractInst~>name)
      contractCdfData=cdfGetInstCDF(contractInst)
      when(contractCdfData
        unless(length(contractCdfData~>parameters)<=2048 error("Too many CDF parameters"))
        contractFirstParam=t
        foreach(contractParam contractCdfData~>parameters
          unless(contractFirstParam contractFields=strcat(contractFields ","))
          contractFirstParam=nil
          contractFields=strcat(contractFields aiInspectJsonString(contractParam~>name contractBudget) ":"
            aiInspectJsonString(contractParam~>value contractBudget))
        )
      )
      contractRecord=sprintf(nil "{\"name\":%s,\"library\":%s,\"cell\":%s,\"cdf\":{%s}}"
        aiInspectJsonString(contractInst~>name contractBudget)
        aiInspectJsonString(contractInst~>libName contractBudget)
        aiInspectJsonString(contractInst~>cellName contractBudget) contractFields)
      when(strlen(contractRecords)>0 contractRecords=strcat(contractRecords ","))
      contractRecords=strcat(contractRecords contractRecord)
    )
    when(or(contractBudget['textTruncated] strlen(contractRecords)>4190000)
      error("Contract CDF evidence exceeds bound"))
    sprintf(nil "{\"cell\":\"TEST_LDO\",\"modified\":%s,\"instances\":[%s]}"
      aiInspectJsonBoolean(dbIsCellViewModified(contractCv)) contractRecords)
  )
)

procedure(aivwCollectContract()
let((aivwDiscoveryRoot aivwDiscoveryAiRoot aivwDiscoveryCv aivwDiscoveryData aivwDiscoveryBudget aivwDiscoveryOrder aivwDiscoveryStateObj)
  aivwDiscoveryRoot=getShellEnvVar("AIVW_CONTRACT_OUTPUT")
  aivwDiscoveryAiRoot=getShellEnvVar("AIVW_LDO_AI_ROOT")
  unless(and(aivwDiscoveryRoot aivwDiscoveryAiRoot) exit(2))
  load(strcat(aivwDiscoveryAiRoot "/skill/AI_protocol.il"))
  load(strcat(aivwDiscoveryAiRoot "/skill/AI_inspect.il"))
  aivwDiscoveryBudget=aiInspectNewSnapshotBudget()
  foreach(aivwDiscoveryPhase list("before" "after")
    foreach(aivwSourceCell list("LDO_MASTER" "LDO_AON" "LDO_PD2" "LDO_opamp" "LDO_INV")
      aivwContractWrite(aivwDiscoveryRoot strcat(aivwSourceCell "-schematic-" aivwDiscoveryPhase)
        aiInspectSchematic("amsLDO" aivwSourceCell "schematic" "schematic" 200 nil))
    )
    aivwContractWrite(aivwDiscoveryRoot strcat("testbench-" aivwDiscoveryPhase)
      aiInspectSchematic("amsLDO" "TEST_LDO" "schematic" "schematic" 200 nil))
    aivwDiscoveryCv=dbOpenCellViewByType("amsLDO" "TEST_LDO" "schematic" "" "r")
    unless(aivwDiscoveryCv error("Cannot read TEST_LDO"))
    aivwContractWrite(aivwDiscoveryRoot strcat("stimulus-" aivwDiscoveryPhase) aivwContractProperties(aivwDiscoveryCv))
    dbClose(aivwDiscoveryCv)
    foreach(aivwDiscoveryCell list("LDO_MASTER" "LDO_AON")
      aivwDiscoveryData=cdfGetCellCDF(ddGetObj("amsLDO" aivwDiscoveryCell))
      aivwDiscoveryOrder=aivwDiscoveryData->simInfo->spectre->termOrder
      aivwContractWrite(aivwDiscoveryRoot strcat(aivwDiscoveryCell "-cdf-" aivwDiscoveryPhase)
        sprintf(nil "{\"cell\":%s,\"spectre_term_order\":%s}"
          aiInspectJsonString(aivwDiscoveryCell aivwDiscoveryBudget) aiInspectJsonString(aivwDiscoveryOrder aivwDiscoveryBudget)))
      aivwContractWrite(aivwDiscoveryRoot strcat(aivwDiscoveryCell "-symbol-" aivwDiscoveryPhase)
        aiInspectSymbolPorts("amsLDO" aivwDiscoveryCell "symbol" "schematicSymbol" 200))
    )
  )
  aivwDiscoveryStateObj=ddGetObj("amsLDO" "TEST_LDO" "maestro" "active.state")
  unless(aivwDiscoveryStateObj error("Cannot resolve Maestro text state"))
  aivwContractWrite(aivwDiscoveryRoot "state-locator"
    sprintf(nil "{\"path\":%s,\"access\":\"read_only\"}"
      aiInspectJsonString(ddGetObjReadPath(aivwDiscoveryStateObj) aivwDiscoveryBudget)))
  t
)
)
if(errset(aivwCollectContract() t) then exit(0) else exit(2))
