; Generic read-only, point-expression RDB capture; no recipe/model/test names.
unless(boundp('aiResultsCaptures) aiResultsCaptures=makeTable("aiResultsCaptures" nil))
procedure(aiResultsRevision() "20260910.ai.results.v1")
procedure(aiResultsSession(arsRef)
  prog((arsEntry)
    arsEntry=aiCircuitContexts[arsRef]
    unless(and(arsEntry arsEntry['state]=="open" arsEntry['type]=="maestro"
      member(arsEntry['session] maeGetSessions())
      aiCircuitSessionMatches(arsEntry['session] arsEntry['target])) error("Maestro context changed"))
    when(arsEntry['window]
      unless(and(member(arsEntry['window] hiGetWindowList('all))
        axlGetWindowSession(arsEntry['window])==arsEntry['session]) error("Maestro window changed")))
    return(arsEntry['session])))
procedure(aiResultsValue(arvValue arvDepth)
  cond((arvDepth>8 error("parameter nesting limit"))
    (null(arvValue) nil)
    (numberp(arvValue)
      unless(and(arvValue==arvValue abs(arvValue)<1e300) error("nonfinite parameter")) arvValue)
    (stringp(arvValue)
      when(strlen(arvValue)>4096 error("parameter text limit")) arvValue)
    (listp(arvValue)
      when(length(arvValue)>128 error("parameter list limit"))
      foreach(mapcar arvItem arvValue aiResultsValue(arvItem arvDepth+1)))
    (symbolp(arvValue) symbolToString(arvValue))
    (t error("unsupported parameter value"))))
procedure(aiResultsOutput(aroOut)
  let((aroValue aroStatus)
    aroValue=aroOut->value
    aroStatus=cond((null(aroValue) "missing")
      (eq(aroValue 'Error) "error")
      (and(numberp(aroValue) aroValue==aroValue abs(aroValue)<1e300) "scalar")
      (t "non_scalar"))
    list("output" aroOut->testName aroOut->cornerName aroOut->pointID aroOut->name
      aroStatus if(aroStatus=="scalar" aroValue nil))))
procedure(aiResultsCapture(arcRef arcHistory arcId)
  prog((arcSession arcEntry arcDb arcH arcStatus arcRecords arcRow arcParams arcCount arcExprCount
    arcKeys arcKey arcResult arcPages arcPage arcLength arcText arcTests arcPoints arcCorners arcNames arcDefs)
    when(or(aiResultsCaptures[arcId] length(aiResultsCaptures)>=8)
      return(aiCircuitError("capture_limit" "Release previous capture or use a new capture ID")))
    arcResult=errset(progn(
      arcSession=aiResultsSession(arcRef) arcEntry=aiCircuitContexts[arcRef]
      arcH=axlGetHistoryEntry(axlGetMainSetupDB(arcSession) arcHistory)
      unless(and(arcH arcH!=0) error("exact history not found"))
      arcStatus=axlGetRunStatus(arcSession)
      unless(and(listp(arcStatus) length(arcStatus)==2 car(arcStatus)==cadr(arcStatus))
        error("session has active/unknown run; capture after it settles"))
      arcDb=maeReadResDB(?session arcSession ?historyName arcHistory)
      unless(arcDb error("history RDB unavailable"))
      foreach(arcRel arcDb->relxSetups()
        unless(and(member(arcRel->name list(nil 'null "null")) member(arcRel->age list(nil 'null "null")))
          error("reliability dimension is not supported by point schema v1")))
      arcPoints=arcDb->points()
      unless(and(arcPoints length(arcPoints)<=512) error("empty history or point limit exceeded"))
      arcCount=0 arcExprCount=0
      foreach(arcPoint arcPoints
        foreach(arcCorner arcPoint->corners()
          arcKey=list(arcPoint->id arcCorner->name)
          when(member(arcKey arcCorners) error("duplicate point/corner")) arcCorners=cons(arcKey arcCorners)
          arcParams=arcCorner->params()
          when(length(arcParams)>128 error("parameter count limit"))
          arcRecords=cons(list("coordinate" arcCorner->name arcPoint->id
            foreach(mapcar arcParam arcParams
              list(arcParam->name symbolToString(arcParam->type) aiResultsValue(arcParam->value 0)))) arcRecords)
          foreach(arcTest arcCorner->tests()
            arcCount=arcCount+1 when(arcCount>2048 error("test point limit"))
            arcKey=list(arcTest->name arcTest->cornerName arcTest->pointID)
            unless(and(arcTest->pointID==arcPoint->id arcTest->cornerName==arcCorner->name
              !member(arcKey arcTests)) error("duplicate or inconsistent test coordinate"))
            arcTests=cons(arcKey arcTests)
            arcRecords=cons(list("test" arcTest->name arcTest->cornerName arcTest->pointID
              symbolToString(arcTest->status) aiResultsValue(arcTest->info 0)) arcRecords)
            foreach(arcOut arcTest->outputs(?type 'expr)
              arcExprCount=arcExprCount+1 when(arcExprCount>4096 error("expression result limit"))
              arcKey=list(arcOut->testName arcOut->cornerName arcOut->pointID arcOut->name)
              unless(and(arcOut->testName==arcTest->name arcOut->cornerName==arcCorner->name
                arcOut->pointID==arcPoint->id !member(arcKey arcKeys)) error("duplicate or inconsistent output coordinate"))
              arcKeys=cons(arcKey arcKeys)
              arcRecords=cons(aiResultsOutput(arcOut) arcRecords)))))
      unless(and(arcCount>0 length(arcDb->tests())==arcCount
        length(arcDb->outputs(?type 'expr))==arcExprCount) error("unsupported aggregate or unrepresented RDB dimension"))
      foreach(arcTestKey arcTests
        unless(member(car(arcTestKey) arcNames)
          arcNames=cons(car(arcTestKey) arcNames)
          arcDefs=car(axlGetHistoryOutputs(arcSession arcH ?testName car(arcTestKey)))
          when(length(arcDefs)>512 error("history output definition limit"))
          foreach(arcDef arcDefs
            when(arcDef->expression
              unless(member(arcDef->evalType list(nil 'point "point")) error("aggregate evaluation type unsupported"))
              unless(stringp(arcDef->name) error("unnamed expression unsupported"))
              arcText=sprintf(nil "%L" arcDef->expression)
              when(strlen(arcText)>4096 error("expression definition limit"))
              arcRecords=cons(list("definition" car(arcTestKey) arcDef->name "point" arcText) arcRecords)))))
      ; Freeze JSON pages before returning. No live RDB reads during pagination.
      arcPage=nil arcLength=0
      foreach(arcRecord reverse(arcRecords)
        arcText=aiCircuitJson(arcRecord)
        when(strlen(arcText)>24000 error("single result record exceeds page limit"))
        when(arcLength+strlen(arcText)>24000
          arcPages=cons(strcat("[" buildString(reverse(arcPage) ",") "]") arcPages)
          arcPage=nil arcLength=0)
        arcPage=cons(arcText arcPage) arcLength=arcLength+strlen(arcText)+1)
      when(arcPage arcPages=cons(strcat("[" buildString(reverse(arcPage) ",") "]") arcPages))
      arcPages=reverse(arcPages)
      aiResultsCaptures[arcId]=arcPages
      t) t)
    unless(arcResult return(aiCircuitError("results_capture_failed" "History/context unsupported, active or unavailable; inspect CIW")))
    return(aiCircuitObject(list(list("ok" t) list("capture_id" arcId)
      list("target" arcEntry['target])
      list("library_path" simplifyFilename(ddGetObj(car(arcEntry['target]))~>readPath))
      list("context_ref" arcRef) list("history" arcHistory)
      list("session" arcSession) list("pages" length(arcPages)) list("test_points" arcCount)
      list("outputs" arcExprCount) list("scope" "point_expressions") list("source" "maeReadResDB"))))))
procedure(aiResultsPage(arpId arpIndex)
  prog((arpPages)
    arpPages=aiResultsCaptures[arpId]
    unless(and(arpPages fixp(arpIndex) arpIndex>=0 arpIndex<length(arpPages))
      return(aiCircuitError("capture_page_missing" "Capture/page unavailable")))
    return(strcat("{\"ok\":true,\"records\":" nth(arpIndex arpPages) "}"))))
procedure(aiResultsRelease(arrId)
  aiResultsCaptures[arrId]=nil
  ; Remove key so repeated read-only captures do not exhaust the table.
  remove(arrId aiResultsCaptures)
  aiCircuitObject(list(list("ok" t))))
