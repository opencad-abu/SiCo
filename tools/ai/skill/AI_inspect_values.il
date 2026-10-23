; Bounded JSON values, budgets and read-only target handles.
; Loaded with AI_inspect.il by the versioned AI parent.
unless(and(boundp('aiInspectVersion)
             aiInspectVersion=="20261022.ai.inspect.v11")

procedure(aiInspectMaxItems(value)
  let((limit)
    limit=if(numberp(value) then fix(value) else 50)
    cond(
      ((limit<1) 1)
      ((limit>200) 200)
      (t limit))
  )
)

; Reserve headroom below aiEvalExpression's 64 KiB value limit.
procedure(aiInspectNewBudget()
  let((budget)
    budget=makeTable("aiInspectBudget" nil)
    budget['limit]=45000
    budget['used]=0
    budget['full]=nil
    budget['textTruncated]=nil
    budget
  )
)

; Snapshot records are streamed to a private file, so they are not subject to
; the inline 45 KiB response budget or 160-character display truncation.  The
; Python side enforces a 4 MiB maximum per JSONL record and rejects the whole
; snapshot if that bound is exceeded.
procedure(aiInspectNewSnapshotBudget()
  let((budget)
    budget=makeTable("aiInspectSnapshotBudget" nil)
    budget['limit]=4194303
    budget['used]=0
    budget['full]=nil
    budget['textTruncated]=nil
    budget['textLimit]=4194303
    budget
  )
)

procedure(aiInspectText(value budget @optional maxLength)
  let((text limit)
    limit=cond(
      (numberp(maxLength) fix(maxLength))
      (and(budget budget['textLimit]) budget['textLimit])
      (t 160))
    text=cond(
      (null(value) "")
      (stringp(value) value)
      (t sprintf(nil "%L" value)))
    when(strlen(text)>limit
      budget['textTruncated]=t
      text=substring(text 1 limit))
    text
  )
)

procedure(aiInspectJsonString(value budget)
  sprintf(nil "\"%s\"" aiJsonEscape(aiInspectText(value budget)))
)

procedure(aiInspectJsonNullableString(value budget)
  if(value then aiInspectJsonString(value budget) else "null")
)

procedure(aiInspectJsonNumber(value)
  cond(
    (fixp(value) sprintf(nil "%d" value))
    (floatp(value) sprintf(nil "%.12g" value))
    (t "null"))
)

procedure(aiInspectJsonBoolean(value)
  if(value then "true" else "false")
)

; Returns (new-array appended-p).  A failed append marks the shared budget
; full, allowing callers to preserve valid JSON without partial records.
procedure(aiInspectAppendJson(array item budget)
  let((separator size)
    separator=if(strlen(array)>0 then "," else "")
    size=strlen(separator)+strlen(item)
    if(budget['used]+size<=budget['limit] then
      progn(
        budget['used]=budget['used]+size
        list(strcat(array separator item) t))
    else
      progn(
        budget['full]=t
        list(array nil)))
  )
)

procedure(aiInspectPointJson(point)
  let((protected values)
    protected=errset(list(xCoord(point) yCoord(point)) nil)
    if(protected then
      values=car(protected)
      sprintf(nil "[%s,%s]"
        aiInspectJsonNumber(car(values))
        aiInspectJsonNumber(cadr(values)))
    else "null")
  )
)

procedure(aiInspectBBoxJson(bbox)
  let((protected values)
    protected=errset(list(xCoord(car(bbox)) yCoord(car(bbox))
                           xCoord(cadr(bbox)) yCoord(cadr(bbox))) nil)
    if(protected then
      values=car(protected)
      sprintf(nil "[[%s,%s],[%s,%s]]"
        aiInspectJsonNumber(car(values))
        aiInspectJsonNumber(cadr(values))
        aiInspectJsonNumber(caddr(values))
        aiInspectJsonNumber(cadddr(values)))
    else "null")
  )
)

procedure(aiInspectLppJson(lpp budget)
  let((protected values)
    protected=errset(list(car(lpp) cadr(lpp)) nil)
    if(protected then
      values=car(protected)
      sprintf(nil "{\"layer\":%s,\"purpose\":%s}"
        aiInspectJsonString(car(values) budget)
        aiInspectJsonString(cadr(values) budget))
    else "null")
  )
)

procedure(aiInspectCellViewJson(cellView budget)
  let((protected values)
    if(!cellView then
      "null"
    else
      protected=errset(
        list(cellView~>libName cellView~>cellName cellView~>viewName) nil)
      if(protected then
        values=car(protected)
        sprintf(nil "{\"lib\":%s,\"cell\":%s,\"view\":%s}"
          aiInspectJsonString(car(values) budget)
          aiInspectJsonString(cadr(values) budget)
          aiInspectJsonString(caddr(values) budget))
      else "null"))
  )
)

; Formal AIVW snapshots may only claim saved OA state.  Return JSON null if
; this IC build cannot establish the modified flag so callers fail closed.
procedure(aiInspectCellViewModifiedJson(cellView)
  let((protected)
    if(!cellView || !isCallable('dbIsCellViewModified) then
      "null"
    else
      protected=errset(dbIsCellViewModified(cellView) nil)
      if(protected then aiInspectJsonBoolean(car(protected)) else "null"))
  )
)

procedure(aiInspectOpenTarget(lib cell view viewType)
  let((target protected)
    target=makeTable("aiInspectTarget" nil)
    target['requested]=and(lib cell)
    target['owned]=nil
    target['cv]=nil
    if(target['requested] then
      when(isCallable('dbOpenCellViewByType)
        protected=errset(dbOpenCellViewByType(lib cell view viewType "r") nil)
        when(protected
          target['cv]=car(protected)
          target['owned]=target['cv]))
    else
      when(isCallable('geGetEditCellView)
        protected=errset(geGetEditCellView() nil)
        when(protected target['cv]=car(protected))))
    target
  )
)

procedure(aiInspectCloseTarget(target)
  when(and(target target['owned] target['cv] isCallable('dbClose))
    errset(dbClose(target['cv]) nil))
  nil
)

procedure(aiInspectError(kind code message maxItems)
  sprintf(nil
    "{\"ok\":false,\"kind\":\"%s\",\"error\":{\"code\":\"%s\",\"message\":\"%s\"},\"max_items\":%d,\"truncated\":false}"
    kind code aiJsonEscape(message) maxItems)
)

procedure(aiInspectTargetError(kind target maxItems)
  if(target['requested] then
    aiInspectError(kind "cellview_open_failed"
      "could not open the requested cellview in read mode" maxItems)
  else
    aiInspectError(kind "no_current_cellview"
      "there is no current editable cellview" maxItems))
)
)
