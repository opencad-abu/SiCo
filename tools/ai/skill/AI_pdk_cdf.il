; Effective cell CDF only. No cdfgData binding, evaluation or callback execution.
procedure(aiPdkCdfSummary(apSummaryCell)
  let((apSummaryCdf apSummaryNames apSummaryResult)
    apSummaryResult=errset(progn(
      apSummaryCdf=cdfGetCellCDF(apSummaryCell)
      when(length(apSummaryCdf~>parameters)>4096 error("CDF parameter limit"))
      foreach(apSummaryParam apSummaryCdf~>parameters
        apSummaryNames=cons(apSummaryParam~>name apSummaryNames))
      t) nil)
    aiPdkObj(list(list("status" if(apSummaryResult "complete" "unavailable"))
      list("presence" if(apSummaryResult if(apSummaryCdf "present" "absent") "unknown"))
      list("names" aiPdkArray(reverse(apSummaryNames)))
      list("source" "cdfGetCellCDF/effective_cell")))))

procedure(aiPdkCdf(apCdfCell)
  let((apCdfValue apCdfRead apCdfParams apCdfSims apCdfHooks apCdfResult apCdfSi apCdfKeys)
    apCdfRead=errset(cdfGetCellCDF(apCdfCell) nil)
    unless(apCdfRead error("effective CDF unavailable"))
    apCdfValue=car(apCdfRead)
    when(apCdfValue
      apCdfResult=errset(progn(
        when(length(apCdfValue~>parameters)>4096 error("CDF parameter limit"))
        foreach(apCdfParam apCdfValue~>parameters
          apCdfParams=cons(aiPdkAttrs(apCdfParam list('id 'data 'formField)) apCdfParams))
        t) nil)
      ; These excluded fields are object handles/UI state, not parameter values.
      apCdfHooks=errset(aiPdkAttrs(apCdfValue list('id 'parameters 'simInfo)) nil)
      apCdfSi=apCdfValue~>simInfo
      apCdfRead=errset(progn(
        when(apCdfSi
          apCdfKeys=get(apCdfSi '?)
          when(length(apCdfKeys)>128 error("simulator limit"))
          foreach(apCdfSimKey sort(copy(apCdfKeys) 'alphalessp)
            apCdfSims=cons(aiPdkObj(list(list("name" symbolToString(apCdfSimKey))
              list("raw" aiPdkAttrs(get(apCdfSi apCdfSimKey) nil)))) apCdfSims)))
        t) nil))
    aiPdkObj(list(list("presence" if(apCdfValue "present" "absent"))
      list("parameters" aiPdkSection(if(or(!apCdfValue apCdfResult) "complete" "unavailable")
        "cdfGetCellCDF/parameters; excluded handle attributes: id,data,formField" reverse(apCdfParams)))
      list("simulators" aiPdkSection(if(apCdfRead "complete" "unavailable")
        "cdfGetCellCDF/simInfo" reverse(apCdfSims)))
      list("cell_metadata" if(apCdfHooks car(apCdfHooks) aiPdkObj(nil)))
      list("cell_metadata_status" if(or(!apCdfValue apCdfHooks) "complete" "unavailable"))))))
