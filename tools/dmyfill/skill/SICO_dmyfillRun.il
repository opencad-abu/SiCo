; End-to-end dummy-fill orchestration for the source/compiled CAD flow.

unless(boundp('SICO_dmyfillIpcTable)
  SICO_dmyfillIpcTable=makeTable("SICO_dmyfillIpcTable" nil))

unless(boundp('SICO_dmyfillIpcByChild)
  SICO_dmyfillIpcByChild=makeTable("SICO_dmyfillIpcByChild" nil))

unless(boundp('SICO_dmyfillIpcSerial)
  SICO_dmyfillIpcSerial=0)

unless(boundp('SICO_dmyfillFlowLogRequest)
  SICO_dmyfillFlowLogRequest=nil)

unless(and(boundp('sicoDmyFillRunVersion)
           sicoDmyFillRunVersion=="20261102.sico.dmyfill.run.v17")

procedure(SICO_dmyfillRunRevision() "20261102.sico.dmyfill.run.v17")

procedure(SICO_dmyfillShellQuote(value)
  let((text out index character)
    text=if(value then sprintf(nil "%s" value) else "")
    out="'"
    for(index 1 strlen(text)
      character=substring(text index 1)
      if(character=="'" then
        out=strcat(out "'\"'\"'")
      else
        out=strcat(out character)))
    strcat(out "'")))

procedure(SICO_dmyfillQuote(value)
  let((text out index character)
    text=if(value then sprintf(nil "%s" value) else "")
    out="\""
    for(index 1 strlen(text)
      character=substring(text index 1)
      when(or(character=="\\" character=="\"")
        out=strcat(out "\\"))
      out=strcat(out character))
    strcat(out "\"")))

procedure(SICO_dmyfillLog(path text)
  let((port)
    port=outfile(path "a")
    when(port
      fprintf(port "%s\n" text)
      close(port))
    text))

procedure(SICO_dmyfillDrcRunRoot(cv)
  let((base)
    base=getShellEnvVar("DRC_DB_DIR")
    unless(and(stringp(base) strlen(base)>0)
      base=getWorkingDir())
    unless(and(stringp(base) strlen(base)>0)
      error("dummy fill requires DRC_DB_DIR or a working directory"))
    strcat(simplifyFilename(base t)
      "/" cv~>libName "." cv~>cellName)))

procedure(SICO_dmyfillUniqueRunDir(cv)
  let((root path base)
    root=SICO_dmyfillDrcRunRoot(cv)
    unless(isDir(root)
      unless(zerop(system(strcat("/bin/mkdir -p -m 700 -- "
               SICO_dmyfillShellQuote(root))))
        error("dummy fill cannot create DRC workflow directory: %L" root)))
    path=makeTempFileName(strcat(root "/dmyfillXXXX"))
    base=if(path car(last(parseString(path "/"))) "")
    when(and(stringp(path)
             rexMatchp("^dmyfill[A-Za-z0-9]+$" base)
             zerop(system(strcat("/bin/mkdir -m 700 -- "
               SICO_dmyfillShellQuote(path))))
             isDir(path))
      path)))

procedure(SICO_dmyfillWriteStreamout(path cv layerMap gds)
  let((port)
    port=outfile(path "w")
    unless(port error("cannot write stream-out command: %L" path))
    fprintf(port "library %s\n" SICO_dmyfillQuote(cv~>libName))
    fprintf(port "topCell %s\n" SICO_dmyfillQuote(cv~>cellName))
    fprintf(port "view %s\n" SICO_dmyfillQuote(cv~>viewName))
    fprintf(port "layerMap %s\n" SICO_dmyfillQuote(layerMap))
    fprintf(port "strmFile %s\n" SICO_dmyfillQuote(gds))
    fprintf(port "strmVersion \"5\"\n")
    fprintf(port "logFile \"strmOut.log\"\n")
    fprintf(port "outputDir \".\"\n")
    fprintf(port "runDir \".\"\n")
    fprintf(port "replaceBusBitChar \"true\"\n")
    close(port)
    path))

procedure(SICO_dmyfillExportGds(cv runDir layerMap
                                @optional objectMap propertyMap logPath)
  let((gds command status log)
    gds=strcat(runDir "/input.gds")
    log=strcat(runDir "/strmout.log")
    SICO_dmyfillWriteStreamout(strcat(runDir "/streamout.cmd")
      cv layerMap gds)
    command=strcat("cd " SICO_dmyfillShellQuote(runDir)
      " && strmout -templateFile streamout.cmd -replaceBusBitChar")
    when(and(objectMap stringp(objectMap) strlen(objectMap)>0)
      command=strcat(command " -objectMap "
        SICO_dmyfillShellQuote(objectMap)))
    when(and(propertyMap stringp(propertyMap) strlen(propertyMap)>0)
      command=strcat(command " -propMap "
        SICO_dmyfillShellQuote(propertyMap)))
    command=strcat(command " > strmout.log 2>&1")
    when(and(logPath stringp(logPath) strlen(logPath)>0)
      command=strcat(command "; cat strmout.log >> "
        SICO_dmyfillShellQuote(logPath)))
    status=system(command)
    unless(and(zerop(status) isFile(gds) greaterp(fileLength(gds) 0))
      error("GDS export failed for dummy fill; see %L" log))
    gds))

procedure(SICO_dmyfillPythonBase()
  let((python entry)
    python=SICO_installedPython()
    unless(python error("SiCo bundled Python is unavailable"))
    entry=SICO_installationPath(SICO_installationRoot()
      "tools/dmyfill/python/dmyfill")
    SICO_backendCommand("dmyfill" python entry)))

procedure(SICO_dmyfillResolveProfile(output)
  let((command log status)
    log=strcat(output ".log")
    command=strcat(SICO_dmyfillPythonBase()
      " resolve --output " SICO_dmyfillShellQuote(output)
      " > " SICO_dmyfillShellQuote(log) " 2>&1")
    status=system(command)
    status))

procedure(SICO_dmyfillPythonCommand(gds topCell layers runDir
                                    @optional region expand logPath)
  let((args)
    args=strcat(
      " run --layout " SICO_dmyfillShellQuote(gds)
      " --top-cell " SICO_dmyfillShellQuote(topCell)
      " --layers " SICO_dmyfillShellQuote(layers)
      " --run-dir " SICO_dmyfillShellQuote(runDir))
    when(region
      args=strcat(args sprintf(nil " --region %g %g %g %g"
        nth(0 region) nth(1 region) nth(2 region) nth(3 region))))
    when(expand
      args=strcat(args " --expand-region"))
    if(and(logPath stringp(logPath) strlen(logPath)>0) then
      strcat(SICO_dmyfillPythonBase() args
        " > " SICO_dmyfillShellQuote(logPath) " 2>&1")
      else
      strcat(SICO_dmyfillPythonBase() args))))

procedure(SICO_dmyfillIpcDataHandler(cid data)
  nil)

procedure(SICO_dmyfillIpcErrHandler(cid data)
  nil)

procedure(SICO_dmyfillEnsureFlowLog()
  let((path)
    unless(isCallable('GUI_flowLogOpen)
      path=SICO_installationPath(SICO_installationRoot()
        "tools/utility/skill/ui/UI_flowLog.il")
      unless(isFile(path)
        error("SiCo common flow logger source is unavailable: %L" path))
      unless(errset(load(path) t)
        error("SiCo common flow logger could not be loaded: %L" path)))
    unless(isCallable('GUI_flowLogOpen)
      error("SiCo common flow logger is unavailable"))
    t))

procedure(SICO_dmyfillFlowLogShow()
  when(and(SICO_dmyfillFlowLogRequest
           isCallable('GUI_flowLogShow))
    GUI_flowLogShow(SICO_dmyfillFlowLogRequest))
  t)

procedure(SICO_dmyfillFlowLogOpen(path)
  let((request)
    SICO_dmyfillEnsureFlowLog()
    when(and(SICO_dmyfillFlowLogRequest
             isCallable('GUI_flowLogClose))
      GUI_flowLogClose(SICO_dmyfillFlowLogRequest))
    request=GUI_flowLogOpen("MDF" path)
    when(request
      SICO_dmyfillFlowLogRequest=request
      GUI_flowLogShow(request)
      ; The common logger creates the text window asynchronously when the
      ; launch log has not been created yet; raise it once after creation.
      hiRegTimer("SICO_dmyfillFlowLogShow()" 1))
    request))

; Compatibility aliases for callers that used the old private log viewer.
procedure(SICO_dmyfillLogViewOpen(path)
  SICO_dmyfillFlowLogOpen(path))

procedure(SICO_dmyfillLogViewTick()
  SICO_dmyfillFlowLogShow())

procedure(SICO_dmyfillIpcPostFunc(cid exitStatus)
  let((id meta)
    id=SICO_dmyfillIpcByChild[cid]
    when(id
      SICO_dmyfillIpcByChild[cid]=nil
      meta=SICO_dmyfillIpcTable[id]
      when(not(zerop(exitStatus))
        SICO_dmyfillLog(nth(3 meta)
          sprintf(nil "[MDF] backend exit status %L" exitStatus)))
      hiEnqueueCmd(sprintf(nil "SICO_dmyfillFinishQueued(%d)" id)))
    t))

procedure(SICO_dmyfillFinishQueued(id)
  let((meta cv runDir plan launchLog form lib cell view result message)
    meta=SICO_dmyfillIpcTable[id]
    SICO_dmyfillIpcTable[id]=nil
    SICO_dmyfillRunning=nil
    when(meta
      cv=nth(0 meta)
      runDir=nth(1 meta)
      plan=nth(2 meta)
      launchLog=nth(3 meta)
      form=nth(4 meta)
      lib=nth(5 meta)
      cell=nth(6 meta)
      view=nth(7 meta)
      cond(
        (not(isFile(plan))
          message=sprintf(nil "Dummy fill failed: plan is missing (%s)." plan))
        (not(and(cv dbIsId(cv)))
          message="Dummy fill finished, but the target cellview is closed; nothing was applied.")
        (t
          SICO_dmyfillLog(launchLog
            sprintf(nil "[MDF] applying plan to %s/%s/%s ..." lib cell view))
          result=errset(SICO_dmyfillApplyPlanToCv(cv plan) t)
          if(result then
            message=sprintf(nil "Applied %L dummy-fill shape(s) to %s/%s/%s."
              car(result) lib cell view)
          else
            message=sprintf(nil "Dummy-fill apply failed: %L" errset.errset))))
      SICO_dmyfillLog(launchLog strcat("[MDF] " message))
      printf("[MDF] %s\n" message)
      when(and(form hiIsForm(form))
        SICO_dmyfillStatus(form message)))
    t))

procedure(SICO_dmyfillStart(layers scope @optional window form)
  prog((cv scopeResult box region runDir gds launchLog command cid id
        layerMap objectMap propertyMap)
    cv=SICO_dmyfillCv(window)
    unless(cv
      warn("Dummy fill requires a writable maskLayout cellview.\n")
      return(nil))
    scope=case(scope
      (("cell" "current cell") "cell")
      (("window" "current window") "window")
      (("selected" "selected box") "selected")
      (t error("unknown dummy-fill scope: %L" scope)))
    scopeResult=SICO_dmyfillScopeBox(scope window)
    box=cadr(scopeResult)
    unless(SICO_dmyfillBoxP(box)
      error("dummy-fill scope has no usable region"))
    region=list(
      car(car(box)) cadr(car(box))
      car(cadr(box)) cadr(cadr(box)))
    layerMap=getShellEnvVar("TECH_LAYER_MAP")
    unless(and(layerMap isFile(layerMap))
      error("dummy fill requires TECH_LAYER_MAP"))
    objectMap=getShellEnvVar("GDS_OBJECT_MAP")
    propertyMap=getShellEnvVar("GDS_PROPERTY_MAP")
    runDir=SICO_dmyfillUniqueRunDir(cv)
    unless(runDir error("cannot create a dummy-fill run directory"))
    launchLog=strcat(runDir "/dmyfill.launch.log")
    SICO_dmyfillRunning=t
    SICO_dmyfillLog(launchLog "Dummy Fill")
    SICO_dmyfillLog(launchLog sprintf(nil "Target: %s/%s/%s"
      cv~>libName cv~>cellName cv~>viewName))
    SICO_dmyfillLog(launchLog sprintf(nil "Layers: %s" layers))
    SICO_dmyfillLog(launchLog sprintf(nil "Scope : %s region: %L" scope region))
    printf("[MDF] Target: %s/%s/%s\n"
      cv~>libName cv~>cellName cv~>viewName)
    printf("[MDF] Layers: %s\n" layers)
    printf("[MDF] Scope: %s %L\n" scope region)
    printf("[MDF] Log: %s\n" launchLog)
    SICO_dmyfillLog(launchLog "[MDF] exporting GDS ...")
    printf("[MDF] Exporting GDS ...\n")
    gds=errset(SICO_dmyfillExportGds(cv runDir layerMap
      objectMap propertyMap launchLog) t)
    unless(gds
      SICO_dmyfillLog(launchLog "[MDF] GDS export failed")
      warn("Dummy fill GDS export failed; see the Dummy Fill log.\n")
      SICO_dmyfillRunning=nil
      return(nil))
    SICO_dmyfillLog(launchLog sprintf(nil "[MDF] GDS: %s" car(gds)))
    printf("[MDF] GDS ready: %s\n" car(gds))
    command=SICO_dmyfillPythonCommand(car(gds) cv~>cellName layers runDir
      region t)
    command=strcat(command " >> " SICO_dmyfillShellQuote(launchLog)
      " 2>&1")
    SICO_dmyfillLog(launchLog "[MDF] starting Calibre backend ...")
    printf("[MDF] Starting Calibre backend ...\n")
    cid=ipcBeginProcess(command "" 'SICO_dmyfillIpcDataHandler
      'SICO_dmyfillIpcErrHandler 'SICO_dmyfillIpcPostFunc)
    unless(cid
      SICO_dmyfillLog(launchLog "[MDF] failed to start IPC process")
      warn("Dummy fill could not start the Calibre backend.\n")
      SICO_dmyfillRunning=nil
      return(nil))
    SICO_dmyfillIpcSerial=SICO_dmyfillIpcSerial+1
    id=SICO_dmyfillIpcSerial
    SICO_dmyfillIpcTable[id]=list(cv runDir strcat(runDir "/fill.plan")
      launchLog form cv~>libName cv~>cellName cv~>viewName)
    SICO_dmyfillIpcByChild[cid]=id
    errset(ipcActivateBatch(cid) t)
    printf("[MDF] Calibre started; log: %s\n" launchLog)
    unless(car(errset(SICO_dmyfillFlowLogOpen(launchLog) nil))
      warn("SiCo common flow logger is unavailable; see %s\n" launchLog))
    printf("[MDF] MDF log window: %s\n" launchLog)
    return(t)))

procedure(SICO_dmyfillRunFromCurrent(layers scope @optional window)
  let((cv scopeResult box region runDir gds plan command status directory
       layerMap objectMap propertyMap)
    cv=SICO_dmyfillCv(window)
    unless(cv error("dummy fill requires a writable maskLayout cellview"))
    scope=case(scope
      (("cell" "current cell") "cell")
      (("window" "current window") "window")
      (("selected" "selected box") "selected")
      (t error("unknown dummy-fill scope: %L" scope)))
    scopeResult=SICO_dmyfillScopeBox(scope window)
    box=cadr(scopeResult)
    unless(SICO_dmyfillBoxP(box)
      error("dummy-fill scope has no usable region"))
    region=list(
      car(car(box)) cadr(car(box))
      car(cadr(box)) cadr(cadr(box)))
    layerMap=getShellEnvVar("TECH_LAYER_MAP")
    unless(and(layerMap isFile(layerMap))
      error("dummy fill requires TECH_LAYER_MAP"))
    objectMap=getShellEnvVar("GDS_OBJECT_MAP")
    propertyMap=getShellEnvVar("GDS_PROPERTY_MAP")
    runDir=SICO_dmyfillUniqueRunDir(cv)
    unless(runDir error("cannot create a dummy-fill run directory"))
    gds=SICO_dmyfillExportGds(cv runDir layerMap objectMap propertyMap)
    command=SICO_dmyfillPythonCommand(gds cv~>cellName layers runDir
      region t strcat(runDir "/dmyfill.log"))
    status=system(command)
    plan=strcat(runDir "/fill.plan")
    unless(and(zerop(status) isFile(plan))
      error("dummy-fill Calibre/plan stage failed; see %L"
        strcat(runDir "/dmyfill.log")))
    directory=SICO_dmyfillApplyPlanToCv(cv plan)
    list(runDir plan directory)))

sicoDmyFillRunVersion="20261102.sico.dmyfill.run.v17"
)
