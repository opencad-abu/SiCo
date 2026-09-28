; Menus for the compiled independent CAD frontends; no source loaders.

procedure(SICO_toolsMenuItems(prefix)
  let((items spec)
    foreach(spec list(
      list("lsf" "LSF Load Monitor" "SICO_lsfMonitorStart()")
      list("drc" "DRC" "cadDisplayDrcForm()")
      list("lvs" "LVS" "cadDisplayLvsForm()")
      list("rce" "RCE" "cadDisplayRceForm()")
      list("dspf" "DSPF Analyzer..." "rceDspfAnalyzerChooseFile()")
      list("gds" "Stream GDS" "cadDisplayStreamGdsForm()")
      list("cdl" "Export CDL" "cadDisplayExportCdlForm()")
      list("lef" "LEF Generator" "cadDisplayLefForm()")
      list("tech" "Change Tech Library..." "cadChangeTechLibGui()")
      list("view" "Netlist to View..." "cadDisplayNl2ViewForm()")
      list("mts" "MTS Netlistor..." "mtsNetlistorStart()"))
      items=append1(items hiCreateMenuItem(
        ?name stringToSymbol(strcat(prefix "_" car(spec)))
        ?itemText cadr(spec) ?callback caddr(spec))))
    items))

procedure(SICO_toolsInstallMenu(window prefix)
  let((name menu index menus)
    name=stringToSymbol(strcat(prefix "_sicoTools"))
    menus=hiGetBannerMenus(window)
    index=0
    while(and(menus car(menus)!=name)
      menus=cdr(menus)
      index=index+1)
    when(menus hiDeleteBannerMenu(window index))
    menu=hiCreatePulldownMenu(name strcat("&" SICO_logo()) SICO_toolsMenuItems(prefix))
    hiInsertBannerMenu(window menu hiGetNumMenus(window))
    t))

procedure(SICO_toolsInstallEditor(args)
  SICO_toolsInstallMenu(args->window "sicoEditor"))

procedure(SICO_toolsInstallMenus()
  unless(or(SICO_environmentValue("SICO_RUNTIME_NO_MENU" list("CAD_RUNTIME_NO_MENU"))=="1"
            getShellEnvVar("VIRTUOSO_CONTEXT_BUILD")=="1")
    ciwMenuInit()
    SICO_toolsInstallMenu(hiGetCIWindow() "sicoCIW")
    foreach(viewType '("maskLayout" "maskLayoutXL" "maskLayoutEXL" "schematic" "schematicXL")
      unless(member('SICO_toolsInstallEditor deGetAppInfo(viewType)->userPostInstallTrigList)
        deRegUserTriggers(viewType nil nil 'SICO_toolsInstallEditor))))
  t)
