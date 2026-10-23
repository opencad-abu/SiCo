; Observe one immutable Ask source. Getter failures never prove target retirement.
procedure(sicoSourceRead(asrBinding)
  prog((asrObservation asrRef asrSignature asrCv asrAde asrCell asrHistory asrTests asrLibrary asrPath)
    when(asrBinding['sourceRetired] return(list('invalid asrBinding['sharedWindowRef])))
    unless(asrBinding['window] return(list('valid nil "null" nil nil)))
    asrObservation=sicoEntryObservation(asrBinding) asrRef=cadr(asrObservation)
    unless(car(asrObservation)=='valid return(list(car(asrObservation) asrRef)))
    asrSignature=caddr(asrObservation) asrCv=or(car(asrSignature) cadr(asrSignature))
    asrAde=caddr(asrSignature)
    asrCell=if(asrAde sicoAdeCellJson(asrAde) aiCellViewJson(asrCv))
    when(and(asrAde asrCell=="null") error("ADE identity unavailable"))
    when(or(and(asrBinding['cellJson] asrCell!=asrBinding['cellJson])
            and(asrBinding['adeSession] asrAde!=asrBinding['adeSession]))
      return(list('invalid asrRef)))
    when(asrBinding['historyHandle]
      unless(asrAde return(list('invalid asrRef)))
      asrHistory=axlGetHistoryEntry(axlGetMainSetupDB(asrAde) asrBinding['historyName])
      unless(and(axlGetHistoryName(asrBinding['historyHandle])==asrBinding['historyName]
                 asrHistory==asrBinding['historyHandle]) return(list('invalid asrRef))))
    when(asrBinding['testName]
      unless(asrAde return(list('invalid asrRef)))
      asrTests=axlGetTests(axlGetMainSetupDB(asrAde))
      unless(member(asrBinding['testName] cadr(asrTests)) return(list('invalid asrRef))))
    asrLibrary=if(asrAde axlGetSessionLibName(asrAde) when(asrCv asrCv~>libName))
    ; Library resolution is optional metadata, separate from native identity.
    when(asrLibrary asrPath=car(errset(ddGetObjReadPath(ddGetObj(asrLibrary)) nil)))
    asrObservation=sicoEntryObservation(asrBinding)
    unless(car(asrObservation)=='valid return(list(car(asrObservation) asrRef)))
    return(list('valid asrRef asrCell asrAde asrPath))))
procedure(sicoSourceObservation(asoBinding)
  prog((asoProtected asoResult)
    asoProtected=errset(sicoSourceRead(asoBinding) nil)
    asoResult=if(asoProtected car(asoProtected) list('unknown asoBinding['sharedWindowRef]))
    when(car(asoResult)=='invalid asoBinding['sourceRetired]=t)
    when(car(asoResult)=='valid
      ; Commit only a complete successful observation, never a partial read.
      unless(asoBinding['cellJson] asoBinding['cellJson]=caddr(asoResult))
      when(nth(3 asoResult) asoBinding['adeSession]=nth(3 asoResult)))
    return(asoResult)))
