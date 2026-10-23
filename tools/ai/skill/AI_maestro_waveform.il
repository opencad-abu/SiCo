; Exact-history raw signal capture. Public RDB help('test) documents resultsDir.
; getData with BOTH keywords does not modify OCEAN/GUI results selection.
unless(boundp('aiWaveCaptures) aiWaveCaptures=makeTable("aiWaveCaptures" nil))
procedure(aiWaveRevision() "20260911.ai.waveform.v2")
procedure(aiWaveFreeze(awfWave)
  prog((awfStatus awfX awfY awfCount awfPages awfRows awfXv awfYv awfPrev)
    awfStatus=cond((null(awfWave) "missing")
      (famIsFamily(awfWave) "family")
      (drIsWaveform(awfWave)
        cond((and(member(drGetWaveformXType(awfWave) list('double 'intlong))
          member(drGetWaveformYType(awfWave) list('double 'intlong))) "waveform")
          (or(drGetWaveformXType(awfWave)=='doublecomplex
            drGetWaveformYType(awfWave)=='doublecomplex) "complex")
          (t "error")))
      (numberp(awfWave) "scalar")
      (t "error"))
    awfCount=0
    when(awfStatus=="waveform"
      awfX=drGetWaveformXVec(awfWave) awfY=drGetWaveformYVec(awfWave)
      awfCount=drVectorLength(awfX)
      unless(awfCount==drVectorLength(awfY) error("waveform vector lengths differ"))
      when(awfCount>65536 error("waveform sample limit; no truncation performed"))
      for(awfIndex 0 awfCount-1
        awfXv=drGetElem(awfX awfIndex) awfYv=drGetElem(awfY awfIndex)
        unless(and(numberp(awfXv) numberp(awfYv) awfXv==awfXv awfYv==awfYv
          abs(awfXv)<1e300 abs(awfYv)<1e300) error("nonfinite waveform sample"))
        when(and(awfPrev awfXv<=awfPrev) error("waveform axis is not strictly increasing"))
        awfPrev=awfXv
        awfRows=cons(list(awfXv awfYv) awfRows)
        when(length(awfRows)==256
          awfPages=cons(aiCircuitJson(reverse(awfRows)) awfPages) awfRows=nil))
      when(awfRows awfPages=cons(aiCircuitJson(reverse(awfRows)) awfPages)))
    return(list(awfStatus awfCount reverse(awfPages)
      when(awfX aiResultsValue(awfX->units 0)) when(awfY aiResultsValue(awfY->units 0))))))
procedure(aiWaveCapture(awcRef awcHistory awcTest awcCorner awcPoint awcAnalysis awcSignal awcId
    @optional (awcAc nil))
  prog((awcSession awcEntry awcDb awcRun awcMatches awcNative awcFrozen awcDir awcResult)
    when(or(aiWaveCaptures[awcId] length(aiWaveCaptures)>=8)
      return(aiCircuitError("waveform_capture_limit" "Release prior frozen captures")))
    awcResult=errset(progn(
      awcSession=aiResultsSession(awcRef) awcEntry=aiCircuitContexts[awcRef]
      unless(axlGetHistoryEntry(axlGetMainSetupDB(awcSession) awcHistory) error("history missing"))
      awcRun=axlGetRunStatus(awcSession)
      unless(and(listp(awcRun) length(awcRun)==2 car(awcRun)==cadr(awcRun)) error("run active/unknown"))
      awcDb=maeReadResDB(?session awcSession ?historyName awcHistory)
      unless(awcDb error("history RDB unavailable"))
      awcMatches=setof(awcRow awcDb->tests()
        and(awcRow->name==awcTest awcRow->cornerName==awcCorner awcRow->pointID==awcPoint))
      unless(length(awcMatches)==1 error("missing/ambiguous test coordinate"))
      awcNative=car(awcMatches)
      unless(awcNative->status=='done error("test coordinate not done"))
      awcDir=awcNative->resultsDir
      unless(and(stringp(awcDir) isDir(awcDir)) error("raw result directory unavailable"))
      awcFrozen=if(awcAc then
        aiAcFreeze(getData(awcSignal ?result stringToSymbol(awcAnalysis) ?resultsDir awcDir))
        else aiWaveFreeze(getData(awcSignal ?result stringToSymbol(awcAnalysis) ?resultsDir awcDir)))
      ; Freeze all pages before publishing; no live waveform handles survive the call.
      aiWaveCaptures[awcId]=list(caddr(awcFrozen))
      t) t)
    unless(awcResult return(aiCircuitError("waveform_capture_failed"
      "Exact history, coordinate or waveform unavailable/unsupported; inspect CIW")))
    return(aiCircuitObject(list(list("ok" t) list("capture_id" awcId)
      list("context_ref" awcRef) list("history" awcHistory) list("test" awcTest)
      list("corner" awcCorner) list("point" awcPoint) list("analysis" awcAnalysis)
      list("signal" awcSignal) list("session" awcSession) list("target" awcEntry['target])
      list("results_directory" simplifyFilename(awcDir)) list("status" car(awcFrozen))
      list("sample_count" cadr(awcFrozen)) list("pages" length(caddr(awcFrozen)))
      list("x_unit" nth(3 awcFrozen)) list("y_unit" nth(4 awcFrozen))
      list("native_y_type" nth(5 awcFrozen)))))))
procedure(aiWavePage(awpId awpIndex)
  prog((awpPages)
    awpPages=car(aiWaveCaptures[awpId])
    unless(and(awpPages fixp(awpIndex) awpIndex>=0 awpIndex<length(awpPages))
      return(aiCircuitError("waveform_page_missing" "Frozen waveform page unavailable")))
    return(strcat("{\"ok\":true,\"samples\":" nth(awpIndex awpPages) "}"))))
procedure(aiWaveRelease(awrId)
  remove(awrId aiWaveCaptures)
  aiCircuitObject(list(list("ok" t))))
