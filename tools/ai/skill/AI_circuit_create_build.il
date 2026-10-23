; Fixed sch* operations and direct connectivity/parameter readback for generic plans.
procedure(aiCrAppliedParams(acvInst acvRow)
  let((acvCdf)
    acvCdf=cdfGetInstCDF(acvInst)
    list(car(acvRow) foreach(mapcar acvValue nth(5 acvRow)
      list(car(acvValue) cdfFindParamByName(acvCdf car(acvValue))~>value)))))
procedure(aiCrSnapshot(acbCv @optional acbContinuing acbParameterInst)
  list(
    sort(foreach(mapcar acbInst acbCv~>instances aiCrValueText(
      list(acbInst~>name acbInst~>libName acbInst~>cellName acbInst~>viewName acbInst~>transform
        sort(foreach(mapcar acbProp setof(acbSp acbInst~>prop
          !and(acbInst==acbParameterInst cdfFindParamByName(cdfGetInstCDF(acbInst) acbSp~>name))) aiCrValueText(
          list(acbProp~>name acbProp~>valueType acbProp~>value))) 'alphalessp)
        unless(acbInst==acbParameterInst aiCrCdfRows(cdfGetInstCDF(acbInst)))
        sort(foreach(mapcar acbIt acbInst~>instTerms
          aiCrValueText(list(acbIt~>name acbIt~>net~>name))) 'alphalessp)))) 'alphalessp)
    sort(foreach(mapcar acbShape acbCv~>shapes aiCrValueText(
      list(acbShape~>objType acbShape~>lpp acbShape~>bBox acbShape~>points acbShape~>theLabel
        when(acbShape~>objType=="label" list(acbShape~>xy acbShape~>justify acbShape~>orient
          acbShape~>font acbShape~>height acbShape~>isVisible acbShape~>isDrafted
          acbShape~>isOverbar acbShape~>labelType acbShape~>parent~>objType
          acbShape~>parent~>points))))) 'alphalessp)
    sort(foreach(mapcar acbTerm acbCv~>terminals aiCrValueText(
      list(acbTerm~>name acbTerm~>direction acbTerm~>net~>name))) 'alphalessp)
    ; ADE adds these environment caches when binding/netlisting this schematic.
    ; Continuation also updates schCheck caches and the automatic pin name counter.
    ; Keep those in strict snapshots used to detect edits between observation and execution.
    sort(foreach(mapcar acbProp setof(acbP acbCv~>prop
      !or(member(acbP~>name list("spectre" "viewNameList"))
        and(acbContinuing member(acbP~>name list("connectivityLastUpdated"
          "lastSchematicExtraction" "schGeometryLastUpdated" "pin#"))))) aiCrValueText(
      list(acbProp~>name acbProp~>valueType acbProp~>value))) 'alphalessp)
    sort(foreach(mapcar acbNet acbCv~>nets aiCrValueText(list(acbNet~>name
      sort(foreach(mapcar acbNp acbNet~>prop aiCrValueText(
        list(acbNp~>name acbNp~>valueType acbNp~>value))) 'alphalessp)))) 'alphalessp)))

procedure(aiCrReadback(acbCv acbEntry)
  prog((acbPayload acbInst acbTerm acbParam acbCdf acbParams)
    acbPayload=acbEntry['payload]
    unless(length(acbCv~>instances)==length(cadr(acbPayload))+length(caddr(acbPayload))+or(acbEntry['baseInstances] 0)
      error("created instance count differs"))
    foreach(acbRow cadr(acbPayload)
      acbInst=dbFindAnyInstByName(acbCv car(acbRow))
      unless(and(acbInst equal(list(acbInst~>libName acbInst~>cellName acbInst~>viewName) cadr(acbRow))
        equal(acbInst~>xy nth(3 acbRow)) acbInst~>orient==nth(4 acbRow)) error("instance master/placement changed"))
      unless(length(acbInst~>instTerms)==length(nth(6 acbRow)) error("instance terminal coverage differs"))
      foreach(acbConn nth(6 acbRow)
        acbTerm=car(setof(acbT acbInst~>instTerms acbT~>name==car(acbConn)))
        unless(and(acbTerm acbTerm~>net~>name==cadr(acbConn)) error("connection differs: %s.%s" car(acbRow) car(acbConn))))
      acbCdf=cdfGetInstCDF(acbInst)
      foreach(acbValue nth(5 acbRow)
        acbParam=cdfFindParamByName(acbCdf car(acbValue))
        unless(and(acbParam or(and(nth(11 acbRow)=="builtin:cdf-callbacks:v1"
          aiCdfCallbackEqual(if(dbFindProp(acbInst car(acbValue)) dbGet(acbInst car(acbValue))
            acbParam~>defValue) caddr(acbValue)))
          equal(dbGet(acbInst car(acbValue)) caddr(acbValue))
          and(nth(11 acbRow) !dbFindProp(acbInst car(acbValue)) equal(acbParam~>defValue caddr(acbValue)))))
          error("raw parameter property differs: %s.%s" car(acbRow) car(acbValue))))
      ; CDF may format numeric strings (e.g. 0.8 -> 800.0m). Require stability against
      ; the effective value captured immediately after application; never evaluate it ourselves.
      unless(equal(aiCrAppliedParams(acbInst acbRow) assoc(car(acbRow) acbEntry['appliedParams]))
        error("effective parameter values changed after application: %s" car(acbRow)))
      ; Keep the reply bounded by the input: report applied parameters, not every vendor CDF field.
      acbParams=cons(list(car(acbRow) foreach(mapcar acbValue nth(5 acbRow)
        list(car(acbValue) cdfFindParamByName(acbCdf car(acbValue))~>value))) acbParams))
    unless(length(acbCv~>terminals)==length(caddr(acbPayload))+or(acbEntry['basePorts] 0) error("port count differs"))
    foreach(acbPort caddr(acbPayload)
      unless(length(acbPort)==5 error(strcat("invalid planned port row " car(acbPort))))
      acbTerm=car(setof(acbT acbCv~>terminals acbT~>name==car(acbPort)))
      unless(and(acbTerm acbTerm~>direction==cadr(acbPort) acbTerm~>net~>name==car(acbPort))
        error("port direction/net differs"))
      ; Locate the created pin instance by master cell and origin: the readback
      ; reopens the saved cellview, so build-time DB IDs are stale.
      acbInst=car(setof(acbI acbCv~>instances
        and(member(acbI~>cellName list("ipin" "opin" "iopin")) equal(acbI~>xy caddr(acbPort)))))
      unless(acbInst error(strcat("port pin instance missing " car(acbPort))))
      unless(acbInst~>orient==nth(4 acbPort)
        error(strcat("port pin orientation differs " car(acbPort)))))
    when(acbEntry['baseSnapshot]
      let((acbAfter)
        acbAfter=aiCrSnapshot(acbCv t)
        for(acbIndex 0 4
          unless(forall(acbBefore nth(acbIndex acbEntry['baseSnapshot])
            member(acbBefore nth(acbIndex acbAfter))) error("Existing content changed during continuation")))))
    return(reverse(acbParams))))

procedure(aiCrExisting(acxCv acxEntry)
  prog((acxBackup acxView acxTarget acxSnapshot)
    acxTarget=car(acxEntry['payload])
    when(acxEntry['targetAction]=="continue"
      foreach(acxRow cadr(acxEntry['payload])
        when(dbFindAnyInstByName(acxCv car(acxRow)) error("New instance name conflicts with existing design")))
      foreach(acxPort caddr(acxEntry['payload])
        when(dbFindTermByName(acxCv car(acxPort)) error("New port conflicts with existing design")))
      acxEntry['baseInstances]=length(acxCv~>instances)
      acxEntry['basePorts]=length(acxCv~>terminals)
      acxEntry['baseSnapshot]=aiCrSnapshot(acxCv t))
    when(acxEntry['targetAction]=="replace"
      acxView=strcat("backup_" substring(acxEntry['ref] 9))
      when(ddGetObj(car(acxTarget) cadr(acxTarget) acxView) error("Backup target already exists"))
      acxSnapshot=aiCrSnapshot(acxCv)
      unwindProtect(progn(
        acxBackup=dbCopyCellView(acxCv car(acxTarget) cadr(acxTarget) acxView)
        unless(and(acxBackup dbSave(acxBackup) equal(acxSnapshot aiCrSnapshot(acxBackup)))
          error("Backup could not be saved and verified"))
        acxEntry['backup]=list(car(acxTarget) cadr(acxTarget) acxView))
        when(acxBackup dbClose(acxBackup)))
      acxEntry['stage]="replace_contents"
      foreach(acxObj acxCv~>instances dbDeleteObject(acxObj))
      foreach(acxObj acxCv~>shapes dbDeleteObject(acxObj))
      foreach(acxObj acxCv~>terminals dbDeleteObject(acxObj))
      foreach(acxObj acxCv~>nets dbDeleteObject(acxObj)))
    ; Approved in-place edit: the user reviewed this exact plan and the target
    ; record already proved the view still holds the reviewed contents, so the
    ; cellview may be cleared and rebuilt in place (instances, wires, labels,
    ; ports and nets) without a backup round trip.
    when(acxEntry['targetAction]=="edit"
      let((acxPlan acxMode acxDel acxMatched acxPorts)
        acxPlan=acxEntry['editPlan]
        unless(acxPlan error("Approved edit requires its reviewed plan"))
        ; Every approved edit keeps the reviewed view: the backup is the user's way
        ; back. It is a view-level copy inside the same cell (schematic_<request>),
        ; so the design cell itself is untouched and both views can be compared.
        acxEntry['stage]="edit_backup"
        acxView=strcat("schematic_" substring(acxEntry['ref] 9))
        aiCopyView(list(car(acxTarget) cadr(acxTarget) caddr(acxTarget))
          list(car(acxTarget) cadr(acxTarget) acxView))
        acxEntry['backup]=list(car(acxTarget) cadr(acxTarget) acxView)
        acxMode=cadr(assoc("mode" acxPlan))
        acxDel=cadr(assoc("delete" acxPlan))
        when(acxMode=="rebuild"
          ; Loss check: a rebuild keeps exactly what the plan declares, so any other
          ; device, port or note is the user's content and needs an explicit
          ; acknowledgement (edit.drops) before it is dropped.
          let((acxLost acxNamed acxPortNamed)
            foreach(acxRow cadr(acxEntry['payload]) acxNamed=cons(car(acxRow) acxNamed))
            foreach(acxPort caddr(acxEntry['payload]) acxPortNamed=cons(car(acxPort) acxPortNamed))
            foreach(acxInst acxCv~>instances
              when(and(!member(acxInst~>name acxNamed)
                !member(acxInst~>cellName list("ipin" "opin" "iopin")))
                acxLost=cons(strcat(acxInst~>name " (" acxInst~>cellName ")") acxLost)))
            foreach(acxTerm acxCv~>terminals
              unless(member(acxTerm~>name acxPortNamed)
                acxLost=cons(strcat("port " acxTerm~>name) acxLost)))
            when(and(acxLost !cadr(assoc("drops" acxPlan)))
              error(strcat("edit_loss_check: the rebuild would drop unlisted objects "
                aiCrValueText(sort(acxLost 'alphalessp))
                "; approve them explicitly with edit.drops"))))
          ; The approved plan rewrites the whole view: replace the view object itself
          ; (backed up above) instead of clearing object by object, so the rebuild
          ; starts from a guaranteed empty cellview with fresh connectivity caches.
          acxEntry['stage]="edit_replace_view"
          when(dbIsCellViewModified(acxCv)
            error("Save or close the edited view before an approved rebuild"))
          when(acxEntry['window]
            errset(hiCloseWindow(acxEntry['window]) t)
            acxEntry['window]=nil)
          foreach(acxOpen dbGetOpenCellViews()
            when(aiCircuitCvMatches(acxOpen acxTarget) dbClose(acxOpen)))
          when(ddGetObj(car(acxTarget) cadr(acxTarget) caddr(acxTarget))
            unless(ddDeleteObj(ddGetObj(car(acxTarget) cadr(acxTarget) caddr(acxTarget)))
              error("Could not delete the reviewed view for the approved rebuild")))
          acxCv=apply('dbOpenCellViewByType append(acxTarget list("schematic" "w")))
          unless(acxCv error("Rebuilt view could not be created"))
          when(acxEntry['mode]=="foreground"
            let((acxWindow)
              acxWindow=aiCircuitNativeCall('deOpenCellView append(acxTarget list("schematic" nil "a")))
              unless(acxWindow error("Rebuilt foreground window failed"))
              aiCircuitNativeCall('aiCircuitPlaceRightHalf list(acxWindow))
              aiCircuitNativeCall('aiCircuitRaiseWindow list(acxWindow))
              unless(and(geGetWindowCellView(acxWindow)==acxCv geGetEditCellView(acxWindow)==acxCv)
                error("Rebuilt window binding differs"))
              acxEntry['window]=acxWindow)))
        when(member(acxMode list("patch" "reroute"))
          acxEntry['stage]="edit_patch"
          unless(acxDel error("patch edit requires the reviewed delete list"))
          ; Deleted objects are found through the extracted connectivity (net~>shapes),
          ; so the caches must exist before the first deletion.
          unless(errset(schCheck(acxCv) t) error("connectivity check before the edit failed"))
          foreach(acxName cadr(assoc("instances" acxDel))
            let((acxInst)
              acxInst=dbFindAnyInstByName(acxCv acxName)
              when(acxInst dbDeleteObject(acxInst))))
          foreach(acxName cadr(assoc("ports" acxDel))
            let((acxTerm)
              acxTerm=dbFindTermByName(acxCv acxName)
              when(acxTerm dbDeleteObject(acxTerm))))
          foreach(acxName cadr(assoc("nets" acxDel))
            ; The extracted net owns its shapes; a label that still carries the name is
            ; removed too, so no stale stub survives a re-route.
            let((acxNet)
              acxNet=car(setof(x acxCv~>nets x~>name==acxName))
              when(acxNet foreach(acxShape acxNet~>shapes dbDeleteObject(acxShape)))
              foreach(acxShape setof(x acxCv~>shapes
                or(x~>net~>name==acxName x~>theLabel==acxName))
                dbDeleteObject(acxShape))))
          when(cadr(assoc("wires" acxDel))
            foreach(acxShape setof(x acxCv~>shapes member(x~>objType list("line" "label")))
              dbDeleteObject(acxShape)))
          ; Rebuild the connectivity caches before the builder touches the pins.
          unless(errset(schCheck(acxCv) t) error("connectivity check after deletion failed"))
          t)
        ; A declared instance that already exists is modified in place, not added.
        acxMatched=0
        foreach(acxRow cadr(acxEntry['payload])
          when(dbFindAnyInstByName(acxCv car(acxRow)) acxMatched=acxMatched+1))
        ; A declared port that is already drawn re-uses its pin instance, so it is
        ; not an added instance either.
        foreach(acxPort caddr(acxEntry['payload])
          when(car(setof(acxPin acxCv~>instances
              and(member(acxPin~>cellName list("ipin" "opin" "iopin"))
                equal(acxPin~>xy caddr(acxPort)))))
            acxMatched=acxMatched+1))
        acxEntry['baseInstances]=length(acxCv~>instances)-acxMatched
        acxPorts=0
        foreach(acxPort caddr(acxEntry['payload])
          when(dbFindTermByName(acxCv car(acxPort)) acxPorts=acxPorts+1))
        acxEntry['basePorts]=length(acxCv~>terminals)-acxPorts)
      acxEntry['cellView]=acxCv)
    when(!acxEntry['cellView] acxEntry['cellView]=acxCv)
    return(acxEntry['cellView])))

procedure(aiCrPrepareCallbacks(acpEntry)
  prog((acpMaster acpResult)
    acpEntry['stage]="parameter_validation"
    unwindProtect(progn(
      foreach(acpRow cadr(acpEntry['payload])
        when(nth(11 acpRow)=="builtin:cdf-callbacks:v1"
          acpEntry['instance]=car(acpRow)
          acpMaster=apply('dbOpenCellViewByType append(cadr(acpRow) list("" "r")))
          unless(acpMaster error("CDF master missing"))
          acpResult=aiCdfCallbackApply(nil acpMaster nth(5 acpRow) nil nil nil nil nil t)
          acpEntry['cdfPrepared]=cons(list(car(acpRow) acpResult) acpEntry['cdfPrepared])
          dbClose(acpMaster) acpMaster=nil))
      acpEntry['instance]=nil t)
      progn(
        acpEntry['failureDetail]=aiCrErrorText()
        when(acpMaster dbClose(acpMaster))))
    return(t)))

; ------------------------------------------------------------- view backup ---
; Copy one cellview to another location, with explicit source and destination
; library/cell/view parameters (modeled on abCopyLibByView for a single view).
; The edit flow uses it for a reviewed, verified view-level backup.

procedure(aiCopyView(acvSrc acvDst @optional acvOverwrite)
  prog((acvCv acvOwn acvTarget acvSnapshot acvCopy)
    unless(and(length(acvSrc)==3 length(acvDst)==3) error("view copy needs lib/cell/view"))
    when(or(!ddGetObj(car(acvDst)) !ddGetObj(car(acvDst) cadr(acvDst)))
      error(strcat("view copy destination library/cell missing: " cadr(acvDst))))
    acvTarget=ddGetObj(car(acvDst) cadr(acvDst) caddr(acvDst))
    when(and(acvTarget !acvOverwrite) error("view copy destination already exists"))
    foreach(acvOpen dbGetOpenCellViews() when(aiCircuitCvMatches(acvOpen acvSrc) acvCv=acvOpen))
    when(and(!acvCv apply('ddGetObj acvSrc))
      acvCv=apply('dbOpenCellViewByType append(acvSrc list("" "r"))) acvOwn=t)
    unless(acvCv error("view copy source missing"))
    unwindProtect(progn(
      acvSnapshot=aiCrSnapshot(acvCv)
      acvCopy=dbCopyCellView(acvCv car(acvDst) cadr(acvDst) caddr(acvDst))
      unless(acvCopy error("view copy failed"))
      unless(and(dbSave(acvCopy) equal(acvSnapshot aiCrSnapshot(acvCopy)))
        error("view copy could not be saved and verified"))
      when(acvCopy dbClose(acvCopy)) acvCopy=nil)
      when(and(acvOwn acvCv) dbClose(acvCv)))
    return(t)))

; ------------------------------------------------------------------ routing ---
; cadence_route rows hand one connection edge to the Virtuoso router: both end
; points are the real pin centres of the created cellview (master pin figure
; transformed by the instance transform; a pin instance uses its own master
; figure), then schCreateWire(cv "route" ...) draws the path and obeys the
; channel. A refused edge keeps a labelled stub per endpoint so same-named
; pieces still merge into one net.

procedure(aiCrBoxUnion(acbFirst acbSecond)
  if(acbFirst
    list(
      list(min(caar(acbFirst) caar(acbSecond)) min(cadar(acbFirst) cadar(acbSecond)))
      list(max(caadr(acbFirst) caadr(acbSecond)) max(cadadr(acbFirst) cadadr(acbSecond))))
    acbSecond))

procedure(aiCrRefCenter(acrCv acrEntry acrRef)
  ; acrRef = list("inst" instanceName terminalName) | list("port" portName)
  let((acrInst acrTerm acrBox)
    if(car(acrRef)=="port" then
      acrInst=cadr(assoc(cadr(acrRef) acrEntry['portInstances]))
      unless(acrInst error(strcat("pin instance missing for port " cadr(acrRef))))
      ; basic pin instances carry no instTerms; their master figure is the anchor
      foreach(acrTerm acrInst~>master~>terminals
        foreach(acrPin acrTerm~>pins
          when(acrPin~>fig acrBox=aiCrBoxUnion(acrBox acrPin~>fig~>bBox))))
    else
      acrInst=dbFindAnyInstByName(acrCv cadr(acrRef))
      unless(acrInst error(strcat("route instance missing " cadr(acrRef))))
      acrTerm=car(setof(acrIt acrInst~>instTerms acrIt~>name==caddr(acrRef)))
      unless(acrTerm error(strcat("route terminal missing " caddr(acrRef))))
      foreach(acrPin acrTerm~>term~>pins
        when(acrPin~>fig acrBox=aiCrBoxUnion(acrBox acrPin~>fig~>bBox))))
    when(acrBox
      ; Centre of the transformed master figure: two opposite corners map through
      ; the instance transform and their midpoint is the pin's connect point.
      let((acrLower acrUpper)
        acrLower=dbTransformPoint(car(acrBox) acrInst~>transform)
        acrUpper=dbTransformPoint(cadr(acrBox) acrInst~>transform)
        list((car(acrLower)+car(acrUpper))/2.0 (cadr(acrLower)+cadr(acrUpper))/2.0)))))

procedure(aiCrLongestSegment(acbSegments)
  let((acbBest acbLength acbValue)
    acbBest=nil acbLength=nil
    foreach(acbSegment acbSegments
      acbValue=abs(car(car(acbSegment~>points))-car(cadr(acbSegment~>points)))
        +abs(cadr(car(acbSegment~>points))-cadr(cadr(acbSegment~>points)))
      when(!acbBest || acbValue>acbLength acbBest=acbSegment acbLength=acbValue))
    acbBest))

procedure(aiCrEdgeLabel(acbCv acbEntry acbWire acbSegments)
  ; One label per successful net: the first edge of a net owns it and the label
  ; rides the longest segment so it stays readable on the drawn path.
  when(and(nth(5 nth(3 acbWire)) acbSegments)
    let((acbSegment acbMid)
      acbSegment=aiCrLongestSegment(acbSegments)
      acbMid=list((car(car(acbSegment~>points))+car(cadr(acbSegment~>points)))/2.0
        (cadr(car(acbSegment~>points))+cadr(cadr(acbSegment~>points)))/2.0)
      aiCrTextWire(acbCv acbEntry acbSegment acbMid car(acbWire)))))

procedure(aiCrStubTerminal(acbCv acbEntry acbNet acbRef acbEscape acbLength)
  ; Draw the endpoint's own labelled stub from its real pin centre, once.
  let((acbKey acbPoint acbEnd acbSegments)
    acbKey=strcat(car(acbRef) ":" cadr(acbRef) ":" or(caddr(acbRef) ""))
    unless(assoc(acbKey acbEntry['stubTerminals])
      acbPoint=aiCrRefCenter(acbCv acbEntry acbRef)
      unless(acbPoint error(strcat("fallback endpoint unresolved " acbKey)))
      acbEnd=list(car(acbPoint)+car(acbEscape)*acbLength cadr(acbPoint)+cadr(acbEscape)*acbLength)
      acbSegments=car(errset(schCreateWire(acbCv "draw" "full" list(acbPoint acbEnd) 0.0625 0.0625 0.0) t))
      unless(acbSegments error(strcat("fallback stub failed " acbKey)))
      foreach(acbSegment acbSegments aiCrAdditionBoxCheck(acbEntry acbSegment~>bBox))
      acbEntry['stubTerminals]=cons(list(acbKey t) acbEntry['stubTerminals])
      when(acbNet
        aiCrTextWire(acbCv acbEntry car(acbSegments) acbEnd acbNet)))
    t))

procedure(aiCrRouteEdge(acrCv acrEntry acrRow)
  ; acrRow = list(net points labelXY route); route = list(from to fromEscape toEscape stub labelP)
  let((acrRoute acrFrom acrTo acrSegments)
    acrRoute=nth(3 acrRow)
    acrFrom=aiCrRefCenter(acrCv acrEntry car(acrRoute))
    acrTo=aiCrRefCenter(acrCv acrEntry cadr(acrRoute))
    acrSegments=car(errset(schCreateWire(acrCv "route" "full" list(acrFrom acrTo) 0.0625 0.0625 0.0) t))
    if(acrSegments then
      foreach(acrSegment acrSegments aiCrAdditionBoxCheck(acrEntry acrSegment~>bBox))
      aiCrEdgeLabel(acrCv acrEntry acrRow acrSegments)
      acrEntry['routing]=cons(list(car(acrRow) "routed" length(acrSegments)) acrEntry['routing])
      t
      else
      aiCrStubTerminal(acrCv acrEntry car(acrRow) car(acrRoute) caddr(acrRoute) nth(4 acrRoute))
      aiCrStubTerminal(acrCv acrEntry car(acrRow) cadr(acrRoute) cadddr(acrRoute) nth(4 acrRoute))
      acrEntry['routing]=cons(list(car(acrRow) "stub_fallback" aiCrErrorText()) acrEntry['routing])
      nil)))

procedure(aiCrBuild(acbCv acbEntry)
  prog((acbPayload acbMaster acbInst acbType acbCdf acbSegments acbName acbOrient)
    acbPayload=acbEntry['payload]
    unwindProtect(progn(
      acbEntry['stage]="instances"
      foreach(acbRow cadr(acbPayload)
        acbEntry['instance]=car(acbRow)
        acbMaster=dbOpenCellViewByType(car(cadr(acbRow)) cadr(cadr(acbRow)) caddr(cadr(acbRow)) "" "r")
        unless(acbMaster error("master missing during build"))
        ; An approved edit may modify an instance that is already in the cellview:
        ; a declared name that exists is re-placed and re-parameterized in place.
        acbInst=dbFindAnyInstByName(acbCv car(acbRow))
        when(acbInst
          ; Only touch what the plan actually changes: rewriting an unchanged
          ; placement or parameter makes the editor drop the pin's wire handles.
          unless(and(equal(acbInst~>xy nth(3 acbRow)) acbInst~>orient==nth(4 acbRow))
            acbInst~>xy=nth(3 acbRow)
            acbInst~>orient=nth(4 acbRow))
          acbCdf=cdfGetInstCDF(acbInst)
          foreach(acbValue nth(5 acbRow)
            ; Compare canonically: an existing "0" must not be rewritten as
            ; numeric 0, because that fires the device CDF callback.
            when(!equal(aiCrValueText(dbGet(acbInst car(acbValue)))
              aiCrValueText(caddr(acbValue)))
              acbType=aiCrParam(acbCdf acbValue)
              unless(dbReplaceProp(acbInst car(acbValue) acbType caddr(acbValue))
                error("property write failed")))))
        unless(acbInst
          if(isCallable('aiCrCreateInstance) then
            acbInst=aiCrCreateInstance(acbCv acbMaster acbRow
              cadr(assoc(car(acbRow) acbEntry['cdfPrepared])))
            else
            when(nth(11 acbRow) error("project extension backend unavailable"))
            acbInst=schCreateInst(acbCv acbMaster car(acbRow) nth(3 acbRow) nth(4 acbRow))
            unless(acbInst error("schCreateInst failed"))
            acbCdf=cdfGetInstCDF(acbInst)
            foreach(acbValue nth(5 acbRow)
              acbType=aiCrParam(acbCdf acbValue)
              unless(dbReplaceProp(acbInst car(acbValue) acbType caddr(acbValue))
                error("property write failed"))))
          )
        ; Verify symbol geometry and CDF again after actual instance parameter application.
        aiCrMasterSignature(acbInst~>master acbRow)
        acbEntry['appliedParams]=cons(aiCrAppliedParams(acbInst acbRow) acbEntry['appliedParams])
        dbClose(acbMaster) acbMaster=nil)
      acbEntry['instance]=nil acbEntry['stage]="ports"
      foreach(acbPort caddr(acbPayload)
        unless(length(acbPort)==5 error(strcat("invalid planned port row " car(acbPort))))
        acbName=case(cadr(acbPort) ("input" "ipin") ("output" "opin") (t "iopin"))
        acbMaster=dbOpenCellViewByType("basic" acbName "symbol" "" "r")
        unless(acbMaster error("pin master missing"))
        ; The plan carries two independent facts: nth(3) is the stub direction
        ; (wire drawing only) and nth(4) is the planned pin-master orientation.
        ; Never derive the pin rotation from the stub direction.
        acbOrient=nth(4 acbPort)
        unless(member(acbOrient list("R0" "R90" "R180" "R270" "MX" "MY" "MXR90" "MYR90"))
          error(strcat("invalid planned pin orientation " car(acbPort))))
        unless(member(nth(3 acbPort) list("left" "right" "up" "down"))
          error(strcat("invalid planned pin escape " car(acbPort))))
        ; A patch keeps the port that is already drawn where the plan declares it;
        ; moving a port is expressed by deleting it in the reviewed plan.
        acbInst=car(setof(acbPin acbCv~>instances
          and(member(acbPin~>cellName list("ipin" "opin" "iopin"))
            equal(acbPin~>xy caddr(acbPort)))))
        if(acbInst then
          acbInst~>orient=acbOrient
          else
          acbInst=schCreatePin(acbCv acbMaster car(acbPort) cadr(acbPort) nil caddr(acbPort) acbOrient)
          unless(acbInst error("schCreatePin failed")))
        unless(dbFindTermByName(acbCv car(acbPort)) error("planned port missing"))
        ; Keep the created pin instance: routing resolves its real centre later.
        acbEntry['portInstances]=cons(list(car(acbPort) acbInst) acbEntry['portInstances])
        dbClose(acbMaster) acbMaster=nil)
      acbEntry['stage]="wires"
      ; cadence_route reads pin centres from the created cellview, so the
      ; connectivity caches must exist before the first edge is routed.
      when(setof(acbRow nth(3 acbPayload) nth(3 acbRow))
        unless(errset(schCheck(acbCv) t) error("connectivity check before routing failed")))
      foreach(acbWire nth(3 acbPayload)
        ; Draw rows are planned labelled stubs/pin links; a fourth element turns
        ; the row into a cadence_route edge owned by the Virtuoso router. Multi-point
        ; wires are created as one route; the label (when present) rides a drawn
        ; segment so the net name stays stable for readback.
        if(nth(3 acbWire) then
          aiCrRouteEdge(acbCv acbEntry acbWire)
          else
          acbSegments=schCreateWire(acbCv "draw" "full" cadr(acbWire) 0.0625 0.0625 0.0)
          unless(acbSegments
            error(strcat("wire creation failed: " aiCrValueText(cadr(acbWire))
              " on " aiCrValueText(car(acbWire)))))
          when(car(acbWire)
            aiCrTextWire(acbCv acbEntry car(acbSegments) caddr(acbWire) car(acbWire)))))
      acbEntry['stage]="notes"
      foreach(acbNote nth(4 acbPayload) aiCrTextNote(acbCv acbEntry acbNote))
      acbEntry['stage]="text_layout"
      aiCrTextFinish(acbCv acbEntry)
      t) when(acbMaster dbClose(acbMaster)))
    return(t)))
