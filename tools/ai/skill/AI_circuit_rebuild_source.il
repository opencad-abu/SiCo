; Bounded source capture through OA APIs; no file-level OA copy or parsing.
unless(boundp('aiRebuildPreviews) aiRebuildPreviews=makeTable("aiRebuildPreviews" nil))
unless(boundp('aiRebuildEntries) aiRebuildEntries=makeTable("aiRebuildEntries" nil))
procedure(aiRebuildRevision() "20260909.ai.rebuild.v2")
procedure(aiRebuildConnections(arcInst)
  sort(foreach(mapcar arcIt arcInst~>instTerms list(arcIt~>name arcIt~>net~>name)) 'aiConfigRowLess))
procedure(aiRebuildSource(arsCell)
  prog((arsCv arsInsts arsPorts arsWires arsPins arsResult arsLabels)
    unwindProtect(progn(
      arsCv=dbOpenCellViewByType("Two_Stage_Opamp" arsCell "schematic" "" "r")
      unless(and(arsCv !dbIsCellViewModified(arsCv)) error("source missing/modified"))
      when(length(arsCv~>instances)>64 error("source too large"))
      foreach(arsT arsCv~>terminals
        unless(length(arsT~>pins)==1 error("multiple terminal figures unsupported"))
        arsPins=cons(car(arsT~>pins)~>fig arsPins)
        arsPorts=cons(list(arsT~>name arsT~>direction arsT~>net~>name) arsPorts))
      foreach(arsI arsCv~>instances
        unless(member(arsI arsPins)
          unless(and(arsI~>viewName=="symbol" member(arsI~>libName list("gpdk045" "analogLib" "Two_Stage_Opamp")))
            error("unsupported reference master"))
          arsInsts=cons(list(arsI~>name arsI~>libName arsI~>cellName arsI~>xy arsI~>orient
            foreach(mapcar arsP arsI~>prop list(arsP~>name arsP~>valueType arsP~>value))
            aiGateParams(arsI) aiRebuildConnections(arsI)) arsInsts)))
      foreach(arsS arsCv~>shapes
        when(and(arsS~>objType=="line" equal(arsS~>lpp list("wire" "drawing")))
          unless(and(arsS~>net~>name length(arsS~>points)==2) error("unconnected/nonline wire unsupported"))
          arsWires=cons(list(arsS~>net~>name arsS~>points) arsWires)))
      arsLabels=foreach(mapcar arsL setof(arsS arsCv~>shapes and(arsS~>objType=="label" equal(arsS~>lpp list("wire" "label"))))
        list(arsL~>theLabel arsL~>xy arsL~>parent~>points))
      arsResult=list(arsCell reverse(arsInsts) sort(arsPorts 'aiConfigRowLess) reverse(arsWires) aiGateSnapshot(arsCv) arsLabels))
      when(arsCv dbClose(arsCv)))
    return(arsResult)))
procedure(aiRebuildSources()
  foreach(mapcar assCell list("OpAmp" "OpAmp_AC_top" "OpAmp_TRAN_top" "opamp_dc_feedback") aiRebuildSource(assCell)))
; Recheck library identities on create, inspect and every run, including work path.
procedure(aiRebuildPaths(arpEntry)
  unless(and(ddGetObj(arpEntry['lib]) ddIsObjWritable(ddGetObj(arpEntry['lib]))
    simplifyFilename(ddGetObj(arpEntry['lib])~>readPath)==arpEntry['workPath]
    simplifyFilename(ddGetObj("gpdk045")~>readPath)==simplifyFilename(strcat(arpEntry['root] "/gpdk045"))
    simplifyFilename(ddGetObj("Two_Stage_Opamp")~>readPath)==arpEntry['sourcePath]) error("library path changed"))
  when(member(arpEntry['workPath] list(arpEntry['sourcePath]
    simplifyFilename(strcat(arpEntry['root] "/gpdk045"))
    simplifyFilename(ddGetObj("basic")~>readPath) simplifyFilename(ddGetObj("analogLib")~>readPath)))
    error("work library aliases a source library"))
  t)
procedure(aiRebuildMasters(arqSources)
  prog((arqRows arqKey arqCv arqKeys)
    foreach(arqSource arqSources
      foreach(arqRow cadr(arqSource)
        arqKey=list(cadr(arqRow) caddr(arqRow))
        unless(member(arqKey arqKeys) arqKeys=cons(arqKey arqKeys))))
    foreach(arqKey arqKeys
      arqCv=nil
      unwindProtect(progn(
        arqCv=dbOpenCellViewByType(car(arqKey) cadr(arqKey) "symbol" "" "r")
        unless(and(arqCv !dbIsCellViewModified(arqCv)) error("symbol master missing/modified"))
        arqRows=cons(list(arqKey aiGateSnapshot(arqCv)) arqRows)) when(arqCv dbClose(arqCv))))
    return(arqRows)))
procedure(aiRebuildSourceContract(arqSources)
  foreach(arqS arqSources
    foreach(arqR cadr(arqS)
      unless(cond(
        (cadr(arqR)=="gpdk045" member(caddr(arqR) if(car(arqS)=="opamp_dc_feedback"
          list("ndio") list("nmos1v" "pmos1v" "resnsppoly" "mimcap"))))
        (cadr(arqR)=="analogLib" member(caddr(arqR) list("vdc" "vpulse" "idc" "cap" "res" "gnd" "vcvs")))
        (cadr(arqR)=="Two_Stage_Opamp" and(member(car(arqS) list("OpAmp_AC_top" "OpAmp_TRAN_top"))
          member(caddr(arqR) list("OpAmp" "opamp_dc_feedback"))))) error("unqualified reference master"))
      foreach(arqP nth(5 arqR)
        unless(member(cadr(arqP) list("string" "int" "float" "boolean")) error("unsupported property type")))))
  t)
