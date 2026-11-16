; Dummy-fill scope resolution and direct editable-shape application.
; The apply path creates one ordinary OA shape per plan record in a single
; SKILL command, so native Virtuoso undo/redo treats the whole fill as one step.

unless(and(boundp('sicoDmyFillVersion)
           sicoDmyFillVersion=="20261029.sico.dmyfill.v2")

unless(boundp('SICO_dmyfillPickedBox)
  SICO_dmyfillPickedBox=nil)

procedure(SICO_dmyfillRevision() "20261029.sico.dmyfill.v2")

procedure(SICO_dmyfillWindow(@optional window)
  or(window hiGetCurrentWindow()))

procedure(SICO_dmyfillCv(@optional window)
  let((win cv)
    win=SICO_dmyfillWindow(window)
    when(win
      cv=car(errset(geGetEditCellView(win) nil))
      unless(cv cv=car(errset(geGetWindowCellView(win) nil)))
      when(and(cv cv~>cellViewType=="maskLayout") cv))))

procedure(SICO_dmyfillBoxP(box)
  and(listp(box) length(box)==2
       listp(car(box)) listp(cadr(box))
       length(car(box))==2 length(cadr(box))==2))

procedure(SICO_dmyfillUnionBox(first second)
  when(and(SICO_dmyfillBoxP(first) SICO_dmyfillBoxP(second))
    list(
      list(min(car(car(first)) car(car(second)))
           min(cadr(car(first)) cadr(car(second))))
      list(max(car(cadr(first)) car(cadr(second)))
           max(cadr(cadr(first)) cadr(cadr(second)))))))

procedure(SICO_dmyfillBoxIntersect(first second)
  let((box)
    when(and(SICO_dmyfillBoxP(first) SICO_dmyfillBoxP(second))
      box=list(
        list(max(car(car(first)) car(car(second)))
             max(cadr(car(first)) cadr(car(second))))
        list(min(car(cadr(first)) car(cadr(second)))
             min(cadr(cadr(first)) cadr(cadr(second)))))
      when(and(lessp(car(car(box)) car(cadr(box)))
               lessp(cadr(car(box)) cadr(cadr(box))))
        box))))

procedure(SICO_dmyfillCellBox(cv)
  when(cv car(errset(cv~>bBox nil))))

procedure(SICO_dmyfillWindowBox(cv @optional window)
  let((win box)
    win=SICO_dmyfillWindow(window)
    when(win
      box=car(errset(hiGetViewBBox(win) nil))
      SICO_dmyfillBoxIntersect(box SICO_dmyfillCellBox(cv)))))

procedure(SICO_dmyfillSelectedBox(cv @optional window)
  let((win selected box fig bbox)
    box=SICO_dmyfillPickedBox
    unless(SICO_dmyfillBoxP(box)
      win=SICO_dmyfillWindow(window)
      when(win
        selected=car(errset(geGetSelSet(win) nil))
        when(listp(selected)
          foreach(fig selected
            bbox=car(errset(fig~>bBox nil))
            when(SICO_dmyfillBoxP(bbox)
              box=if(box SICO_dmyfillUnionBox(box bbox) bbox))))))
    when(SICO_dmyfillBoxP(box)
      SICO_dmyfillBoxIntersect(box SICO_dmyfillCellBox(cv)))))

procedure(SICO_dmyfillScopeBox(scope @optional window)
  let((cv box)
    cv=SICO_dmyfillCv(window)
    unless(cv
      error("dummy fill requires a writable maskLayout cellview"))
    box=case(scope
      (("cell" "current cell") SICO_dmyfillCellBox(cv))
      (("window" "current window") SICO_dmyfillWindowBox(cv window))
      (("selected" "selected box") SICO_dmyfillSelectedBox(cv window))
      (t error("unknown dummy-fill scope: %L" scope)))
    unless(SICO_dmyfillBoxP(box)
      error("dummy-fill scope has no usable region: %L" scope))
    list(scope box)))

procedure(SICO_dmyfillUndoReady()
  unless(isCallable('dbEnableUndo)
    error("Virtuoso database undo is unavailable"))
  ; Calling this function is harmless when undo is already enabled; some
  ; Virtuoso versions return nil in that case.
  errset(dbEnableUndo() nil)
  when(and(isCallable('hiGetUndoLimit) leqp(hiGetUndoLimit() 0))
    error("Virtuoso undo limit is zero"))
  t)

procedure(SICO_dmyfillPlanShape(cv head layer purpose values)
  let((points pointCount x y shape)
    unless(and(stringp(layer) stringp(purpose))
      error("dummy-fill plan has an invalid layer/purpose"))
    if(equal(head "RECT") then
      unless(length(values)==4
        error("dummy-fill RECT requires four coordinates"))
      shape=dbCreateRect(cv list(layer purpose)
        list(atof(nth(0 values)):atof(nth(1 values))
             atof(nth(2 values)):atof(nth(3 values))))
      unless(shape error("dummy-fill RECT creation failed"))
      t
    else
      pointCount=length(values)
      unless(and(geqp(pointCount 6) zerop(mod(pointCount 2)))
        error("dummy-fill POLY requires at least three coordinate pairs"))
      points=nil
      for(index 0 (pointCount-1) 2
        x=atof(nth(index values))
        y=atof(nth(index+1 values))
        unless(and(numberp(x) numberp(y))
          error("dummy-fill POLY has a non-numeric coordinate"))
        points=append1(points x:y))
      shape=dbCreatePolygon(cv list(layer purpose) points)
      unless(shape error("dummy-fill POLY creation failed"))
      t)))

procedure(SICO_dmyfillApplyPlanToCv(cv planPath)
  prog((port line tokens head count)
    unless(cv
      error("dummy fill requires a writable maskLayout cellview"))
    SICO_dmyfillUndoReady()
    port=infile(planPath)
    unless(port error("cannot read dummy-fill plan: %L" planPath))
    count=0
    unwindProtect(
      progn(
        unless(gets(line port)
          error("dummy-fill plan is empty"))
        unless(rexMatchp("^# dmyfill-plan v1" line)
          error("unsupported dummy-fill plan header"))
        while(gets(line port)
          tokens=parseString(line)
          when(tokens
            head=car(tokens)
            cond(
              (head == "#")
              (equal(head "RECT")
                SICO_dmyfillPlanShape(cv head nth(1 tokens) nth(2 tokens)
                  cdddr(tokens))
                count=count+1)
              (equal(head "POLY")
                SICO_dmyfillPlanShape(cv head nth(1 tokens) nth(2 tokens)
                  cdddr(tokens))
                count=count+1)
              (t error("unknown dummy-fill plan record: %L" head))))))
      close(port))
    return(count)))

procedure(SICO_dmyfillApplyPlan(planPath @optional window)
  let((cv)
    cv=SICO_dmyfillCv(SICO_dmyfillWindow(window))
    SICO_dmyfillApplyPlanToCv(cv planPath)))

sicoDmyFillVersion="20261029.sico.dmyfill.v2"
)
