/* Native editor context menus. Keep site menus and capture before popup dispatch. */

unless(and(boundp('sicoRmbVersion)
           sicoRmbVersion=="20260921.sico.rmb.v2")

unless(boundp('sicoLayoutMenus) sicoLayoutMenus=makeTable("sicoLayoutMenus" nil))

unless(boundp('sicoAskIconCache) sicoAskIconCache=nil)
unless(boundp('sicoAskIconCachePath) sicoAskIconCachePath="")
unless(boundp('sicoAskIconWarnedPath) sicoAskIconWarnedPath=nil)

procedure(sicoAskIcon()
  prog((root path icon)
    unless(isCallable('hiLoadIconFile)
      return(nil))
    root=when(boundp('sicoRoot) sicoRoot)
    unless(root return(nil))
    path=SICO_iconPath(SICO_installationRoot(strcat(root "/../..")) "brand" "ai-small.png")
    when(and(sicoAskIconCache
             sicoAskIconCachePath==path
             isCallable('hiIsIcon)
             hiIsIcon(sicoAskIconCache))
      return(sicoAskIconCache))
    icon=when(isFile(path) hiLoadIconFile(path 16 16))
    when(icon
      sicoAskIconCachePath=path
      sicoAskIconCache=icon
      return(icon))
    unless(sicoAskIconWarnedPath==path
      sicoAskIconWarnedPath=path
      warn("Silicon Copilot: cannot load menu icon: %s\n" path))
    return(nil)
  ))

procedure(sicoEditorMenuCommand(entry)
  let((binding)
    binding=makeTable("sicoEditorTarget" nil)
    binding['window]=hiGetCurrentWindow()
    binding['entry]=entry
    when(binding['window] errset(sicoContextJson(binding) nil))
    sicoMenuCommand(binding)))

procedure(sicoSchematicMenus(@optional command refresh)
  let((protected menu)
    foreach(category list("schNone" "instance" "wire" "schStandard" "schMultiple"
                          "label" "shapes" "schPin" "instPin" "schUnknown" "marker"
                          "indexDefault" "indexInstPin" "indexPin" "indexSheet" "border")
      protected=errset(schGetPopUpMenu(category) nil)
      menu=when(protected car(protected))
      when(menu
        /* Replace our item on reload so existing editors receive the new icon. */
        when(and(refresh get(menu 'sicoSchematicAsk))
          hiDeleteMenuItem(menu 'sicoSchematicAsk))
        unless(get(menu 'sicoSchematicAsk)
          hiInsertMenuItem(menu hiCreateMenuItem(
            ?name 'sicoSchematicAsk ?itemText "Ask Silicon Copilot..."
            ?itemIcon sicoAskIcon()
            ?callback "sicoFromMenu(\"refresh_required\")") 0))
        when(command hiSetMenuItemCallback(menu 'sicoSchematicAsk command))))
    t))

procedure(sicoPrepareSchematicMenu()
  /* Schematic canvas does not run the menu-builder enable callback. */
  errset(sicoSchematicMenus(sicoEditorMenuCommand("schematic_rmb")) t)
  t)

procedure(sicoInstallSchematicRmb(@optional args)
  let((binding prefix)
    when(isCallable('schGetPopUpMenu) sicoSchematicMenus(nil t))
    prefix="sicoPrepareSchematicMenu() "
    foreach(key list("None<Btn3Down>" "None<Btn3Up>")
      binding=hiGetBindKey("Schematics" key)
      when(and(stringp(binding) strlen(binding)>0)
        /* Preserve the complete original command, including site customizations. */
        unless(and(strlen(binding)>=strlen(prefix)
                   substring(binding 1 strlen(prefix))==prefix)
          hiSetBindKey("Schematics" key strcat(prefix binding)))))
    t))

procedure(sicoLayoutMenuItem()
  hiCreateMenuItem(?name 'sicoLayoutAsk ?itemText "Ask Silicon Copilot..."
    ?itemIcon sicoAskIcon()
    ?callback sicoEditorMenuCommand("layout_rmb")))

procedure(sicoInstallLayoutRmb(@optional args)
  when(isCallable('mbRegisterCustomMenu)
    /* XL/GXL inherit maskLayout entries; registering each would duplicate them. */
    unless(sicoLayoutMenus["maskLayout"]
      when(mbRegisterCustomMenu("maskLayout" "sicoAskSiliconCopilot" "t"
             "sicoLayoutMenuItem()")
        sicoLayoutMenus["maskLayout"]=mbSetContextData("maskLayout"
          "sicoAskSiliconCopilot" "Any None" "Canvas Navigator" "Create"))))
  t)

procedure(sicoRegisterRmbTrigger(viewType callback)
  let((info)
    info=deGetAppInfo(viewType)
    unless(and(info member(callback info->userPostInstallTrigList))
      deRegUserTriggers(viewType nil nil callback))))

procedure(sicoInstallEditorMenus()
  /* dbAccess has no editor UI; it still loads the standalone Agent bridge. */
  when(and(isCallable('hiGetBindKey) isCallable('deRegUserTriggers))
    foreach(viewType list("schematic" "schematicXL")
      sicoRegisterRmbTrigger(viewType 'sicoInstallSchematicRmb))
    foreach(viewType list("maskLayout" "maskLayoutXL" "maskLayoutGXL")
      sicoRegisterRmbTrigger(viewType 'sicoInstallLayoutRmb))
    /* Also extend editors that were open before this file was loaded. */
    sicoInstallSchematicRmb()
    sicoInstallLayoutRmb())
  t)

procedure(sicoRmbRevision()
  "20260921.sico.rmb.v2")

sicoRmbVersion="20260921.sico.rmb.v2"
)
