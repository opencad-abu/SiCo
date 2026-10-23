; Bounded, read-only JSON helpers. Named cellviews open "r" and close before return.
unless(and(boundp('aiInspectVersion)
             aiInspectVersion=="20261022.ai.inspect.v11")

procedure(aiInspectSchematic(lib cell view viewType maxItems includePlacement)
  let((limit target cv budget instances nets terminals remaining entry appended
             count truncated body)
    limit=aiInspectMaxItems(maxItems)
    target=aiInspectOpenTarget(lib cell view viewType)
    cv=target['cv]
    budget=aiInspectNewBudget()
    if(!cv then
      aiInspectTargetError("schematic" target limit)
    else
      body=errset(progn(
        instances="" nets="" terminals="" truncated=nil
        remaining=cv~>instances count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectSchematicInstanceJson(car(remaining) includePlacement budget)
          appended=aiInspectAppendJson(instances entry budget)
          if(cadr(appended) then
            progn(
              instances=car(appended)
              remaining=cdr(remaining)
              count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        remaining=cv~>nets count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectNetJson(car(remaining) budget)
          appended=aiInspectAppendJson(nets entry budget)
          if(cadr(appended) then
            progn(nets=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        remaining=cv~>terminals count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectTerminalJson(car(remaining) budget)
          appended=aiInspectAppendJson(terminals entry budget)
          if(cadr(appended) then
            progn(terminals=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        sprintf(nil
          "{\"ok\":true,\"kind\":\"schematic\",\"cellview\":%s,\"modified\":%s,\"instances\":[%s],\"nets\":[%s],\"terminals\":[%s],\"max_items\":%d,\"truncated\":%s}"
          aiInspectCellViewJson(cv budget) aiInspectCellViewModifiedJson(cv)
          instances nets terminals limit
          aiInspectJsonBoolean(or(truncated budget['full] budget['textTruncated])))) nil)
      aiInspectCloseTarget(target)
      if(body then car(body) else
        aiInspectError("schematic" "inspection_failed"
          "schematic inspection failed" limit)))
  )
)

procedure(aiInspectLayout(lib cell view viewType maxItems)
  let((limit target cv budget shapes instances remaining entry appended count
             truncated body)
    limit=aiInspectMaxItems(maxItems)
    target=aiInspectOpenTarget(lib cell view viewType)
    cv=target['cv]
    budget=aiInspectNewBudget()
    if(!cv then
      aiInspectTargetError("layout" target limit)
    else
      body=errset(progn(
        shapes="" instances="" truncated=nil
        remaining=cv~>shapes count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectLayoutShapeJson(car(remaining) budget)
          appended=aiInspectAppendJson(shapes entry budget)
          if(cadr(appended) then
            progn(shapes=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        remaining=cv~>instances count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectLayoutInstanceJson(car(remaining) budget)
          appended=aiInspectAppendJson(instances entry budget)
          if(cadr(appended) then
            progn(instances=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        sprintf(nil
          "{\"ok\":true,\"kind\":\"layout\",\"cellview\":%s,\"modified\":%s,\"shapes\":[%s],\"instances\":[%s],\"max_items\":%d,\"truncated\":%s}"
          aiInspectCellViewJson(cv budget) aiInspectCellViewModifiedJson(cv)
          shapes instances limit
          aiInspectJsonBoolean(or(truncated budget['full] budget['textTruncated])))) nil)
      aiInspectCloseTarget(target)
      if(body then car(body) else
        aiInspectError("layout" "inspection_failed" "layout inspection failed" limit)))
  )
)

procedure(aiInspectSymbolPorts(lib cell view viewType maxItems)
  let((limit target cv budget terminals portOrder termOrder remaining entry appended
             count truncated body)
    limit=aiInspectMaxItems(maxItems)
    target=aiInspectOpenTarget(lib cell view viewType)
    cv=target['cv]
    budget=aiInspectNewBudget()
    if(!cv then
      aiInspectTargetError("symbol" target limit)
    else
      body=errset(progn(
        terminals="" portOrder="" termOrder="" truncated=nil
        remaining=cv~>terminals count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectTerminalJson(car(remaining) budget)
          appended=aiInspectAppendJson(terminals entry budget)
          if(cadr(appended) then
            progn(terminals=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        remaining=cv~>portOrder count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectJsonString(car(remaining) budget)
          appended=aiInspectAppendJson(portOrder entry budget)
          if(cadr(appended) then
            progn(portOrder=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        remaining=cv~>termOrder count=0
        while(remaining && count<limit && !budget['full]
          entry=aiInspectJsonString(car(remaining) budget)
          appended=aiInspectAppendJson(termOrder entry budget)
          if(cadr(appended) then
            progn(termOrder=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        sprintf(nil
          "{\"ok\":true,\"kind\":\"symbol\",\"cellview\":%s,\"modified\":%s,\"terminals\":[%s],\"port_order\":[%s],\"term_order\":[%s],\"max_items\":%d,\"truncated\":%s}"
          aiInspectCellViewJson(cv budget) aiInspectCellViewModifiedJson(cv)
          terminals portOrder termOrder limit
          aiInspectJsonBoolean(or(truncated budget['full] budget['textTruncated])))) nil)
      aiInspectCloseTarget(target)
      if(body then car(body) else
        aiInspectError("symbol" "inspection_failed" "symbol inspection failed" limit)))
  )
)

procedure(aiListLibraries(maxItems)
  let((limit budget libraries remaining entry appended count truncated protected body)
    limit=aiInspectMaxItems(maxItems)
    budget=aiInspectNewBudget()
    protected=if(isCallable('ddGetLibList) then errset(ddGetLibList() nil) else nil)
    if(!protected then
      aiInspectError("libraries" "library_list_unavailable"
        "could not read the Virtuoso library list" limit)
    else
      body=errset(progn(
        libraries="" truncated=nil remaining=car(protected) count=0
        while(remaining && count<limit && !budget['full]
          entry=car(errset(car(remaining)~>name nil))
          entry=sprintf(nil "{\"name\":%s}" aiInspectJsonString(entry budget))
          appended=aiInspectAppendJson(libraries entry budget)
          if(cadr(appended) then
            progn(libraries=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        sprintf(nil
          "{\"ok\":true,\"kind\":\"libraries\",\"libraries\":[%s],\"max_items\":%d,\"truncated\":%s}"
          libraries limit aiInspectJsonBoolean(or(truncated budget['full] budget['textTruncated])))) nil)
      if(body then car(body) else
        aiInspectError("libraries" "inspection_failed" "library inspection failed" limit)))
  )
)

procedure(aiInspectLibrary(name)
  let((budget libObj protected libName path techName body)
    budget=aiInspectNewBudget()
    protected=if(and(stringp(name) strlen(name)>0 isCallable('ddGetObj)) then
      errset(ddGetObj(name) nil) else nil)
    libObj=if(protected then car(protected) else nil)
    if(!libObj then
      aiInspectError("library" "library_not_found" "the requested library was not found" 1)
    else
      body=errset(progn(
        protected=errset(libObj~>name nil)
        libName=if(protected then car(protected) else name)
        protected=errset(libObj~>readPath nil)
        path=if(protected then car(protected) else nil)
        techName=nil
        when(isCallable('techGetTechLibName)
          protected=errset(techGetTechLibName(libObj) nil)
          when(protected techName=car(protected)))
        sprintf(nil
          "{\"ok\":true,\"kind\":\"library\",\"name\":%s,\"path\":%s,\"technology_library\":%s,\"truncated\":%s}"
          aiInspectJsonString(libName budget)
          aiInspectJsonNullableString(path budget)
          aiInspectJsonNullableString(techName budget)
          aiInspectJsonBoolean(budget['textTruncated]))) nil)
      if(body then car(body) else
        aiInspectError("library" "inspection_failed" "library inspection failed" 1)))
  )
)

procedure(aiInspectConfigListJson(value budget)
  let((items result remaining appended)
    items=if(stringp(value) then parseString(value " ") else nil)
    result=""
    remaining=items
    while(remaining && !budget['full]
      appended=aiInspectAppendJson(
        result
        aiInspectJsonString(car(remaining) budget)
        budget)
      if(cadr(appended) then
        progn(result=car(appended) remaining=cdr(remaining))
      else
        budget['full]=t)
    result
  )
)
)

; Read-only HDB configuration inspection.  The first adapter intentionally
; exposes global defaults and top identity only.  Formal AMS binding evidence
; must additionally come from a qualified HED/ADE-UNL/runams producer; this
; result is therefore a bounded input record, not a verification verdict.
procedure(aiInspectConfigBinding(lib cell view maxItems)
  let((limit budget config protected topLib topCell topView libList viewList stopList body)
    limit=aiInspectMaxItems(maxItems)
    budget=aiInspectNewBudget()
    protected=if(and(stringp(lib) stringp(cell) stringp(view)
                     isCallable('hdbOpen))
      then errset(hdbOpen(lib cell view "r" "CDBA") nil) else nil)
    config=if(protected then car(protected) else nil)
    if(!config then
      aiInspectError("config_binding" "config_not_found"
        "could not open the requested configuration view in read mode" limit)
    else
      body=errset(progn(
        topLib=if(isCallable('hdbGetTopLibName) then hdbGetTopLibName(config) else nil)
        topCell=if(isCallable('hdbGetTopCellName) then hdbGetTopCellName(config) else nil)
        topView=if(isCallable('hdbGetTopViewName) then hdbGetTopViewName(config) else nil)
        libList=if(isCallable('hdbGetDefaultLibListString)
          then hdbGetDefaultLibListString(config) else nil)
        viewList=if(isCallable('hdbGetDefaultViewListString)
          then hdbGetDefaultViewListString(config) else nil)
        stopList=if(isCallable('hdbGetDefaultStopListString)
          then hdbGetDefaultStopListString(config) else nil)
        hdbClose(config)
        sprintf(nil
          "{\"ok\":true,\"kind\":\"config_binding\",\"schema_version\":\"aivw.config.inspection.v1\",\"identity\":{\"library\":%s,\"cell\":%s,\"view\":%s},\"top\":{\"library\":%s,\"cell\":%s,\"view\":%s},\"liblist\":[%s],\"viewlist\":[%s],\"stoplist\":[%s],\"cell_bindings\":[],\"instance_bindings\":[],\"discipline_bindings\":[],\"connect_rules\":[],\"source_artifacts\":[],\"complete\":%s,\"truncated\":%s}"
          aiInspectJsonString(lib budget)
          aiInspectJsonString(cell budget)
          aiInspectJsonString(view budget)
          aiInspectJsonNullableString(topLib budget)
          aiInspectJsonNullableString(topCell budget)
          aiInspectJsonNullableString(topView budget)
          aiInspectConfigListJson(libList budget)
          aiInspectConfigListJson(viewList budget)
          aiInspectConfigListJson(stopList budget)
          ; Global lists are useful diagnostics, but this first inspector does
          ; not yet enumerate HDB cell/instance overrides or AMS disciplines.
          ; Keep the formal binding incomplete until that producer is qualified.
          "false"
          aiInspectJsonBoolean(budget['full]))) nil)
      if(body then car(body) else
        progn(
          when(config hdbClose(config))
          aiInspectError("config_binding" "inspection_failed"
            "configuration inspection failed" limit))))
  )
)

procedure(aiListWindows(maxItems)
  let((limit budget windows remaining window cellView entry appended count
             truncated protected body)
    limit=aiInspectMaxItems(maxItems)
    budget=aiInspectNewBudget()
    protected=if(isCallable('hiGetWindowList) then errset(hiGetWindowList() nil) else nil)
    if(!protected then
      aiInspectError("windows" "window_list_unavailable"
        "could not read the Virtuoso window list" limit)
    else
      body=errset(progn(
        windows="" truncated=nil remaining=car(protected) count=0
        while(remaining && count<limit && !budget['full]
          window=car(remaining)
          protected=if(isCallable('geGetWindowCellView) then
            errset(geGetWindowCellView(window) nil) else nil)
          cellView=if(protected then car(protected) else nil)
          entry=sprintf(nil "{\"id\":%s,\"cellview\":%s}"
            aiInspectJsonString(sprintf(nil "%L" window) budget)
            aiInspectCellViewJson(cellView budget))
          appended=aiInspectAppendJson(windows entry budget)
          if(cadr(appended) then
            progn(windows=car(appended) remaining=cdr(remaining) count=count+1)
          else budget['full]=t))
        when(remaining truncated=t)
        sprintf(nil
          "{\"ok\":true,\"kind\":\"windows\",\"windows\":[%s],\"max_items\":%d,\"truncated\":%s}"
          windows limit aiInspectJsonBoolean(or(truncated budget['full] budget['textTruncated])))) nil)
      if(body then car(body) else
        aiInspectError("windows" "inspection_failed" "window inspection failed" limit)))
  )
)
; Generic master evidence: the cell's view list plus the saved symbol body
; (occupied bbox, pin centers, a suggested outward escape). The capture contract
; is the binding path's own reader (aiCrBodyData, served as aiCrBindingGeometry),
; so a read-only check matches server-side binding evidence. No PDK policy,
; classification or retention; the escape direction is advisory.
procedure(aiInspectMasterEscape(ameCenter ameBox)
  let((ameCx ameCy ameX0 ameY0 ameX1 ameY1 ameDx ameDy)
    ameCx=car(ameCenter) ameCy=cadr(ameCenter)
    ameX0=car(car(ameBox)) ameY0=cadr(car(ameBox))
    ameX1=car(cadr(ameBox)) ameY1=cadr(cadr(ameBox))
    ameDx=ameCx-(ameX0+ameX1)/2.0 ameDy=ameCy-(ameY0+ameY1)/2.0
    if(abs(ameDy)>=abs(ameDx)
      if(ameDy>=0 "up" "down")
      if(ameDx>=0 "right" "left"))))
procedure(aiInspectMasterOnBoundary(ameCenter ameBox)
  let((ameCx ameCy ameEps)
    ameCx=car(ameCenter) ameCy=cadr(ameCenter) ameEps=0.0001
    or(abs(ameCx-car(car(ameBox)))<=ameEps
      abs(ameCx-car(cadr(ameBox)))<=ameEps
      abs(ameCy-cadr(car(ameBox)))<=ameEps
      abs(ameCy-cadr(cadr(ameBox)))<=ameEps)))
procedure(aiInspectMasterViews(ameCellObj)
  if(ameCellObj
    sort(foreach(mapcar ameView ameCellObj~>views ameView~>name) 'alphalessp)
    nil))
; Raw number pairs only; car/cadr stay callable after protected-context relocation.
procedure(aiInspectMasterPointJson(ameCenter)
  sprintf(nil "[%s,%s]"
    aiInspectJsonNumber(car(ameCenter)) aiInspectJsonNumber(cadr(ameCenter))))
procedure(aiInspectMasterBBoxJson(ameBox)
  if(ameBox
    sprintf(nil "[[%s,%s],[%s,%s]]"
      aiInspectJsonNumber(car(car(ameBox))) aiInspectJsonNumber(cadr(car(ameBox)))
      aiInspectJsonNumber(car(cadr(ameBox))) aiInspectJsonNumber(cadr(cadr(ameBox))))
    "null"))
procedure(aiInspectMasterPinsJson(ameCenters ameBox budget)
  let((ameIndex ameItems ameEntry)
    ameIndex=0 ameItems=""
    foreach(ameCenter ameCenters
      ameEntry=sprintf(nil "{\"id\":\"pin%d\",\"xy\":%s,\"suggested_escape\":%s,\"on_boundary\":%s}"
        ameIndex
        aiInspectMasterPointJson(ameCenter)
        aiInspectJsonString(aiInspectMasterEscape(ameCenter ameBox) budget)
        aiInspectJsonBoolean(aiInspectMasterOnBoundary(ameCenter ameBox)))
      ameItems=strcat(ameItems if(ameIndex>0 "," "") ameEntry)
      ameIndex=ameIndex+1)
    ameItems))
procedure(aiInspectMasterTerminalsJson(ameData budget)
  buildString(foreach(mapcar ameTerm ameData['pin_centers]
    sprintf(nil "{\"name\":%s,\"pins\":[%s]}"
      aiInspectJsonString(car(ameTerm) budget)
      aiInspectMasterPinsJson(cadr(ameTerm) ameData['occupied_bbox] budget))) ","))
procedure(aiInspectMasterRow(ameLib ameCell ameView budget)
  let((ameCellObj ameViews amePath ameBody ameData ameReason ameSig)
    ameCellObj=if(isCallable('ddGetObj) car(errset(ddGetObj(ameLib ameCell) nil)) nil)
    ameViews=aiInspectMasterViews(ameCellObj)
    amePath=if(ameCellObj
      car(errset(simplifyFilename(ddGetObjReadPath(ddGetObj(ameLib))) nil))
      nil)
    ameBody=if(ameCellObj && amePath errset(aiCrBodyData(ameLib ameCell ameView amePath) nil) nil)
    ameReason=cond(
      (!ameCellObj "cell not found")
      (!amePath "library read path unavailable")
      ((!ameBody) aiCrErrorText())
      (t nil))
    if(ameBody
      progn(
        ameData=car(ameBody)
        ameSig=ameData['signature]
        sprintf(nil "{\"library\":%s,\"cell\":%s,\"view\":%s,\"found\":true,\"views\":[%s],\"library_path\":%s,\"binding_ready\":true,\"occupied_bbox\":%s,\"annotation_bbox\":%s,\"terminals\":[%s],\"signature_chars\":%d,\"signature_head\":%s}"
          aiInspectJsonString(ameLib budget) aiInspectJsonString(ameCell budget)
          aiInspectJsonString(ameView budget)
          buildString(foreach(mapcar ameName ameViews aiInspectJsonString(ameName budget)) ",")
          aiInspectJsonNullableString(amePath budget)
          aiInspectMasterBBoxJson(ameData['occupied_bbox])
          aiInspectMasterBBoxJson(ameData['annotation_bbox])
          aiInspectMasterTerminalsJson(ameData budget)
          strlen(ameSig)
          aiInspectJsonString(if(strlen(ameSig)>160 substring(ameSig 1 160) ameSig) budget)))
      sprintf(nil "{\"library\":%s,\"cell\":%s,\"view\":%s,\"found\":%s,\"views\":[%s],\"library_path\":%s,\"binding_ready\":false,\"reason\":%s}"
        aiInspectJsonString(ameLib budget) aiInspectJsonString(ameCell budget)
        aiInspectJsonString(ameView budget)
        aiInspectJsonBoolean(ameCellObj!=nil)
        buildString(foreach(mapcar ameName ameViews aiInspectJsonString(ameName budget)) ",")
        aiInspectJsonNullableString(amePath budget)
        aiInspectJsonString(ameReason budget)))))
procedure(aiInspectMasterEvidence(ameTargets ameMaxItems)
  prog((limit budget rows remaining count row appended truncated)
    limit=aiInspectMaxItems(ameMaxItems)
    budget=aiInspectNewBudget()
    unless(and(listp(ameTargets) ameTargets
      forall(ameTarget ameTargets
        and(listp(ameTarget) length(ameTarget)==3
          forall(amePart ameTarget and(stringp(amePart) strlen(amePart)>0)))))
      return(aiInspectError("master_evidence" "invalid_targets"
        "targets must be non-empty library/cell/view triples" limit)))
    rows="" count=0 truncated=nil remaining=ameTargets
    while(remaining && count<limit && !budget['full]
      row=aiInspectMasterRow(car(car(remaining)) cadr(car(remaining)) caddr(car(remaining)) budget)
      appended=aiInspectAppendJson(rows row budget)
      if(cadr(appended)
        progn(rows=car(appended) remaining=cdr(remaining) count=count+1)
        budget['full]=t))
    when(remaining truncated=t)
    sprintf(nil "{\"ok\":true,\"kind\":\"master_evidence\",\"items\":[%s],\"count\":%d,\"max_items\":%d,\"truncated\":%s}"
      rows count limit
      aiInspectJsonBoolean(or(truncated budget['full] budget['textTruncated])))))
procedure(aiInspectRevision()
  "20261022.ai.inspect.v11"
)

aiInspectVersion="20261022.ai.inspect.v11"
)
