; DataHub v1 SKILL envelope and legacy compatibility edge.
; Requires AI_pdk_core.il and generated AI_datahub_contracts.il.
unless(and(boundp('aiDatahubVersion)
           aiDatahubVersion=="20261023.ai.datahub.v1")

unless(and(isCallable('aiPdkObj) isCallable('aiPdkArray)
           isCallable('aiPdkBool) isCallable('aiPdkJson)
           isCallable('aiJsonEscape) boundp('datahubContractRows)
           boundp('datahubLegacyErrors))
  error("DataHub SKILL requires AI_pdk_core and generated contracts"))

aiDatahubEnvelopeLimit=65536
aiDatahubPayloadLimit=55000
aiDatahubLegacyLimit=55000
aiDatahubMaxDepth=16
aiDatahubMaxSequence=200
aiDatahubMaxText=4096
aiDatahubMaxId=128
aiDatahubMaxDeadline=300000

procedure(datahubRevision() "20261023.ai.datahub.v1")

procedure(datahubTextOk(dhtValue dhtLimit)
  when(stringp(dhtValue)
    let((dhtIndex dhtCode dhtOk)
      dhtIndex=1 dhtOk=t
      while(and(dhtOk dhtIndex<=strlen(dhtValue))
        dhtCode=charToInt(concat(substring(dhtValue dhtIndex 1)))
        when(or(dhtCode<32 dhtCode==127) dhtOk=nil)
        dhtIndex=dhtIndex+1)
      and(dhtOk strlen(dhtValue)<=dhtLimit))))

procedure(datahubObjectPairs(dhoValue)
  when(and(listp(dhoValue) eq(car(dhoValue) 'pdkJsonObject))
    cdr(dhoValue)))

procedure(datahubHasField(dhfObject dhfName)
  and(listp(dhfObject) eq(car(dhfObject) 'pdkJsonObject)
    assoc(dhfName cdr(dhfObject))))

procedure(datahubField(dhfObject dhfName)
  let((dhfPair)
    when(datahubHasField(dhfObject dhfName)
      dhfPair=assoc(dhfName cdr(dhfObject))
      cadr(dhfPair))))

procedure(datahubKnownError(dkeCode)
  prog((dkeContract dkeAction dkeSpec)
    foreach(dkeContract datahubContractRows
      foreach(dkeAction caddr(dkeContract)
        dkeSpec=dkeAction
        when(member(dkeCode nth(4 dkeSpec)) return(t))))
    nil))

procedure(datahubResolve(dhrContract dhrVersion dhrAction)
  prog((dhrRow dhrFound dhrSpec dhrActions)
    foreach(dhrRow datahubContractRows
      when(and(!dhrFound car(dhrRow)==dhrContract) dhrFound=dhrRow))
    unless(dhrFound
      return(list('error "unsupported_contract"
        "Unsupported DataHub contract" aiPdkObj(nil))))
    unless(cadr(dhrFound)==dhrVersion
      return(list('error "version_mismatch"
        "DataHub contract version is not supported"
        aiPdkObj(list(list("expected_version" cadr(dhrFound)))))))
    dhrActions=caddr(dhrFound)
    foreach(dhrSpec dhrActions
      when(car(dhrSpec)==dhrAction
        return(list('ok dhrSpec nth(3 dhrFound)))))
    return(list('error "unsupported_contract"
      "Unsupported DataHub contract action" aiPdkObj(nil)))))

procedure(datahubCapabilityAvailable(dcaSpec dcaCapabilities)
  if(dcaCapabilities
    and(member(cadr(dcaSpec) dcaCapabilities) t)
    and(isCallable(cadr(dcaSpec)) t)))

procedure(datahubCheckRequest(dcrContract dcrVersion dcrAction
                              dcrPayload dcrCapabilities)
  prog((dcrResolved dcrSpec dcrLimits dcrPairs dcrFields dcrField dcrPair
        dcrValue)
    dcrResolved=datahubResolve(dcrContract dcrVersion dcrAction)
    unless(car(dcrResolved)=='ok return(dcrResolved))
    dcrSpec=cadr(dcrResolved) dcrLimits=caddr(dcrResolved)
    unless(datahubCapabilityAvailable(dcrSpec dcrCapabilities)
      return(list('error "capability_not_available"
        "Required DataHub capability is unavailable"
        aiPdkObj(list(list("capability" cadr(dcrSpec)))))))
    dcrPairs=datahubObjectPairs(dcrPayload)
    unless(dcrPairs
      return(list('error "invalid_arguments"
        "DataHub request payload must be an object" aiPdkObj(nil))))
    dcrFields=nth(3 dcrSpec)
    unless(length(dcrPairs)==length(dcrFields)
      return(list('error "invalid_arguments"
        "DataHub request fields do not match the contract" aiPdkObj(nil))))
    foreach(dcrPair dcrPairs
      unless(and(listp(dcrPair) length(dcrPair)==2
                 member(car(dcrPair) dcrFields))
        return(list('error "invalid_arguments"
          "DataHub request has an unexpected field" aiPdkObj(nil)))))
    foreach(dcrField dcrFields
      unless(assoc(dcrField dcrPairs)
        return(list('error "invalid_arguments"
          "DataHub request is missing a field" aiPdkObj(nil)))))
    unless(errset(progn(
        datahubCheckValue(dcrPayload 1 cadr(dcrLimits)) t) nil)
      return(list('error "invalid_arguments"
        "DataHub request value is invalid" aiPdkObj(nil))))
    when(assoc("limit" dcrPairs)
      dcrValue=cadr(assoc("limit" dcrPairs))
      unless(and(fixp(dcrValue) dcrValue>=1 dcrValue<=car(dcrLimits))
        return(list('error "invalid_arguments"
          "DataHub request limit is invalid" aiPdkObj(nil)))))
    when(assoc("offset" dcrPairs)
      dcrValue=cadr(assoc("offset" dcrPairs))
      unless(and(fixp(dcrValue) dcrValue>=0 dcrValue<=4096)
        return(list('error "invalid_arguments"
          "DataHub request offset is invalid" aiPdkObj(nil)))))
    when(assoc("query" dcrPairs)
      dcrValue=cadr(assoc("query" dcrPairs))
      unless(and(stringp(dcrValue) strlen(dcrValue)<=car(dcrLimits))
        return(list('error "invalid_arguments"
          "DataHub request query is invalid" aiPdkObj(nil)))))
    return(list('ok dcrSpec dcrLimits))))

procedure(datahubCheckValue(dhvValue dhvDepth dhvLimit)
  let((dhvCount dhvItem dhvPairs dhvPair)
    when(dhvDepth>aiDatahubMaxDepth error("DataHub nesting limit"))
    cond(
      (or(null(dhvValue) eq(dhvValue t) eq(dhvValue 'pdkJsonFalse)) t)
      (stringp(dhvValue)
        unless(datahubTextOk(dhvValue dhvLimit)
          error("DataHub text limit")))
      (numberp(dhvValue) t)
      (listp(dhvValue)
        if(eq(car(dhvValue) 'pdkJsonObject) then
          dhvPairs=cdr(dhvValue)
          when(length(dhvPairs)>aiDatahubMaxSequence
            error("DataHub sequence limit"))
          foreach(dhvPair dhvPairs
            unless(and(listp(dhvPair) length(dhvPair)==2
                       stringp(car(dhvPair))
                       datahubTextOk(car(dhvPair) aiDatahubMaxId))
              error("DataHub object field is invalid"))
            datahubCheckValue(cadr(dhvPair) dhvDepth+1 dhvLimit))
        else
          when(eq(car(dhvValue) 'pdkJsonArray) dhvValue=cdr(dhvValue))
          when(length(dhvValue)>aiDatahubMaxSequence
            error("DataHub sequence limit"))
          foreach(dhvItem dhvValue
            datahubCheckValue(dhvItem dhvDepth+1 dhvLimit))))
      (t error("DataHub value type is unsupported")))))

procedure(datahubMeta(@optional (dhmTrace "") (dhmSource "skill")
                      (dhmTarget "python") (dhmDeadline 5000))
  aiPdkObj(list(
    list("trace_id" dhmTrace)
    list("source" dhmSource)
    list("target" dhmTarget)
    list("deadline_ms" dhmDeadline))))

procedure(datahubPage(dhpOffset dhpLimit dhpTotal dhpNext dhpTruncated)
  aiPdkObj(list(
    list("offset" dhpOffset)
    list("limit" dhpLimit)
    list("total" dhpTotal)
    list("next" dhpNext)
    list("truncated" aiPdkBool(dhpTruncated)))))

procedure(datahubNextOffset(dhnOffset dhnItems dhnTotal dhnTruncated)
  when(dhnTruncated
    let((dhnNext)
      dhnNext=max(dhnOffset+dhnItems dhnOffset+1)
      when(dhnNext>dhnTotal dhnNext=dhnTotal)
      dhnNext)))

procedure(datahubEnvelope(dheContract dheVersion dheId dheAction dheStatus
                          dhePayload dhePage dheError dheMeta)
  let((dhePairs)
    dhePairs=list(
      list("hub" "sico.datahub.v1")
      list("contract" dheContract)
      list("version" dheVersion)
      list("id" dheId)
      list("action" dheAction)
      list("status" dheStatus)
      list("payload" dhePayload)
      list("meta" dheMeta)
      list("error" dheError))
    when(dhePage dhePairs=append1(dhePairs list("page" dhePage)))
    aiPdkObj(dhePairs)))

procedure(datahubOk(dhoContract dhoVersion dhoId dhoAction dhoPayload
                    @optional dhoPage (dhoMeta nil))
  let((dhoResolved)
    unless(dhoMeta dhoMeta=datahubMeta())
    dhoResolved=datahubResolve(dhoContract dhoVersion dhoAction)
    unless(car(dhoResolved)=='ok error("DataHub contract action is unavailable"))
    when(dhoPage datahubCheckPage(dhoPage car(caddr(dhoResolved))))
    datahubEnvelope(dhoContract dhoVersion dhoId dhoAction "ok"
      dhoPayload dhoPage nil dhoMeta)))

procedure(datahubMapError(dmeCode)
  let((dmePair)
    dmePair=assoc(dmeCode datahubLegacyErrors)
    if(dmePair cadr(dmePair) dmeCode)))

procedure(datahubError(dheContract dheVersion dheId dheAction dheCode
                       dheMessage @optional dheDetails (dheMeta nil))
  let((dheStable dheLegacyCode)
    dheLegacyCode=dheCode
    dheStable=datahubMapError(dheLegacyCode)
    unless(datahubKnownError(dheStable) dheStable="internal")
    unless(dheDetails dheDetails=aiPdkObj(nil))
    dheDetails=aiPdkObj(cons(list("legacy_code" dheLegacyCode)
      cdr(dheDetails)))
    unless(dheMeta dheMeta=datahubMeta())
    datahubEnvelope(dheContract dheVersion dheId dheAction "error"
      aiPdkObj(nil) nil
      aiPdkObj(list(
        list("code" dheStable)
        list("message" dheMessage)
        list("details" dheDetails)))
      dheMeta)))

procedure(datahubUnknown(dhuContract dhuVersion dhuId dhuAction
                         @optional dhuError (dhuMeta nil))
  unless(dhuMeta dhuMeta=datahubMeta())
  datahubEnvelope(dhuContract dhuVersion dhuId dhuAction "unknown"
    aiPdkObj(nil) nil dhuError dhuMeta))

procedure(datahubCheckPage(dcpPage dcpLimit)
  let((dcpOffset dcpTotal dcpNext dcpTruncated)
    unless(datahubObjectPairs(dcpPage)
      error("DataHub page must be an object"))
    unless(length(cdr(dcpPage))==5 error("DataHub page fields are invalid"))
    dcpOffset=datahubField(dcpPage "offset")
    dcpTotal=datahubField(dcpPage "total")
    dcpNext=datahubField(dcpPage "next")
    dcpTruncated=datahubField(dcpPage "truncated")
    unless(and(fixp(dcpOffset) dcpOffset>=0
               fixp(datahubField(dcpPage "limit"))
               datahubField(dcpPage "limit")>=1
               datahubField(dcpPage "limit")<=dcpLimit
               fixp(dcpTotal) dcpTotal>=0)
      error("DataHub page bounds are invalid"))
    unless(or(eq(dcpTruncated t) eq(dcpTruncated 'pdkJsonFalse))
      error("DataHub page truncation is invalid"))
    if(eq(dcpTruncated t) then
      unless(and(fixp(dcpNext) dcpNext>dcpOffset dcpNext<=dcpTotal)
        error("DataHub page next offset is invalid"))
    else
      unless(null(dcpNext) error("DataHub page next offset is invalid")))
    t))

procedure(datahubCheckEnvelope(dceEnvelope)
  let((dceCount dceStatus dceError dceMeta dceCode)
    unless(datahubObjectPairs(dceEnvelope)
      error("DataHub envelope must be an object"))
    dceCount=length(cdr(dceEnvelope))
    unless(or(dceCount==9 dceCount==10)
      error("DataHub envelope fields are invalid"))
    foreach(dceField list("hub" "contract" "version" "id" "action"
                         "status" "payload" "meta" "error")
      unless(datahubHasField(dceEnvelope dceField)
        error("DataHub envelope field is missing")))
    unless(datahubField(dceEnvelope "hub")=="sico.datahub.v1"
      error("DataHub envelope identity is invalid"))
    dceStatus=datahubField(dceEnvelope "status")
    unless(member(dceStatus list("ok" "error" "unknown"))
      error("DataHub envelope status is invalid"))
    datahubCheckValue(datahubField(dceEnvelope "payload") 1
      aiDatahubMaxText)
    dceMeta=datahubField(dceEnvelope "meta")
    unless(and(datahubObjectPairs(dceMeta) length(cdr(dceMeta))==4)
      error("DataHub envelope meta is invalid"))
    unless(and(stringp(datahubField(dceMeta "trace_id"))
               datahubTextOk(datahubField(dceMeta "trace_id")
                 aiDatahubMaxId)
               stringp(datahubField(dceMeta "source"))
               datahubTextOk(datahubField(dceMeta "source") 64)
               stringp(datahubField(dceMeta "target"))
               datahubTextOk(datahubField(dceMeta "target") 64)
               fixp(datahubField(dceMeta "deadline_ms"))
               datahubField(dceMeta "deadline_ms")>0
               datahubField(dceMeta "deadline_ms")<=aiDatahubMaxDeadline)
      error("DataHub envelope meta values are invalid"))
    dceError=datahubField(dceEnvelope "error")
    cond(
      (dceStatus=="ok" unless(null(dceError)
        error("DataHub success cannot carry an error")))
      (dceStatus=="error"
        unless(and(datahubObjectPairs(dceError)
                   length(cdr(dceError))==3)
          error("DataHub error envelope is invalid"))
        dceCode=datahubField(dceError "code")
        unless(and(stringp(dceCode) datahubKnownError(dceCode))
          error("DataHub error code is invalid"))
        unless(and(stringp(datahubField(dceError "message"))
                   datahubTextOk(datahubField(dceError "message")
                     aiDatahubMaxText))
          error("DataHub error message is invalid"))
        datahubCheckValue(datahubField(dceError "details") 1
          aiDatahubMaxText))
      (t when(dceError
        unless(and(datahubObjectPairs(dceError)
                   length(cdr(dceError))==3)
          error("DataHub unknown error is invalid"))
        dceCode=datahubField(dceError "code")
        unless(and(stringp(dceCode) datahubKnownError(dceCode))
          error("DataHub unknown error code is invalid"))
        unless(and(stringp(datahubField(dceError "message"))
                   datahubTextOk(datahubField(dceError "message")
                     aiDatahubMaxText))
          error("DataHub unknown error message is invalid"))
        datahubCheckValue(datahubField(dceError "details") 1
          aiDatahubMaxText))))
    when(datahubField(dceEnvelope "page")
      datahubCheckPage(datahubField(dceEnvelope "page")
        aiDatahubMaxSequence))
    t))

procedure(datahubJson(dhjEnvelope)
  let((dhjPayload dhjPayloadJson dhjResult)
    unless(datahubCheckEnvelope(dhjEnvelope)
      error("DataHub envelope is invalid"))
    dhjPayload=datahubField(dhjEnvelope "payload")
    dhjPayloadJson=aiPdkJson(dhjPayload)
    when(strlen(dhjPayloadJson)>aiDatahubPayloadLimit
      error("DataHub payload limit"))
    dhjResult=aiPdkJson(dhjEnvelope)
    when(strlen(dhjResult)>aiDatahubEnvelopeLimit
      error("DataHub envelope limit"))
    dhjResult))

procedure(datahubLegacyErrorObject(dleEnvelope)
  let((dleError dleDetails dleCode dleLegacy)
    dleError=datahubField(dleEnvelope "error")
    dleDetails=datahubField(dleError "details")
    dleCode=datahubField(dleError "code")
    dleLegacy=datahubField(dleDetails "legacy_code")
    aiPdkObj(list(
      list("ok" 'pdkJsonFalse)
      list("code" if(stringp(dleLegacy) dleLegacy dleCode))
      list("message" datahubField(dleError "message"))))))

procedure(datahubLegacyMode(dlmContract dlmAction)
  cond(
    (dlmContract=="sico.cad.selection.v1" "selection")
    (dlmContract=="sico.cad.objects.v1" "objects")
    (dlmContract=="sico.cad.windows.v1" "windows")
    (dlmContract=="sico.cad.highlight.v1" "highlight")
    (and(dlmContract=="sico.cad.layout_export.v1"
         dlmAction=="visible_oasis") "layout_export")
    (and(dlmContract=="sico.ask.objects.v1"
         dlmAction=="selection") "selection")
    (and(dlmContract=="sico.ask.objects.v1"
         dlmAction=="objects") "objects")
    (and(dlmContract=="sico.ask.objects.v1"
         dlmAction=="windows") "windows")
    (t nil)))

procedure(datahubLegacyPayload(dlpEnvelope dlpSpec)
  let((dlpPayload dlpPage dlpPairs dlpResult dlpSchema dlpMode)
    dlpPayload=datahubField(dlpEnvelope "payload")
    dlpPage=datahubField(dlpEnvelope "page")
    dlpSchema=caddr(dlpSpec)
    dlpMode=datahubLegacyMode(datahubField(dlpEnvelope "contract")
      datahubField(dlpEnvelope "action"))
    dlpResult=list(
      list("ok" t)
      list("valid" t)
      list("schema" dlpSchema))
    foreach(dlpPairs cdr(dlpPayload)
      dlpResult=append1(dlpResult dlpPairs))
    case(dlpMode
      ("selection"
        unless(dlpPage error("DataHub selection requires a page"))
        dlpResult=append1(dlpResult
          list("max_items" datahubField(dlpPage "limit")))
        dlpResult=append1(dlpResult
          list("offset" datahubField(dlpPage "offset")))
        dlpResult=append1(dlpResult
          list("truncated" datahubField(dlpPage "truncated")))
        )
      ("objects"
        unless(dlpPage error("DataHub objects require a page"))
        dlpResult=append1(dlpResult list("total"
          datahubField(dlpPage "total")))
        dlpResult=append1(dlpResult
          list("max_items" datahubField(dlpPage "limit")))
        dlpResult=append1(dlpResult
          list("offset" datahubField(dlpPage "offset")))
        dlpResult=append1(dlpResult
          list("truncated" datahubField(dlpPage "truncated")))
        )
      ("windows"
        unless(dlpPage error("DataHub windows require a page"))
        dlpResult=append1(dlpResult
          list("max_items" datahubField(dlpPage "limit")))
        dlpResult=append1(dlpResult list("total"
          datahubField(dlpPage "total")))
        dlpResult=append1(dlpResult
          list("offset" datahubField(dlpPage "offset")))
        dlpResult=append1(dlpResult
          list("next_offset" datahubField(dlpPage "next")))
        dlpResult=append1(dlpResult
          list("truncated" datahubField(dlpPage "truncated")))
        )
      ("highlight"
        when(dlpPage error("DataHub highlight cannot be paged"))
        )
      ("layout_export"
        when(dlpPage error("DataHub layout export cannot be paged"))
        )
      (t error("DataHub legacy mode is unsupported")))
    aiPdkObj(dlpResult)))

procedure(datahubLegacyJson(dljEnvelope)
  prog((dljResolved dljStatus dljResult dljJson)
    datahubJson(dljEnvelope)
    dljStatus=datahubField(dljEnvelope "status")
    cond(
      (dljStatus=="error"
        dljResult=datahubLegacyErrorObject(dljEnvelope))
      (dljStatus=="unknown"
        dljResult=aiPdkObj(list(
          list("ok" 'pdkJsonFalse)
          list("code" "timeout")
          list("message" "DataHub operation outcome is unknown"))))
      (t
        dljResolved=datahubResolve(
          datahubField(dljEnvelope "contract")
          datahubField(dljEnvelope "version")
          datahubField(dljEnvelope "action"))
        unless(car(dljResolved)=='ok
          return(aiPdkError("internal" "DataHub contract is unavailable")))
        dljResult=datahubLegacyPayload(dljEnvelope cadr(dljResolved))))
    dljJson=aiPdkJson(dljResult)
    when(strlen(dljJson)>aiDatahubLegacyLimit
      return(aiPdkError("result_too_large" "DataHub legacy result exceeds the limit")))
    return(dljJson)))

procedure(datahubLegacyError(dleContract dleAction dleCode dleMessage
                             @optional dleDetails (dleMeta nil))
  datahubLegacyJson(datahubError(dleContract 1 "skill-error" dleAction
    dleCode dleMessage dleDetails dleMeta)))

procedure(datahubLegacyResult(dlrEnvelope dlrLimitMessage)
  let((dlrResult)
    dlrResult=errset(datahubLegacyJson(dlrEnvelope) nil)
    if(dlrResult car(dlrResult)
      aiPdkError("result_too_large" dlrLimitMessage))))

aiDatahubVersion="20261023.ai.datahub.v1"
)
