; AC native phasors only. Public DR access and OCEAN real/imag preserve original samples.
procedure(aiAcRevision() "20260911.ai.ac.v1")
procedure(aiAcFinite(aafNumber)
  and(numberp(aafNumber) aafNumber==aafNumber abs(aafNumber)<1e300))
procedure(aiAcFreeze(aafWave)
  prog((aafStatus aafX aafY aafType aafCount aafPages aafRows aafFreq
      aafValue aafReal aafImag aafPrev aafIndex)
    aafStatus=cond((null(aafWave) "missing")
      (famIsFamily(aafWave) "family")
      (drIsWaveform(aafWave)
        cond((and(member(drGetWaveformXType(aafWave) list('double 'intlong))
          member(drGetWaveformYType(aafWave) list('doublecomplex 'double 'intlong))) "waveform")
          (t "error")))
      (numberp(aafWave) "scalar") (t "error"))
    aafCount=0
    when(aafStatus=="waveform"
      aafX=drGetWaveformXVec(aafWave) aafY=drGetWaveformYVec(aafWave)
      aafType=drGetWaveformYType(aafWave) aafCount=drVectorLength(aafX)
      unless(aafCount==drVectorLength(aafY) error("AC vector lengths differ"))
      when(aafCount>65536 error("AC sample limit; no truncation performed"))
      for(aafIndex 0 aafCount-1
        aafFreq=drGetElem(aafX aafIndex) aafValue=drGetElem(aafY aafIndex)
        if(aafType=='doublecomplex then
          aafReal=real(aafValue) aafImag=imag(aafValue)
          else aafReal=aafValue aafImag=0.0)
        unless(and(aiAcFinite(aafFreq) aiAcFinite(aafReal) aiAcFinite(aafImag))
          error("nonfinite AC sample"))
        when(or(aafFreq<0 and(aafPrev aafFreq<=aafPrev))
          error("AC frequency must be nonnegative and strictly increasing"))
        aafPrev=aafFreq aafRows=cons(list(aafFreq aafReal aafImag) aafRows)
        when(length(aafRows)==256
          aafPages=cons(aiCircuitJson(reverse(aafRows)) aafPages) aafRows=nil))
      when(aafRows aafPages=cons(aiCircuitJson(reverse(aafRows)) aafPages)))
    return(list(aafStatus aafCount reverse(aafPages)
      when(aafX aiResultsValue(aafX->units 0)) when(aafY aiResultsValue(aafY->units 0))
      when(aafType symbolToString(aafType))))))
procedure(aiAcCapture(aacRef aacHistory aacTest aacCorner aacPoint aacAnalysis aacSignal aacId)
  if(aacAnalysis=="ac" then
    aiWaveCapture(aacRef aacHistory aacTest aacCorner aacPoint aacAnalysis aacSignal aacId t)
    else aiCircuitError("ac_analysis_required" "Only raw ac result supported")))
