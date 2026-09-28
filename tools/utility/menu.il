let((commonSkill)
  commonSkill=strcat("/" buildString(reverse(cdr(reverse(
    parseString(simplifyFilename(get_filename(piport) t) "/")))) "/")
    "/../common/skill/")
  load(strcat(commonSkill "SICO_environment.il"))
  load(strcat(commonSkill "SICO_installation.il")))

;------------------------------------------------------------------------------
; TOML-driven SiCo menu registration for CIW, Layout Editor, and Schematic.
; Load this file from .cdsinit after setting the LOGO/COMPANY environment if needed.
;------------------------------------------------------------------------------

unless(and(boundp('witMenuLoaderVersion)
             witMenuLoaderVersion=="20260922.sico.menu.v4"
             isCallable('witMenuLoadRelative)
             isCallable('witMenuInstallAgent)
             isCallable('witMenuReload))

procedure(witMenuShellQuote(value)
  let((s out i ch)
    s=if(value then sprintf(nil "%s" value) else "")
    out="'"
    for(i 1 strlen(s)
      ch=substring(s i 1)
      if(ch == "'" then
        out=strcat(out "'\"'\"'")
      else
        out=strcat(out ch)
      )
    )
    strcat(out "'")
  )
)

procedure(witMenuPythonLibraryPath()
  or(SICO_envValue("SICO_SYSTEM_LD_LIBRARY_PATH") "")
)

procedure(witMenuInstalledDataPath()
  when(and(boundp('witMenuRoot)
             witMenuRoot
             strlen(witMenuRoot)>0)
    simplifyFilename(strcat(witMenuRoot "/menu.generated.il") t)
  )
)

procedure(witMenuCacheDataPath()
  let((cacheRoot xdg home)
    cacheRoot=getShellEnvVar("SICO_MENU_CACHE_DIR")
    unless(and(cacheRoot strlen(cacheRoot)>0)
      xdg=getShellEnvVar("XDG_CACHE_HOME")
      when(and(xdg strlen(xdg)>0)
        cacheRoot=strcat(xdg "/sico")
      )
    )
    unless(and(cacheRoot strlen(cacheRoot)>0)
      home=getShellEnvVar("HOME")
      when(and(home strlen(home)>0)
        cacheRoot=strcat(home "/.cache/sico")
      )
    )
    when(and(cacheRoot strlen(cacheRoot)>0)
      simplifyFilename(strcat(cacheRoot "/menu.generated.il") t)
    )
  )
)

procedure(witMenuTryLoadData(path)
  let((result detail)
    when(and(path isFile(path))
      result=errset(load(path) nil)
      if(result then
        t
      else
        detail=errset.errset
        warn("%s menu data could not be loaded from %s: %L\n"
              witMenuLogo() path detail)
        nil
      )
    )
  )
)

procedure(witMenuToolsRoot()
  SICO_installationPath(SICO_installationRoot() "tools"))

procedure(witMenuLoadRelative(relativePath)
  let((root path)
    root=witMenuToolsRoot()
    when(root
      path=simplifyFilename(strcat(root "/" relativePath) t)
    )
    if(and(path isFile(path)) then
      load(path)
    else
      warn("%s menu cannot find CAD tool: %L\n" witMenuLogo() path)
      nil
    )
  )
)

procedure(witMenuGenerateData()
  let((python cadPython pythonRoot helper config output cmd status)
    cadPython=SICO_envValue("SICO_PYTHON")
    pythonRoot=SICO_envValue("SICO_PYTHON_ROOT")
    python=cond(
      (and(cadPython strlen(cadPython)>0) cadPython)
      (and(pythonRoot strlen(pythonRoot)>0)
        simplifyFilename(strcat(pythonRoot "/bin/python3") t))
      (t "python3"))
    when(and(boundp('witMenuRoot)
               witMenuRoot
               strlen(witMenuRoot)>0)
      helper=strcat(witMenuRoot "/menu_to_skill.py")
      config=strcat(witMenuRoot "/menu.toml")
    )
    output=witMenuCacheDataPath()
    when(and(helper config output isFile(helper) isFile(config))
      cmd=strcat("env -u PYTHONHOME -u PYTHONPATH LD_LIBRARY_PATH="
                  witMenuShellQuote(witMenuPythonLibraryPath())
                  " "
                  witMenuShellQuote(python) " "
                  witMenuShellQuote(helper) " "
                  witMenuShellQuote(config) " "
                  witMenuShellQuote(output))
      status=system(cmd)
    )
    if(and(status status==0 output isFile(output)) then
      witMenuTryLoadData(output)
    else
      warn("%s menu data generation failed (status=%L, output=%L).\n"
            witMenuLogo() status output)
      nil
    )
  )
)

procedure(witMenuLoadData()
  let((installed cache)
    installed=witMenuInstalledDataPath()
    if(witMenuTryLoadData(installed) then
      t
    else
      cache=witMenuCacheDataPath()
      if(witMenuTryLoadData(cache) then
        t
      else
        warn("%s installed menu data is unavailable; generating user cache: %L\n"
              witMenuLogo() installed)
        witMenuGenerateData()
      )
    )
  )
)

procedure(witMenuLogo()
  let((logo company logoEnv defaultLogo)
    logoEnv=if(boundp('witMenuLogoEnv) witMenuLogoEnv "LOGO")
    defaultLogo=if(boundp('witMenuDefaultLogo) witMenuDefaultLogo "SiCo")
    logo=getShellEnvVar(logoEnv)
    company=getShellEnvVar("COMPANY")
    unless(and(logo strlen(logo)>0)
      logo=nil)
    when(and(logo upperCase(logo)=="OCAD")
      logo=nil)
    unless(and(company strlen(company)>0)
      company=nil)
    when(and(company upperCase(company)=="OCAD")
      company=nil)
    or(logo company defaultLogo "SiCo")
  )
)

procedure(witMenuSymbol(prefix title)
  stringToSymbol(strcat(prefix "_" buildString(parseString(title " \t&/\\.:;") "_")))
)

procedure(witMenuItemSymbol(prefix title name index)
  let((base)
    base=if(name && strlen(name)>0 then name else sprintf(nil "%d" index))
    stringToSymbol(strcat(prefix "_"
                            buildString(parseString(title " \t&/\\.:;") "_")
                            "_"
                            buildString(parseString(base " \t&/\\.:;") "_")))
  )
)

procedure(witMenuBuildItems(prefix title itemSpecs)
  let((items index item name text callback enabled)
    items=nil
    index=0
    foreach(item itemSpecs
      index=index+1
      name=car(item)
      text=cadr(item)
      callback=caddr(item)
      enabled=cadddr(item)
      when(enabled
        items=append1(items
          hiCreateMenuItem(
            ?name witMenuItemSymbol(prefix title name index)
            ?itemText text
            ?callback callback
          )
        )
      )
    )
    items
  )
)

procedure(witMenuBuildMenu(prefix section)
  let((enabled title itemSpecs menuSym)
    enabled=car(section)
    title=cadr(section)
    itemSpecs=cadddr(section)
    when(enabled
      menuSym=witMenuSymbol(prefix title)
      hiCreatePulldownMenu(
        menuSym
        strcat("&" witMenuLogo())
        witMenuBuildItems(prefix title itemSpecs)
      )
    )
  )
)

procedure(witMenuBannerIndex(window menuSym)
  let((menus index found)
    menus=hiGetBannerMenus(window)
    index=0
    found=nil
    while(menus && !found
      when(car(menus) == menuSym
        found=index
      )
      menus=cdr(menus)
      index=index+1
    )
    found
  )
)

procedure(witMenuInstallOnWindow(window prefix section)
  let((menu menuSym index)
    menuSym=witMenuSymbol(prefix cadr(section))
    menu=witMenuBuildMenu(prefix section)
    when(menu
      index=witMenuBannerIndex(window menuSym)
      when(index != nil
        hiDeleteBannerMenu(window index)
      )
      hiInsertBannerMenu(window menu hiGetNumMenus(window))
    )
  )
)

procedure(witMenuInstallCIW()
  ciwMenuInit()
  witMenuInstallOnWindow(hiGetCIWindow() "ciw" witMenuCIW)
)

procedure(witMenuInstallLayout(args)
  witMenuInstallOnWindow(args->window "layout" witMenuLayout)
)

procedure(witMenuInstallSchematic(args)
  witMenuInstallOnWindow(args->window "schematic" witMenuSchematic)
)

procedure(witMenuTriggerRegisteredP(viewType trigger)
  let((appInfo)
    appInfo=deGetAppInfo(viewType)
    and(appInfo member(trigger appInfo->userPostInstallTrigList))
  )
)

procedure(witMenuRegisterTriggers()
  let((section viewTypes)
    section=witMenuLayout
    viewTypes=caddr(section)
    foreach(viewType viewTypes
      unless(witMenuTriggerRegisteredP(viewType 'witMenuInstallLayout)
        deRegUserTriggers(viewType nil nil 'witMenuInstallLayout)
      )
    )
    section=witMenuSchematic
    viewTypes=caddr(section)
    foreach(viewType viewTypes
      unless(witMenuTriggerRegisteredP(viewType 'witMenuInstallSchematic)
        deRegUserTriggers(viewType nil nil 'witMenuInstallSchematic)
      )
    )
  )
)

procedure(witMenuInstallAgent()
  when(isFile(strcat(witMenuToolsRoot() "/sico/skill++/SICO.ils"))
    errset(witMenuLoadRelative("sico/skill++/SICO.ils") t))
  witMenuAgentInitVersion="20260909.menu.agent.v1"
  t)

procedure(witMenuReload()
  when(witMenuLoadData()
    witMenuInstallCIW()
    witMenuRegisterTriggers()
    witMenuInstallAgent()
    t
  )
)

procedure(witMenuRegenerate()
  when(witMenuGenerateData()
    witMenuInstallCIW()
    witMenuRegisterTriggers()
    witMenuInstallAgent()
    t
  )
)

witMenuLoaderVersion="20260922.sico.menu.v4"
)

witMenuRoot=SICO_installationPath(SICO_installationRoot() "tools/utility")

if(witMenuRoot then
  unless(and(boundp('witMenuInitVersion)
               witMenuInitVersion=="20260922.sico.menu.v4"
               boundp('witMenuAgentInitVersion)
               witMenuAgentInitVersion=="20260909.menu.agent.v1"
               boundp('witMenuInitRoot)
               witMenuInitRoot==witMenuRoot)
    when(witMenuReload()
      witMenuInitRoot=witMenuRoot
      witMenuInitVersion="20260922.sico.menu.v4"
    )
  )
else
  warn("%s menu root is not configured. Set SICO_HOME first.\n" witMenuLogo())
)
