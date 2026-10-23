; Read-only template capture. No invocation at load time; all targets are explicit.
; Requires AI_protocol.il. Persistent artifacts never contain live dbId handles.

procedure(aiTplGet(object attribute)
  let((answer)
    answer=errset(get(object attribute) nil)
    if(answer car(answer) nil)))

; Preserve the protocol codec exactly while bounding its growing-string copies.
procedure(aiTplQuoted(atqValue)
  let((atqParts atqStart)
    atqStart=1
    while(atqStart<=strlen(atqValue)
      atqParts=cons(aiJsonEscape(substring(atqValue atqStart min(256 strlen(atqValue)-atqStart+1)))
        atqParts)
      atqStart=atqStart+256)
    strcat("\"" buildString(reverse(atqParts) "") "\"")))

procedure(aiTplJson(value)
  let((parts)
    cond(
      (null(value) "null")
      (eq(value t) "true")
      (stringp(value) aiTplQuoted(value))
      (fixp(value) sprintf(nil "%d" value))
      (floatp(value) sprintf(nil "%.16g" value))
      (listp(value)
        parts=foreach(mapcar item value aiTplJson(item))
        strcat("[" buildString(parts ",") "]"))
      (t aiTplJson(sprintf(nil "%L" value))))))

procedure(aiTplFields(object attributes)
  let((parts)
    parts=""
    foreach(attribute attributes
      parts=strcat(parts if(parts=="" "" ",") aiTplJson(symbolToString(attribute)) ":"
        aiTplJson(aiTplGet(object attribute))))
    parts))

procedure(aiTplNet(object)
  if(object~>net object~>net~>name nil))

procedure(aiTplGlobal(net)
  prog((atgValue)
    atgValue=net~>isGlobal
    cond((or(eq(atgValue t) equal(atgValue "TRUE")) return(t))
      (or(null(atgValue) equal(atgValue "FALSE")) return(nil))
      (t error("Mixed/unknown global bus semantics cannot be captured: %s (%L)" net~>name atgValue)))))

; A property object can carry no name (degenerate or tool-internal entries in
; cv~>prop); the named property table cannot represent it, so it is skipped and
; the asset declares the scope limit instead of failing the whole capture.
procedure(aiTplPropName(appProperty)
  let((appName)
    appName=appProperty~>name
    and(stringp(appName) strlen(appName)>0)))

