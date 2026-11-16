; Licensed acceptance fixture only. Load explicitly in an owned test session.
; No vendor source or model content is copied here. Not installed by AI.ils.
; This qualifies selected static MOS symbols; it is not a general SMIC/PCell adapter.
procedure(aiSmicTestInstall(astLibraryPath)
  aiSmicTestCdfs=makeTable("aiSmicTestCdfs" nil)
  foreach(astCell list("n09_ckt" "p09_ckt")
    aiSmicTestCdfs[astCell]=aiCrCdfRows(cdfGetCellCDF(ddGetObj("smic28hkmg" astCell)))
    aiCrRegisterExtension(strcat("test:smic28:" astCell ":v1") "test.v1"
      list("smic28hkmg" astCell "symbol") astLibraryPath 'aiSmicTestValidate 'aiSmicTestCreate))
  t)
procedure(aiSmicTestValidate(astMaster astRows)
  prog((astCdf astParam astNumber)
    astCdf=cdfGetCellCDF(ddGetObj(astMaster~>libName astMaster~>cellName))
    unless(and(astCdf astMaster~>libName=="smic28hkmg"
      member(astMaster~>cellName list("n09_ckt" "p09_ckt"))
      astCdf~>formInitProc=="PasCdfFormInit" astCdf~>doneProc=="PasCdfDone"
      equal(aiSmicTestCdfs[astMaster~>cellName] aiCrCdfRows(astCdf)))
      error("test project CDF metadata changed"))
    unless(and(length(astRows)==4
      forall(astName list("l" "fingers" "w" "m") assoc(astName astRows)))
      error("test project requires explicit l/fingers/w/m"))
    foreach(astRow astRows
      astParam=cdfFindParamByName(astCdf car(astRow))
      unless(and(astParam astParam~>paramType=="string" cadr(astRow)=="string"
        astParam~>callback==sprintf(nil "smic28hkmg_mosCB( '%s )" car(astRow))
        stringp(caddr(astRow)) pcreMatchp("^[0-9]+[.]?[0-9]*[afpnumkMG]?$" caddr(astRow)))
        error("test project only accepts selected literal scalar parameters"))
      astNumber=cdfParseFloatString(caddr(astRow))
      unless(and(numberp(astNumber) astNumber>0) error("positive numeric value required"))
      when(member(car(astRow) list("fingers" "m"))
        unless(and(astNumber==fix(astNumber) astNumber<=16) error("test count must be integer 1..16"))))
    return(t)))
procedure(aiSmicTestCreate(astCv astMaster astName astXY astOrient astRows)
  prog((cdfgData cdfgForm astSaved astInst astRequest astP)
    cdfgData=cdfGetCellCDF(ddGetObj(astMaster~>libName astMaster~>cellName)) cdfgForm=nil
    astSaved=foreach(mapcar astP cdfgData~>parameters list(astP astP~>value))
    unwindProtect(progn(
      foreach(astP cdfgData~>parameters astP~>value=astP~>defValue)
      PasCdfFormInit(cdfgData)
      when(isCallable('aiCdfProbeCheckpoint) aiCdfProbeCheckpoint(cdfgData "project:form_init"))
      ; PDK semantics: changing fingers changes total width. Set total w afterwards.
      foreach(astParamName list("l" "fingers" "w" "m")
        astRequest=assoc(astParamName astRows)
        astP=cdfFindParamByName(cdfgData astParamName) astP~>value=caddr(astRequest)
        ; Only the locally verified callback descriptor is evaluated, never request text.
        evalstring(astP~>callback)
        when(isCallable('aiCdfProbeCheckpoint) aiCdfProbeCheckpoint(cdfgData strcat("project:set:" astParamName))))
      when(and(boundp('aiSmicTestInjectFailure) aiSmicTestInjectFailure)
        error("injected project creation failure"))
      astInst=schCreateInst(astCv astMaster astName astXY astOrient)
      unless(astInst error("test project instance creation failed")))
      foreach(astSavedRow astSaved car(astSavedRow)~>value=cadr(astSavedRow)))
    return(astInst)))
