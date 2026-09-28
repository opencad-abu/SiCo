;------------------------------------------------------------------------------
; SiCo Netlist to View frontend for cdsTextTo5x.
;------------------------------------------------------------------------------

unless(and(boundp('sicoNl2ViewVersion)
             sicoNl2ViewVersion=="20260818.nl2view.v6"
             isCallable('cadDisplayNl2ViewForm))

unless(and(isCallable('SICO_tomlRevision)
             SICO_tomlRevision()=="20260922.sico.temp.v6")
  cond(
    (isCallable('cadLoadRelative)
      cadLoadRelative("common/skill/SICO_toml.il"))
    (isCallable('witMenuLoadRelative)
      witMenuLoadRelative("common/skill/SICO_toml.il"))
    (t
      warn("Netlist to View requires SICO_HOME or the %s menu loader.\n"
           cond(
             (isCallable('witMenuLogo) witMenuLogo())
             (t "SiCo"))))
  )
)

procedure(cadNl2ViewBrand()
  cond(
    (isCallable('SICO_logo) SICO_logo())
    (isCallable('witMenuLogo) witMenuLogo())
    (t "SiCo")
  )
)

unless(boundp('cadNl2ViewIpcTable)
  cadNl2ViewIpcTable=makeTable("cadNl2ViewIpcTable" nil)
)

procedure(cadNl2ViewExitCleanup()
  foreach(cid cadNl2ViewIpcTable
    when(and(cid ipcIsAliveProcess(cid))
      if(isCallable('ipcSignalProcess) then
        errset(ipcSignalProcess(cid 'TERM) nil)
      else
        errset(ipcKillProcess(cid) nil)))
    cadNl2ViewIpcTable[cid]=nil
  )
  t
)

procedure(cadNl2ViewInitializeRuntime()
  unless(and(boundp('cadNl2ViewExitCleanupRegistered)
               cadNl2ViewExitCleanupRegistered)
    when(isCallable('regExitBefore)
      regExitBefore('cadNl2ViewExitCleanup)
    )
    cadNl2ViewExitCleanupRegistered=t
  )
  t
)

unless(getShellEnvVar("VIRTUOSO_CONTEXT_BUILD")=="1"
  cadNl2ViewInitializeRuntime()
)

procedure(cadNl2ViewDefaultView(formatName)
  case(formatName
    ("Spectre" "spectreText")
    ("HSPICE" "hspiceText")
    ("SPICE" "spiceText")
    ("DSPF" "dspfText")
    (t "spectreText")
  )
)

procedure(cadNl2ViewFormatArgument(formatName)
  case(formatName
    ("Spectre" "spectre")
    ("HSPICE" "hspice")
    ("SPICE" "spice")
    ("DSPF" "dspf")
    (t nil)
  )
)

procedure(cadNl2ViewSetStatus(form text)
  when(and(form form~>nl2viewStatus)
    form~>nl2viewStatus~>value=text
  )
  text
)

procedure(cadNl2ViewFormatCB(form)
  when(and(form form~>nl2viewFormat form~>nl2viewView)
    form~>nl2viewView~>value=
      cadNl2ViewDefaultView(form~>nl2viewFormat~>value)
  )
  t
)

procedure(cadNl2ViewSourceCB(form)
  let((cellName)
    when(and(form form~>nl2viewSource form~>nl2viewCell)
      cellName=SICO_fileStem(form~>nl2viewSource~>value)
      when(and(cellName strlen(cellName)>0)
        form~>nl2viewCell~>value=cellName
      )
    )
    t
  )
)

procedure(cadNl2ViewToolsRoot()
  or(when(isCallable('witMenuToolsRoot) witMenuToolsRoot())
     when(isCallable('SICO_toolsRoot) SICO_toolsRoot()))
)

procedure(cadNl2ViewNonEmpty(value)
  when(and(stringp(value) strlen(value)>0) value)
)

procedure(cadNl2ViewSessionLibraryPath(libName)
  let((libObj protected path)
    libObj=when(and(libName strlen(libName)>0) ddGetObj(libName))
    when(libObj
      protected=errset(ddGetObjWritePath(libObj) nil)
      path=when(and(protected car(protected)) car(protected))
      unless(path
        protected=errset(ddGetObjReadPath(libObj) nil)
        path=when(and(protected car(protected)) car(protected))
      )
    )
    when(path simplifyFilename(path t))
  )
)

procedure(cadNl2ViewCommand(form logFile)
  let((root entry source formatName libName cellName viewName cdsLib mode
        environment libraryPath)
    root=cadNl2ViewToolsRoot()
    entry=when(root simplifyFilename(strcat(root "/../bin/nl2view") t))
    source=SICO_absolutePath(form~>nl2viewSource~>value)
    formatName=cadNl2ViewFormatArgument(form~>nl2viewFormat~>value)
    libName=or(form~>nl2viewLibrary~>value "")
    cellName=or(form~>nl2viewCell~>value "")
    viewName=or(form~>nl2viewView~>value "")
    cdsLib=SICO_absolutePath(form~>nl2viewCdsLib~>value)
    libraryPath=cadNl2ViewSessionLibraryPath(libName)
    mode=if(form~>nl2viewCopy~>value then "--copy" else "--link")
    environment=SICO_tempEnvironment()
    when(and(environment entry isExecutable(entry) formatName
             strlen(source)>0 isFile(source)
             strlen(libName)>0 libraryPath
             strlen(cellName)>0 strlen(viewName)>0
             strlen(cdsLib)>0 isFile(cdsLib))
      strcat("exec env -u PYTHONHOME -u PYTHONPATH "
             "-u CDS_MPS_SESSION -u CDS_MPS_HOST -u CDS_MPS_PORT " environment
             "NL2VIEW_ORIG_LD_LIBRARY_PATH="
             SICO_shellQuote(or(getShellEnvVar("LD_LIBRARY_PATH") "")) " "
             "SICO_SYSTEM_LD_LIBRARY_PATH="
             SICO_shellQuote(SICO_pythonLibraryPath()) " "
             "LD_LIBRARY_PATH=" SICO_shellQuote(SICO_pythonLibraryPath()) " "
             SICO_shellQuote(entry)
             " --format " SICO_shellQuote(formatName)
             " --library " SICO_shellQuote(libName)
             " --cell " SICO_shellQuote(cellName)
             " --view " SICO_shellQuote(viewName)
             " --cdslib " SICO_shellQuote(cdsLib)
             " --expected-library-path " SICO_shellQuote(libraryPath)
             " --log " SICO_shellQuote(logFile)
             " " mode " " SICO_shellQuote(source))
    )
  )
)

procedure(cadNl2ViewDataHandler(cid data)
  when(and(data strlen(data)>0)
    info("<INFO> nl2view: %s" data)
  )
)

procedure(cadNl2ViewErrorHandler(cid data)
  when(and(data strlen(data)>0)
    warn("<WARN> nl2view: %s" data)
  )
)

procedure(cadNl2ViewPostFunc(cid exitStatus)
  let((meta form target logFile targetFields viewObj)
    meta=cadNl2ViewIpcTable[cid]
    cadNl2ViewIpcTable[cid]=nil
    form=when(meta car(meta))
    target=when(meta cadr(meta))
    logFile=when(meta caddr(meta))
    targetFields=when(meta nth(3 meta))
    when(and(form boundp('cadNl2ViewForm) cadNl2ViewForm==form)
      form~>nl2viewCid=nil
      hiSetFormButtonEnabled(form 'Apply t)
      if(exitStatus==0 then
        when(isCallable('ddUpdateLibList) ddUpdateLibList())
        viewObj=when(targetFields
          ddGetObj(car(targetFields) cadr(targetFields) caddr(targetFields)))
        if(viewObj then
          cadNl2ViewSetStatus(form strcat("Created " target))
          info("<INFO> Netlist view created: %s\n" target)
        else
          cadNl2ViewSetStatus(form
            strcat("Import did not create " target "; log: " logFile))
          warn("<WARN> nl2view exited successfully but did not create %s; log: %s\n"
               target logFile)
        )
      else
        cadNl2ViewSetStatus(form
          sprintf(nil "Import failed (exit %L); log: %s" exitStatus logFile))
        warn("<WARN> Netlist view import failed (exit %L); log: %s\n"
             exitStatus logFile)
      )
    )
    and(exitStatus==0 viewObj)
  )
)

procedure(cadNl2ViewApplyCB(form)
  let((tempDir logFile ipcLogFile command cid target targetFields)
    tempDir=SICO_tempDir()
    logFile=when(tempDir simplifyFilename(strcat(tempDir "/nl2view.log") t))
    ipcLogFile=when(tempDir
      simplifyFilename(strcat(tempDir "/nl2view.ipc.log") t))
    command=when(logFile cadNl2ViewCommand(form logFile))
    unless(command
      cadNl2ViewSetStatus(form
        "Choose an existing netlist, library, cell, view, and cds.lib")
    )
    when(command
      target=strcat(form~>nl2viewLibrary~>value "/"
                    form~>nl2viewCell~>value "/" form~>nl2viewView~>value)
      targetFields=list(form~>nl2viewLibrary~>value
                        form~>nl2viewCell~>value form~>nl2viewView~>value)
      cadNl2ViewSetStatus(form strcat("Importing " target "..."))
      hiSetFormButtonEnabled(form 'Apply nil)
      cid=ipcBeginProcess(command "" 'cadNl2ViewDataHandler
                          'cadNl2ViewErrorHandler 'cadNl2ViewPostFunc ipcLogFile)
      if(cid then
        form~>nl2viewCid=cid
        cadNl2ViewIpcTable[cid]=list(form target logFile targetFields)
        ipcActivateBatch(cid)
      else
        hiSetFormButtonEnabled(form 'Apply t)
        cadNl2ViewSetStatus(form "Could not start nl2view")
      )
    )
    cid
  )
)

procedure(cadNl2ViewCloseCB(form)
  let((cid)
    cid=when(form form~>nl2viewCid)
    when(cid
      cadNl2ViewIpcTable[cid]=nil
    )
    when(and(cid ipcIsAliveProcess(cid))
      if(isCallable('ipcSignalProcess) then
        ipcSignalProcess(cid 'TERM)
      else
        ipcKillProcess(cid))
    )
    when(form
      form~>nl2viewCid=nil
      hiSetFormButtonEnabled(form 'Apply t)
      when(cid cadNl2ViewSetStatus(form "Import canceled")))
  )
  when(form
    hiFormDone(form)
  )
  t
)

procedure(cadNl2ViewCreateForm()
  let((cv defaultLib defaultCell source formatField library cell view cdsLib
        copySource status destinationLayout mainLayout form cdsLibDefault
        updatedLib forcedLib)
    cv=when(isCallable('geGetEditCellView) geGetEditCellView())
    defaultLib=if(cv then cv~>libName else "")
    defaultCell=if(cv then cv~>cellName else "")
    updatedLib=when(isCallable('ddGetUpdatedLib) ddGetUpdatedLib())
    forcedLib=when(isCallable('ddGetForcedLib) ddGetForcedLib())
    cdsLibDefault=or(cadNl2ViewNonEmpty(updatedLib)
                     cadNl2ViewNonEmpty(forcedLib)
                     SICO_envValue("CDS_LIB")
                     simplifyFilename(strcat(getWorkingDir() "/cds.lib") t))
    source=hiCreateFileSelectorField(
      ?name 'nl2viewSource ?prompt "Netlist File:" ?mode 'existingFile
      ?defValue "" ?editable t
      ?callback "cadNl2ViewSourceCB(hiGetCurrentForm())")
    formatField=hiCreateCyclicField(
      ?name 'nl2viewFormat ?prompt "Format:"
      ?choices list("Spectre" "HSPICE" "SPICE" "DSPF")
      ?defValue "Spectre"
      ?callback "cadNl2ViewFormatCB(hiGetCurrentForm())")
    library=ddHiCreateLibraryComboField(
      ?name 'nl2viewLibrary ?prompt "Library:" ?value defaultLib ?editable t)
    cell=ddHiCreateCellComboField(
      ?name 'nl2viewCell ?prompt "Cell:" ?value defaultCell ?editable t)
    view=hiCreateStringField(
      ?name 'nl2viewView ?prompt "View:" ?defValue "spectreText")
    destinationLayout=hiCreateHorizontalBoxLayout(
      'cadNl2ViewDestinationLayout ?frame "Destination"
      ?spacing 8 ?items list(library cell view))
    cdsLib=hiCreateFileSelectorField(
      ?name 'nl2viewCdsLib ?prompt "cds.lib:" ?mode 'existingFile
      ?defValue cdsLibDefault ?editable t)
    copySource=hiCreateBooleanButton(
      ?name 'nl2viewCopy ?buttonText "Copy source into view" ?defValue t)
    status=hiCreateStringField(
      ?name 'nl2viewStatus ?prompt "Status:" ?defValue "Ready" ?editable nil)
    mainLayout=hiCreateVerticalBoxLayout(
      'cadNl2ViewMainLayout ?margins list(10 10 10 10) ?spacing 10
      ?items list(source formatField destinationLayout cdsLib copySource status))
    form=hiCreateLayoutForm(
      'cadNl2ViewForm strcat(cadNl2ViewBrand() "::Netlist to View") mainLayout
      ?dialogStyle 'modeless ?sizePolicy 'expanding ?initialSize list(760 300)
      ?buttonLayout list('Empty
        list('Apply 'cadNl2ViewApplyCB)
        list('Close 'cadNl2ViewCloseCB)))
    when(form
      form~>nl2viewCid=nil
      hiInstantiateForm(form)
      ddHiLinkFields(form~>nl2viewLibrary form~>nl2viewCell)
      hiSetFieldMinSize(form 'nl2viewSource ?widgetWidth 520)
      hiSetFieldMinSize(form 'nl2viewCdsLib ?widgetWidth 520)
      hiSetFieldMinSize(form 'nl2viewStatus ?widgetWidth 520)
    )
    form
  )
)

procedure(cadDisplayNl2ViewForm()
  unless(and(boundp('cadNl2ViewForm) cadNl2ViewForm)
    cadNl2ViewForm=cadNl2ViewCreateForm()
  )
  when(cadNl2ViewForm hiDisplayForm(cadNl2ViewForm))
  cadNl2ViewForm
)

procedure(cadNl2ViewRevision()
  sicoNl2ViewVersion
)

sicoNl2ViewVersion="20260818.nl2view.v6"
)