procedure(aiTplFigure(figure)
  if(figure then
    strcat("{" aiTplFields(figure '(objType bBox lpp points xy orient height justify font labelType
      ellipseBBox startAngle stopAngle width beginPt endPt))
      ",\"text\":" aiTplJson(aiTplGet(figure 'theLabel)) "}")
  else "null"))

procedure(aiTplWrite(port line)
  let((offset)
    offset=fileTell(port)
    when(or(strlen(line)>16777216 offset+strlen(line)>134217728)
      error("Template capture exceeds bounded artifact size"))
    fprintf(port "%s\n" line)))

procedure(aiTplRecord(port counts kind body)
  counts[kind]=counts[kind]+1
  aiTplWrite(port strcat("{\"kind\":" aiTplJson(kind) if(body=="" "" ",") body "}")))

procedure(aiTplObject(object)
  let((cv)
    cv=aiTplGet(object 'cellView)
    when(aiTplGet(object 'objType)=="cellView" cv=object)
    strcat("{\"object_type\":" aiTplJson(aiTplGet(object 'objType))
      ",\"name\":" aiTplJson(aiTplGet(object 'name))
      ",\"library\":" aiTplJson(when(cv cv~>libName))
      ",\"cell\":" aiTplJson(when(cv cv~>cellName))
      ",\"view\":" aiTplJson(when(cv cv~>viewName)) "}")))

procedure(aiTplBindingValue(value)
  let((parts)
    cond(
      (null(value) "null")
      (listp(value)
        parts=""
        foreach(member value
          parts=strcat(parts if(parts=="" "" ",") aiTplBindingValue(member)))
        strcat("[" parts "]"))
      (and(!numberp(value) !stringp(value) car(errset(dbIsId(value) nil))) aiTplObject(value))
      (t aiTplJson(value)))))

; Identify only the actual Cadence installation libraries; aliases retain identity.
procedure(aiTplBuiltinLibrary(atbLib)
  prog((atbObject atbPath atbRoot)
    atbObject=ddGetObj(atbLib)
    unless(atbObject return(nil))
    atbPath=simplifyFilename(ddGetObjReadPath(atbObject))
    atbRoot=car(getInstallPath())
    cond((atbPath==simplifyFilename(strcat(atbRoot "/etc/cdslib/basic")) return("basic"))
      (atbPath==simplifyFilename(strcat(atbRoot "/etc/cdslib/artist/analogLib")) return("analogLib")))
    return(nil)))

; Only installed, available symbol masters can be ignored electrical graphics.
procedure(aiTplGraphicRole(atgInst)
  let((atgBuiltin atgCell)
    when(and(atgInst~>master atgInst~>master~>cellViewType=="schematicSymbol")
      atgBuiltin=aiTplBuiltinLibrary(atgInst~>libName) atgCell=atgInst~>cellName
      cond((and(atgBuiltin=="basic" member(atgCell list("ipin" "opin" "iopin"))) "port_graphic")
        (and(atgBuiltin=="basic" atgCell=="noConn") "no_connect")
        (and(member(atgBuiltin list("basic" "analogLib"))
          member(atgCell list("gnd" "vdd"))) "supply_marker")))))

procedure(aiTplCaptureAsset(lib cell view asset port expectedSchView @optional (captureV2 nil))
  let((cv viewType counts unnamed masterAvailable masterView schAvailable connRef source sourceMatches
       bound siblings permuted bindingStatus kinds body dependencyBefore dependencyAfter nativeBefore nativeAfter)
    viewType=case(asset ("schematic" "schematic") ("symbol" "schematicSymbol") ("layout" "maskLayout"))
    if(!ddGetObj(lib cell view) then
      aiTplWrite(port strcat("{\"kind\":\"missing_asset\",\"asset\":" aiTplJson(asset)
        ",\"view\":" aiTplJson(view) ",\"reason\":\"view_not_found\"}"))
    else
      cv=dbOpenCellViewByType(lib cell view viewType "r")
      unless(cv error("Cannot read template view %s/%s/%s" lib cell view))
      unwindProtect(
        progn(
          unless(cv~>cellViewType==viewType error("Template source has an unexpected view type"))
          cv=aiTplSavedView(cv)
          counts=makeTable("aiTplCounts" 0)
          kinds=list("instance" "instance_terminal" "instance_property" "net" "terminal" "pin" "shape"
            "master_geometry" "binding" "layout_context" "cell_property" "mosaic")
          unnamed=nil
          foreach(property cv~>prop when(!aiTplPropName(property) unnamed=t))
          foreach(instance cv~>instances
            foreach(property instance~>prop when(!aiTplPropName(property) unnamed=t)))
    when(captureV2
      unless(isCallable('aiTplDependencies) error("Capture v2 geometry module is unavailable"))
      kinds=append(kinds list("instance_geometry" "port_anchor" "dependency_guard"))
      ; Warm walk: materialize parameterized master facts before the baseline.
      aiTplDependencies(cv t)
      dependencyBefore=aiTplDependencies(cv)
      when(isCallable('aiTplNativeContent) nativeBefore=aiTplNativeContent(cv)))
          aiTplWrite(port strcat("{\"kind\":\"asset_header\",\"asset\":" aiTplJson(asset)
            ",\"lib\":" aiTplJson(lib) ",\"cell\":" aiTplJson(cell) ",\"view\":" aiTplJson(view)
            ",\"library_path\":" aiTplJson(ddGetObjReadPath(ddGetObj(lib)))
            ",\"working_directory\":" aiTplJson(getWorkingDir())
            ",\"forced_cds_lib\":" aiTplJson(ddGetForcedLib())
            ",\"cds_include_chain\":null"
            ",\"classification_semantics\":\"explicit_evidence_v2\""
            ",\"net_global_semantics\":\"uniform_boolean_v1\""
            ",\"tool_version\":" aiTplJson(getVersion()) ",\"dbu_per_uu\":" aiTplJson(cv~>DBUPerUU)
            ",\"bbox\":" aiTplJson(cv~>bBox)
            if(and(captureV2 asset=="schematic")
              strcat(",\"wire_net_semantics\":\"oa_shape_net_v1\""
                ",\"electrical_observation\":" aiTplElectricalEvidence(cv)) "")
            if(nativeBefore strcat(",\"native_content\":" nativeBefore) "")
            if(captureV2 ",\"dynamic_text_policy\":\"ignored_for_electrical_graph\"" "")
            ",\"incomplete_features\":" aiTplJson(
              append(list("hierarchy_not_expanded" "inherited_connections_not_resolved"
                "bus_members_not_expanded" "dynamic_text_not_evaluated" "master_geometry_not_captured")
                if(unnamed list("unnamed_properties_not_recorded") nil))) "}"))
          when(asset=="layout"
            connRef=nil source=nil sourceMatches=nil
            when(isCallable('lxGetConnRef)
              connRef=car(errset(lxGetConnRef(cv) nil)))
            when(isCallable('lxGetSource)
              source=car(errset(lxGetSource(cv) nil)))
            when(source
              sourceMatches=and(source~>libName==lib source~>cellName==cell
                source~>viewName==expectedSchView))
            when(and(connRef !equal(connRef list("CELLVIEW" lib cell expectedSchView "")))
              sourceMatches=nil)
            bindingStatus=cond((and(source !sourceMatches) "source_conflict")
              (and(sourceMatches isCallable('bndGetBoundObjects)) "query_available_unverified")
              (t "binding_unavailable"))
            aiTplRecord(port counts "layout_context"
              strcat("\"conn_ref\":" aiTplJson(connRef) ",\"source\":" if(source aiTplObject(source) "null")
                ",\"binding_status\":" aiTplJson(bindingStatus)
                ",\"flatten_policy\":\"skip_missing_instances_no_reconstruction\"")))
          foreach(instance cv~>instances
            masterAvailable=ddGetObj(instance~>libName instance~>cellName instance~>viewName)
            masterView=when(masterAvailable
              if(isCallable('aiTplMasterView) aiTplMasterView(instance) instance~>master))
            schAvailable=ddGetObj(instance~>libName instance~>cellName "schematic")
            aiTplRecord(port counts "instance"
              strcat(aiTplFields(instance '(name libName cellName viewName xy orient))
                ",\"builtin_library\":" aiTplJson(aiTplBuiltinLibrary(instance~>libName))
                ",\"master_view_type\":" aiTplJson(when(masterView masterView~>cellViewType))
                ",\"bbox\":" aiTplJson(instance~>bBox)
                ",\"terminal_names\":" aiTplJson(when(masterView masterView~>terminals~>name))
                ",\"master_available\":" if(masterAvailable "true" "false")
                ",\"schematic_available\":" if(schAvailable "true" "false")))
            foreach(property instance~>prop
              when(aiTplPropName(property)
                aiTplRecord(port counts "instance_property"
                  strcat("\"instance\":" aiTplJson(instance~>name) ",\"name\":" aiTplJson(property~>name)
                    ",\"type\":" aiTplJson(property~>valueType)
                    ",\"value_skill\":" aiTplJson(sprintf(nil "%L" property~>value))))))
            foreach(instTerm instance~>instTerms
              aiTplRecord(port counts "instance_terminal"
                strcat("\"instance\":" aiTplJson(instance~>name) ",\"name\":" aiTplJson(instTerm~>name)
                  ",\"net\":" aiTplJson(aiTplNet(instTerm)))))
            when(asset=="layout"
              bound=nil siblings=nil permuted=nil
              when(and(sourceMatches isCallable('bndGetBoundObjects))
                bound=car(errset(bndGetBoundObjects(instance) nil))
                when(isCallable('bndGetSiblingBoundObjects)
                  siblings=car(errset(bndGetSiblingBoundObjects(instance) nil)))
                when(isCallable('lxGetPermutedInstTerms)
                  permuted=car(errset(lxGetPermutedInstTerms(instance) nil))))
              aiTplRecord(port counts "binding"
                strcat("\"instance\":" aiTplJson(instance~>name)
                  ",\"status\":" aiTplJson(if(bound "observed" bindingStatus))
                  ",\"objects\":" aiTplBindingValue(bound)
                  ",\"siblings\":" aiTplBindingValue(siblings)
                  ",\"permuted_terms\":" aiTplBindingValue(permuted)))))
          foreach(net cv~>nets
            aiTplRecord(port counts "net"
              strcat(aiTplFields(net '(name numBits sigType)) ",\"is_global\":" if(aiTplGlobal(net) "true" "false")
                ",\"instance_terminal_count\":" aiTplJson(length(net~>instTerms)))))
          foreach(terminal cv~>terminals
            aiTplRecord(port counts "terminal"
              strcat(aiTplFields(terminal '(name direction numBits)) ",\"net\":" aiTplJson(aiTplNet(terminal))))
            foreach(pin terminal~>pins
              aiTplRecord(port counts "pin"
                strcat("\"terminal\":" aiTplJson(terminal~>name) ",\"net\":" aiTplJson(aiTplNet(pin))
                  ",\"figure\":" aiTplFigure(pin~>fig)))))
          unless(asset=="layout"
            foreach(shape cv~>shapes
              aiTplRecord(port counts "shape"
                strcat("\"net\":" aiTplJson(aiTplNet(shape)) ",\"figure\":" aiTplFigure(shape)))))
          foreach(property cv~>prop
            when(aiTplPropName(property)
              aiTplRecord(port counts "cell_property"
                strcat("\"name\":" aiTplJson(property~>name) ",\"type\":" aiTplJson(property~>valueType)
                  ",\"value_skill\":" aiTplJson(sprintf(nil "%L" property~>value))))))
          foreach(mosaic cv~>mosaics
            aiTplRecord(port counts "mosaic"
              strcat(aiTplFields(mosaic '(name xy orient rows columns rowSpacing columnSpacing))
                ",\"status\":\"members_not_expanded\"")))
          unless(captureV2
            cv=aiTplSavedView(cv)
            when(dbIsCellViewModified(cv) error("Template source changed during capture")))
          when(captureV2
            when(asset=="schematic" aiTplCaptureGeometry(cv port counts))
            dependencyAfter=aiTplDependencies(cv)
            when(nativeBefore
              nativeAfter=aiTplNativeContent(cv)
              unless(equal(nativeBefore nativeAfter) error("Native source bytes changed during capture")))
            unless(equal(dependencyBefore dependencyAfter) error("Template source/master changed during capture"))
            aiTplRecord(port counts "dependency_guard"
              strcat("\"before\":" aiTplJson(dependencyBefore) ",\"after\":" aiTplJson(dependencyAfter))))
          body=""
          foreach(kind kinds
            body=strcat(body if(body=="" "" ",") aiTplJson(kind) ":" aiTplJson(counts[kind])))
          aiTplWrite(port strcat("{\"kind\":\"asset_footer\",\"complete\":true,\"counts\":{" body "}"
            if(nativeAfter strcat(",\"native_content_after\":" nativeAfter) "") "}")))
        dbClose(cv)))))

procedure(aiTplCapture(lib cell schematicView symbolView layoutView outputPath assets
  @optional (captureSchema "cad.template.capture.v1"))
  let((port completed view)
    when(isFile(outputPath) error("Template capture path exists"))
    unless(member(captureSchema list("cad.template.capture.v1" "cad.template.capture.v2"))
      error("Unsupported capture schema"))
    port=outfile(outputPath "w")
    unless(port error("Cannot create template capture artifact"))
    unwindProtect(
      progn(
        ; A background collection session has no editors: accepted saved views stay
        ; accepted for the whole session because this session's own reads may mark
        ; them dirty in memory.  Interactive sessions re-judge every bundle.
        unless(and(boundp('aiTplIsolatedSession) aiTplIsolatedSession)
          aiTplResetSavedViews())
        aiTplWrite(port strcat("{\"kind\":\"bundle_header\",\"schema_version\":" aiTplJson(captureSchema)
          ",\"assets\":" aiTplJson(assets) "}"))
        foreach(asset assets
          view=case(asset ("schematic" schematicView) ("symbol" symbolView) ("layout" layoutView))
          aiTplCaptureAsset(lib cell view asset port schematicView captureSchema=="cad.template.capture.v2"))
        aiTplWrite(port strcat("{\"kind\":\"bundle_footer\",\"complete\":true,\"asset_count\":" aiTplJson(length(assets)) "}"))
        completed=t)
      progn(close(port) unless(completed when(isFile(outputPath) deleteFile(outputPath)))))
    strcat("{\"ok\":true,\"artifact_path\":" aiTplJson(outputPath)
      ",\"schema_version\":" aiTplJson(captureSchema) "}")))

procedure(aiTemplatesRevision()
  "20261009.ai.templates.v18")
