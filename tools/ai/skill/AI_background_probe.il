; B0 only: explicit saved OA target, no windows, callbacks, relay or job lifetime.
unless(and(boundp('aiBackgroundProbeVersion)
           aiBackgroundProbeVersion=="20260915.ai.background.probe.v1")

procedure(aiBackgroundProbe(abpLib abpCell abpView abpType abpMax abpPlacement)
  prog((abpCv abpExisting abpProtected abpClosed abpReason abpTarget abpBudget abpInspection
        abpTech abpDependencies abpInst abpMaster abpMasterLib abpCdf abpBase abpCount abpApi)
    abpReason="explicit_target_required" abpDependencies="" abpCount=0
    abpTarget=sprintf(nil "{\"lib\":\"%s\",\"cell\":\"%s\",\"view\":\"%s\"}"
      aiJsonEscape(or(abpLib "")) aiJsonEscape(or(abpCell "")) aiJsonEscape(or(abpView "")))
    abpProtected=errset(unwindProtect(progn(
      unless(and(stringp(abpLib) strlen(abpLib)>0 stringp(abpCell) strlen(abpCell)>0
        stringp(abpView) strlen(abpView)>0) error("Explicit database target required"))
      abpReason="unsupported_view_type"
      unless(abpType=="schematic" error("Only schematic inspection is qualified"))
      abpReason="api_unavailable"
      foreach(abpApi list('dbOpenCellViewByType 'dbFindOpenCellViewByName 'dbClose 'dbIsCellViewModified 'ddGetObj
        'techGetTechFile 'techGetTechLibName 'cdfGetBaseCellCDF 'cdfGetInstCDF 'aiInspectSchematic)
        unless(isCallable(abpApi) error("Background API unavailable")))
      abpReason="cellview_open_failed"
      unless(ddGetObj(abpLib abpCell abpView) error("Target unavailable"))
      abpExisting=dbFindOpenCellViewByName(abpLib abpCell abpView)
      abpReason="writable_handle"
      when(and(abpExisting abpExisting~>mode!="r") error("Existing writable target cannot be reused"))
      abpReason="modified_target"
      when(and(abpExisting dbIsCellViewModified(abpExisting)) error("Existing modified target cannot be reused"))
      abpReason="cellview_open_failed"
      abpCv=dbOpenCellViewByType(abpLib abpCell abpView "" "r")
      unless(abpCv error("Target cannot open read-only"))
      abpReason="writable_handle"
      unless(abpCv~>mode=="r" error("Existing writable target cannot be reused"))
      abpReason="modified_target"
      when(dbIsCellViewModified(abpCv) error("Only saved OA data is supported"))
      abpReason="view_type_mismatch"
      unless(abpCv~>cellViewType=="schematic" error("Target is not a schematic"))
      abpReason="unsupported_dependency"
      when(or(abpCv~>isParamCell abpCv~>superMaster abpCv~>mosaics)
        error("Parametric or mosaic targets are not qualified"))
      abpReason="technology_unavailable"
      unless(techGetTechFile(abpCv) error("Target technology unavailable"))
      abpTech=techGetTechLibName(ddGetObj(abpLib))
      unless(and(abpTech ddGetObj(abpTech nil nil "tech.db"))
        error("Target technology identity unavailable"))
      abpReason="dependency_limit"
      when(length(abpCv~>instances)>200 error("B0 dependency audit limit exceeded"))
      abpBudget=aiInspectNewBudget()
      foreach(abpInst abpCv~>instances
        abpReason="master_unavailable"
        abpMaster=abpInst~>master
        unless(and(abpMaster ddGetObj(abpMaster~>libName abpMaster~>cellName abpMaster~>viewName))
          error("Instance master unavailable"))
        abpReason="unsupported_dependency"
        unless(and(abpMaster~>cellViewType=="schematicSymbol" !abpMaster~>isParamCell
          !abpMaster~>superMaster !abpMaster~>instances !abpMaster~>mosaics)
          error("Only static flat symbol dependencies are qualified"))
        abpReason="writable_handle"
        unless(and(abpMaster~>mode=="r" !dbIsCellViewModified(abpMaster))
          error("Existing writable master cannot be reused"))
        abpReason="technology_unavailable"
        unless(techGetTechFile(abpMaster) error("Master technology unavailable"))
        abpMasterLib=ddGetObj(abpMaster~>libName)
        unless(and(techGetTechLibName(abpMasterLib)
          ddGetObj(techGetTechLibName(abpMasterLib) nil nil "tech.db"))
          error("Master technology identity unavailable"))
        abpReason="cdf_unavailable"
        abpBase=cdfGetBaseCellCDF(ddGetObj(abpMaster~>libName abpMaster~>cellName))
        abpCdf=cdfGetInstCDF(abpInst)
        when(and(abpBase !abpCdf) error("Effective CDF unavailable"))
        when(and(abpCdf length(abpCdf~>parameters)>512) error("CDF audit limit exceeded"))
        abpDependencies=strcat(abpDependencies if(abpCount>0 "," "") sprintf(nil
          "{\"instance\":\"%s\",\"master\":%s,\"open_mode\":\"r\",\"modified\":false,\"technology_library\":\"%s\",\"cdf_present\":%s,\"cdf_parameter_count\":%d}"
          aiJsonEscape(abpInst~>name) aiInspectCellViewJson(abpMaster abpBudget) aiJsonEscape(techGetTechLibName(abpMasterLib))
          if(abpCdf "true" "false") if(abpCdf length(abpCdf~>parameters) 0)))
        abpCount=abpCount+1)
      abpReason="inspection_failed"
      abpInspection=aiInspectSchematic(abpLib abpCell abpView abpType abpMax abpPlacement)
      unless(and(stringp(abpInspection) strlen(abpInspection)<=60000) error("Inspector result unavailable"))
      abpReason="modified_target"
      when(dbIsCellViewModified(abpCv) error("Inspection changed target"))
      t)
      when(abpCv abpClosed=errset(dbClose(abpCv) nil))) nil)
    when(and(abpCv !and(abpClosed car(abpClosed))) abpProtected=nil abpReason="close_failed")
    unless(abpProtected
      return(sprintf(nil
        "{\"protocol\":\"cad_ai_background_probe.v1\",\"ok\":false,\"code\":\"background_unavailable\",\"reason\":\"%s\",\"target\":%s,\"virtuoso_version\":\"%s\"}"
        abpReason abpTarget aiJsonEscape(getVersion(t)))))
    return(sprintf(nil
      "{\"protocol\":\"cad_ai_background_probe.v1\",\"ok\":true,\"target\":%s,\"virtuoso_version\":\"%s\",\"open_mode\":\"r\",\"modified\":false,\"closed\":true,\"window_required\":false,\"callbacks_executed\":false,\"technology_library\":\"%s\",\"instance_count\":%d,\"dependencies\":[%s],\"inspection\":%s}"
      abpTarget aiJsonEscape(getVersion(t)) aiJsonEscape(abpTech) abpCount abpDependencies abpInspection))))

procedure(aiBackgroundProbeRevision() "20260915.ai.background.probe.v1")
aiBackgroundProbeVersion="20260915.ai.background.probe.v1"
)
