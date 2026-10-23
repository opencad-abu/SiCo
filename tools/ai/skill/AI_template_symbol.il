; Task-bound consumption of a persistent template. Requires template/circuit helpers.
unless(boundp('aiTemplateSymbolPreviews) aiTemplateSymbolPreviews=makeTable("aiTemplateSymbolPreviews" nil))
unless(boundp('aiTemplateSymbols) aiTemplateSymbols=makeTable("aiTemplateSymbols" nil))
procedure(aiTemplateSymbolRevision() "20261020.ai.template.symbol.v8")
procedure(aiTsErrorText()
  let((ateText)
    ateText=sprintf(nil "%L" errset.errset)
    if(strlen(ateText)>2048 substring(ateText 1 2048) ateText)))

procedure(aiTsLibraryPath(atlName)
  prog((atlObject atlRead atlWrite)
    atlObject=ddGetObj(atlName)
    unless(atlObject error("library identity unavailable: %s" atlName))
    atlRead=ddGetObjReadPath(atlObject) atlWrite=ddGetObjWritePath(atlObject)
    unless(and(stringp(atlRead) strlen(atlRead)>0) error("library read path unavailable"))
    return(list(simplifyFilename(atlRead) when(atlWrite simplifyFilename(atlWrite))))))

procedure(aiTsPathWithin(atwPath atwRoot)
  or(atwPath==atwRoot
    and(strlen(atwPath)>strlen(atwRoot)
      substring(atwPath 1 strlen(atwRoot)+1)==strcat(atwRoot "/"))))

; Only the captured source design may receive its own missing symbol.
procedure(aiTsSourceDesignP(atsEntry atsPath)
  and(atsEntry['lib]==atsEntry['sourceLib]
    atsEntry['cell]==atsEntry['sourceCell]
    atsPath==simplifyFilename(atsEntry['sourcePath])))

; Read current target dependencies, including alias paths; no library-name policy.
procedure(aiTsLibraryGuard(atgEntry atgPath)
  prog((atgSource atgTech atgTechPath atgRows atgCv atgMasterPath atgDesign atgResult atgError)
    unless(and(stringp(atgEntry['sourcePath]) strlen(atgEntry['sourcePath])>0)
      error("source library identity unavailable"))
    atgSource=list(simplifyFilename(atgEntry['sourcePath]))
    when(ddGetObj(atgEntry['sourceLib])
      atgSource=append(atgSource aiTsLibraryPath(atgEntry['sourceLib])))
    foreach(atgRoot atgSource
      when(and(atgRoot or(aiTsPathWithin(atgPath atgRoot) aiTsPathWithin(atgRoot atgPath)))
        unless(aiTsSourceDesignP(atgEntry atgPath)
          error("separate work library required; source library only permits the source design unit"))))
    when(aiTsPathWithin(atgPath simplifyFilename(car(getInstallPath())))
      error("target is inside the Cadence installation"))
    atgTech=techGetTechLibName(ddGetObj(atgEntry['lib]))
    when(atgTech
      atgTechPath=aiTsLibraryPath(atgTech)
      when(member(atgPath atgTechPath) error("target is a technology library")))
    unwindProtect(progn(
      atgResult=errset(progn(
        atgCv=dbOpenCellViewByType(atgEntry['lib] atgEntry['cell] "schematic" "" "r")
        unless(and(atgCv !dbIsCellViewModified(atgCv)) error("saved target schematic required"))
        when(or(length(atgCv~>instances)>512 atgCv~>mosaics)
          error("target dependency inventory exceeds supported scope"))
        foreach(atgInstance atgCv~>instances
          unless(atgInstance~>master error("target dependency master unavailable"))
          atgMasterPath=aiTsLibraryPath(atgInstance~>libName)
          atgDesign=ddGetObj(atgInstance~>libName atgInstance~>cellName "schematic")
          when(and(member(atgPath atgMasterPath)
            or(!atgDesign atgInstance~>cellName==atgEntry['cell]))
            error("target aliases a device library or source design cell"))
          atgRows=cons(list(atgInstance~>name atgInstance~>libName atgInstance~>cellName
            atgInstance~>viewName atgMasterPath if(atgDesign t nil)) atgRows))
      t) nil)
      unless(atgResult atgError=aiTsErrorText())
      t) when(atgCv dbClose(atgCv)))
    unless(atgResult error("%s" atgError))
    return(list(atgSource atgTechPath sort(atgRows 'aiTsGuardLess)))))

procedure(aiTsGuardLess(atlLeft atlRight)
  alphalessp(sprintf(nil "%L" atlLeft) sprintf(nil "%L" atlRight)))

procedure(aiTsPath(atpEntry)
  prog((atpLib atpPaths atpPath atpGuard)
    atpLib=ddGetObj(atpEntry['lib])
    unless(and(atpLib ddIsObjWritable(atpLib)) error("writable work library required"))
    atpPaths=aiTsLibraryPath(atpEntry['lib]) atpPath=car(atpPaths)
    unless(atpPath==cadr(atpPaths) error("separate read/write library paths are unsupported"))
    when(and(atpEntry['workPath] atpEntry['workPath]!=atpPath) error("work library path changed"))
    unless(aiCircuitTasks[atpEntry['task]]==atpEntry['mode] error("confirmed task lost/changed"))
    when(and(atpEntry['mode]=="foreground" !aiCircuitGuiP()) error("foreground GUI unavailable"))
    atpGuard=aiTsLibraryGuard(atpEntry atpPath)
    when(and(atpEntry['libraryGuard] !equal(atpEntry['libraryGuard] atpGuard))
      error("source/technology/master library identity changed since preview"))
    unless(atpEntry['libraryGuard] atpEntry['libraryGuard]=atpGuard)
    return(atpPath)))

procedure(aiTsSchematic(atsEntry)
  prog((atsCv atsPorts)
    unless(aiCircuitViewType(atsEntry['lib] atsEntry['cell] "schematic")=="schematic"
      error("existing schematic required"))
    unwindProtect(progn(
      atsCv=dbOpenCellViewByType(atsEntry['lib] atsEntry['cell] "schematic" "" "r")
      unless(and(atsCv !dbIsCellViewModified(atsCv)) error("saved unmodified schematic required"))
      aiTsCheckPorts(atsCv atsEntry['rows] nil)
      atsPorts=aiTsPorts(atsCv)
      when(and(atsEntry['schematicPorts] !equal(atsPorts atsEntry['schematicPorts]))
        error("schematic interface changed since preview"))) when(atsCv dbClose(atsCv)))
    return(atsPorts)))

procedure(aiTsAbsent(ataEntry)
  when(ddGetObj(ataEntry['lib] ataEntry['cell] "symbol") error("symbol view already exists"))
  foreach(ataOpen dbGetOpenCellViews()
    when(aiCircuitCvMatches(ataOpen list(ataEntry['lib] ataEntry['cell] "symbol"))
      error("symbol already open, including unsaved view"))) t)

procedure(aiTemplateSymbolPreview(atpTask atpRequest atpTemplate atpDigest atpLib atpCell atpSourceLib atpSourcePath atpRows atpWidth atpHeight @optional atpOutline atpSourceCell atpStyle)
  prog((atpRecord atpResult atpEntry)
    unless(member(aiCircuitTasks[atpTask] list("foreground" "background"))
      return(aiCircuitError("task_not_found" "Existing confirmed circuit task required")))
    atpResult=errset(aiRcReserve(strcat("template-symbol-preview:" atpRequest)
      list(atpTask atpTemplate atpDigest atpLib atpCell atpSourceLib atpSourcePath atpRows atpWidth atpHeight atpOutline atpSourceCell atpStyle)) nil)
    unless(atpResult return(aiCircuitError("request_conflict_or_limit" "Preview request conflict/limit")))
    atpRecord=car(atpResult) when(atpRecord['reply] return(atpRecord['reply]))
    atpEntry=makeTable("aiTemplateSymbolPreview" nil)
    atpEntry['ref]=strcat("template-symbol-preview:" atpRequest)
    atpEntry['task]=atpTask atpEntry['mode]=aiCircuitTasks[atpTask]
    atpEntry['lib]=atpLib atpEntry['cell]=atpCell atpEntry['template]=atpTemplate atpEntry['digest]=atpDigest
    atpEntry['sourceLib]=atpSourceLib atpEntry['sourcePath]=atpSourcePath
    atpEntry['sourceCell]=atpSourceCell
    atpEntry['rows]=atpRows atpEntry['width]=atpWidth atpEntry['height]=atpHeight
    atpEntry['outline]=atpOutline atpEntry['style]=atpStyle
    atpResult=errset(progn(
      unless(and(aiCircuitTargetP(atpLib atpCell "symbol" "schematicSymbol")
        listp(atpRows) length(atpRows)>0 length(atpRows)<=128 numberp(atpWidth) numberp(atpHeight)
        atpWidth>=1 atpWidth<=256 atpHeight>=1 atpHeight<=256) error("invalid symbol plan"))
      foreach(atpRow atpRows
        unless(and(length(atpRow)==8 aiCircuitTextP(car(atpRow))
          member(cadr(atpRow) list("input" "output" "inputOutput"))
          dbGetNameNumBit(car(atpRow))==caddr(atpRow)) error("invalid terminal plan")))
      atpEntry['workPath]=aiTsPath(atpEntry)
      aiTsAbsent(atpEntry)
      atpEntry['schematicPorts]=aiTsSchematic(atpEntry)
      when(atpOutline aiTdPreflight(atpEntry))
      unless(and(ddGetObj("basic" "sympin" "symbolNN") isCallable('schCreateSymbolPin)
        isCallable('schCreateSymbolLabel) isCallable('schVIC)) error("symbol APIs/pin master unavailable"))
      aiTemplateSymbolPreviews[atpEntry['ref]]=atpEntry
      atpRecord['reply]=aiCircuitObject(list(list("ok" t) list("preview_ref" atpEntry['ref])
        list("preview_digest" atpDigest) list("template_ref" atpTemplate) list("task_ref" atpTask)
        list("target" list(atpLib atpCell "symbol")) list("presentation" atpEntry['mode])
        list("stage" "previewed") list("interface_check" "matched_template_schematic_ports")
        list("saved" 'jsonFalse))) t) t)
    unless(atpResult
      atpRecord['reply]=aiCircuitError("symbol_preview_failed"
        strcat("Target interface/path/availability check failed: " aiTsErrorText())))
    return(atpRecord['reply])))

procedure(aiTsReply(atrEntry)
  aiCircuitObject(list(list("ok" aiCircuitBool(!atrEntry['error]))
    list("symbol_ref" atrEntry['ref]) list("preview_ref" atrEntry['preview])
    list("preview_digest" atrEntry['digest]) list("template_ref" atrEntry['template])
    list("task_ref" atrEntry['task]) list("target" list(atrEntry['lib] atrEntry['cell] "symbol"))
    list("presentation" atrEntry['mode]) list("stage" atrEntry['stage]) list("saved" aiCircuitBool(atrEntry['saved]))
    list("view_created" aiCircuitBool(atrEntry['created])) list("interface_check" atrEntry['vic])
    list("style" if(atrEntry['style] atrEntry['style]
      if(atrEntry['outline] "template_outline" "rectangular_module")))
    list("bootstrap_cleaned" aiCircuitBool(atrEntry['bootstrapCleaned]))
    list("readback_verified" aiCircuitBool(atrEntry['verified])) list("error" atrEntry['error])
    list("qualification" "symbol_interface_and_geometry; visual_review_required"))))

procedure(aiTsValidate(atvEntry)
  prog((atvCv atvTarget atvWindow)
    aiTsPath(atvEntry) aiTsSchematic(atvEntry)
    atvTarget=list(atvEntry['lib] atvEntry['cell] "symbol") atvWindow=atvEntry['window]
    when(atvEntry['mode]=="foreground"
      unless(and(member(atvWindow hiGetWindowList('all)) aiCircuitCvMatches(geGetWindowCellView(atvWindow) atvTarget)
        geGetWindowCellView(atvWindow)==geGetEditCellView(atvWindow)) error("symbol window closed/rebound/EIP")))
    unwindProtect(progn(
      atvCv=dbOpenCellViewByType(car(atvTarget) cadr(atvTarget) "symbol" "" "r")
      unless(and(atvCv atvCv~>cellViewType=="schematicSymbol" !dbIsCellViewModified(atvCv)) error("symbol unavailable/modified"))
      aiTsCheckPorts(atvCv atvEntry['rows] t)
      unless(equal(aiTsSnapshot(atvCv) atvEntry['snapshot]) error("saved symbol geometry/provenance differs")))
      when(atvCv dbClose(atvCv)))
    return(t)))

procedure(aiTemplateSymbolCreate(atcPreview atcDigest atcRequest)
  prog((atcP atcRecord atcResult atcEntry atcCv atcWindow)
    atcP=aiTemplateSymbolPreviews[atcPreview]
    unless(and(atcP atcP['digest]==atcDigest)
      return(aiCircuitError("symbol_preview_not_found" "Preview missing or digest differs; preview exact target again")))
    atcResult=errset(aiRcReserve(strcat("template-symbol:" atcRequest) list(atcPreview atcDigest)) nil)
    unless(atcResult return(aiCircuitError("request_conflict_or_limit" "Create request conflict/limit")))
    atcRecord=car(atcResult) when(atcRecord['entry] return(aiTsReply(atcRecord['entry])))
    atcEntry=makeTable("aiTemplateSymbol" nil)
    foreach(atcKey list('task 'mode 'lib 'cell 'template 'digest 'sourceLib 'sourceCell 'sourcePath 'workPath 'libraryGuard 'rows 'width 'height 'schematicPorts 'outline 'style)
      atcEntry[atcKey]=atcP[atcKey])
    atcEntry['ref]=strcat("template-symbol:" atcRequest) atcEntry['preview]=atcPreview atcEntry['stage]="preflight"
    atcRecord['entry]=atcEntry aiTemplateSymbols[atcEntry['ref]]=atcEntry
    atcResult=errset(unwindProtect(progn(
      aiTsPath(atcEntry) aiTsSchematic(atcEntry) aiTsAbsent(atcEntry)
      atcEntry['stage]="create_view"
      if(atcEntry['mode]=="foreground" then
        atcWindow=aiCircuitNativeCall('deNewCellView
          list(atcEntry['lib] atcEntry['cell] "symbol" "schematicSymbol" nil))
        unless(atcWindow error("new symbol window failed"))
        aiCircuitRaiseWindow(atcWindow)
        atcEntry['window]=atcWindow
        atcCv=dbOpenCellViewByType(atcEntry['lib] atcEntry['cell] "symbol" "" "r")
        unless(and(atcCv geGetEditCellView(atcWindow)==atcCv geGetWindowCellView(atcWindow)==atcCv)
          error("new symbol window mismatch"))
      else atcCv=dbOpenCellViewByType(atcEntry['lib] atcEntry['cell] "symbol" "schematicSymbol" "a"))
      unless(and(atcCv atcCv~>cellViewType=="schematicSymbol") error("symbol open/type failed"))
      atcEntry['created]=t atcEntry['stage]="geometry"
      unless(and(!atcCv~>shapes !atcCv~>instances !atcCv~>terminals) error("new view is not empty"))
      if(atcEntry['outline] then aiTdBuild(atcCv atcEntry) else aiTsBuild(atcCv atcEntry))
      atcEntry['stage]="interface_check" atcEntry['vic]=aiTsVic(atcCv)
      atcEntry['stage]="save"
      unless(dbSave(atcCv) error("symbol save failed"))
      atcEntry['saved]=t atcEntry['snapshot]=aiTsSnapshot(atcCv)
      when(atcWindow aiCircuitNativeCall('hiZoomIn list(atcWindow list(
        list(xCoord(car(atcCv~>bBox))-0.5 yCoord(car(atcCv~>bBox))-0.5)
        list(xCoord(cadr(atcCv~>bBox))+0.5 yCoord(cadr(atcCv~>bBox))+0.5)))))
      t)
      when(atcCv
        ; Failed background builds retain their owned in-memory view for diagnosis.
        if(and(!atcEntry['saved] !atcWindow) then atcEntry['partialCv]=atcCv else dbClose(atcCv)))) t)
    when(atcResult
      atcEntry['stage]="saved_readback"
      atcResult=errset(aiTsValidate(atcEntry) t))
    if(atcResult then atcEntry['verified]=t atcEntry['stage]="symbol_saved"
      else atcEntry['error]=strcat("Failed at " atcEntry['stage] "; partial target retained if created; inspect CIW"))
    when(isCallable('sicoNativeOutput)
      aiCircuitNativeCall('sicoNativeOutput
        list(atcEntry['window] list(atcEntry['lib] atcEntry['cell] "symbol")
          atcEntry['created] atcEntry['verified])))
    return(aiTsReply(atcEntry))))

procedure(aiTemplateSymbolInspect(atiRef)
  prog((atiEntry)
    atiEntry=aiTemplateSymbols[atiRef]
    unless(atiEntry return(aiCircuitError("symbol_not_found" "Unknown symbol reference")))
    when(atiEntry['error] return(aiTsReply(atiEntry)))
    unless(car(errset(aiTsValidate(atiEntry) t))
      return(aiCircuitError("symbol_changed" "Symbol/schematic interface/library/window changed; inspect CIW" atiRef)))
    return(aiTsReply(atiEntry))))
