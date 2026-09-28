; Public ADE customization callbacks, composed with any existing site callbacks.

unless(and(boundp('sicoMenusVersion)
           sicoMenusVersion=="20260921.sico.menus.v1")

unless(boundp('sicoMenuTargets) sicoMenuTargets=makeTable("sicoMenuTargets" nil))
unless(boundp('sicoMenuOrder) sicoMenuOrder=nil)
unless(boundp('sicoMenuCounter) sicoMenuCounter=0)
unless(boundp('sicoInstalledHistoryDefinition) sicoInstalledHistoryDefinition=nil)
unless(boundp('sicoInstalledResultsDefinition) sicoInstalledResultsDefinition=nil)
unless(boundp('sicoAdeWindowMenus) sicoAdeWindowMenus=makeTable("sicoAdeWindowMenus" nil))
/* hiRegTimer delay is a ~0.1 s tick (measured: 5 -> 1.2 s, 20 -> 2.8 s,
   50 -> 5.4 s, 100 -> 9.4 s), not milliseconds; 20 is about two seconds. */
unless(boundp('sicoAdeMenuPollTicks) sicoAdeMenuPollTicks=20)
unless(boundp('sicoAdeMenuPolling) sicoAdeMenuPolling=nil)

procedure(sicoWindowForSession(session)
  let((found protected)
    foreach(win hiGetWindowList()
      protected=errset(axlGetWindowSession(win) nil)
      when(and(protected car(protected)==session) found=win))
    found))

procedure(sicoWindowForResult(session testName)
  let((found protected axlSession tests)
    foreach(win hiGetWindowList()
      protected=errset(axlGetWindowSession(win) nil)
      axlSession=when(protected car(protected))
      when(axlSession
        when(axlSession==session found=win)
        protected=errset(axlGetTests(axlGetMainSetupDB(axlSession)) nil)
        tests=if(testName then list(testName) else when(protected cadr(car(protected))))
        foreach(test tests
          protected=errset(axlGetToolSession(axlSession test) nil)
          when(and(protected car(protected)==session) found=win))))
    found))

procedure(sicoMenuCommand(binding)
  let((key retired)
    sicoMenuCounter=sicoMenuCounter+1
    key=sprintf(nil "menu_%d" sicoMenuCounter)
    sicoMenuTargets[key]=binding
    sicoMenuOrder=append1(sicoMenuOrder key)
    when(length(sicoMenuOrder)>128
      retired=car(sicoMenuOrder)
      sicoMenuTargets[retired]=nil
      sicoMenuOrder=cdr(sicoMenuOrder))
    sprintf(nil "sicoFromMenu(\"%s\")" key)))

procedure(sicoFromMenu(key)
  let((binding)
    binding=sicoMenuTargets[key]
    if(binding then sicoQuickAsk(binding)
    else warn("Silicon Copilot: reopen the context menu to refresh this target.\n"))))

procedure(sicoHistoryMenu(session history)
  let((existing protected binding command)
    when(isCallable('sicoPreviousHistoryMenu)
      protected=errset(sicoPreviousHistoryMenu(session history) nil)
      when(protected existing=car(protected)))
    binding=makeTable("sicoHistoryTarget" nil)
    binding['window]=sicoWindowForSession(session)
    binding['entry]="ade_history"
    binding['adeSession]=session
    binding['historyHandle]=history
    protected=errset(axlGetHistoryName(history) nil)
    when(protected binding['historyName]=car(protected))
    when(binding['window] errset(sicoContextJson(binding) nil))
    command=sicoMenuCommand(binding)
    append(existing list(list("Ask Silicon Copilot..." command
      if(and(binding['window] binding['historyName] binding['valid])
        then "false" else "true"))))))

procedure(sicoMenuArgument(arguments keyword)
  prog((tail)
    tail=member(keyword arguments)
    return(when(tail cadr(tail)))))

procedure(sicoResultsMenu(@rest arguments)
  let((existing protected binding window adeSession axlSession testName)
    when(isCallable('sicoPreviousResultsMenu)
      protected=errset(apply('sicoPreviousResultsMenu arguments) nil)
      when(protected existing=car(protected)))
    ; IC231 supports axlSession/sevSession/testName as well as older adeSession.
    ; Prefer the explicit session over UI focus, keeping raw args for the site hook.
    adeSession=or(sicoMenuArgument(arguments ?sevSession)
                   sicoMenuArgument(arguments ?adeSession))
    axlSession=sicoMenuArgument(arguments ?axlSession)
    testName=sicoMenuArgument(arguments ?testName)
    window=cond(
      (axlSession sicoWindowForSession(axlSession))
      (adeSession sicoWindowForResult(adeSession testName))
      (t hiGetCurrentWindow()))
    binding=makeTable("sicoResultsTarget" nil)
    binding['window]=window binding['entry]="ade_results"
    when(window
      protected=errset(axlGetWindowSession(window) nil)
      when(protected binding['adeSession]=car(protected)))
    binding['resultSession]=adeSession
    binding['testName]=testName
    when(window errset(sicoContextJson(binding) nil))
    append(existing list(list("Ask Silicon Copilot..." sicoMenuCommand(binding)
      !and(binding['adeSession] binding['valid]))))))

procedure(sicoInstallAdeMenus()
  unless(getd('axlGetDataViewHistoryUserMenu)==getd('sicoHistoryMenu)
    when(and(isCallable('axlGetDataViewHistoryUserMenu)
             getd('axlGetDataViewHistoryUserMenu)!=sicoInstalledHistoryDefinition)
      putd('sicoPreviousHistoryMenu getd('axlGetDataViewHistoryUserMenu)))
    putd('axlGetDataViewHistoryUserMenu getd('sicoHistoryMenu)))
  sicoInstalledHistoryDefinition=getd('axlGetDataViewHistoryUserMenu)
  unless(getd('axlGetCustomOutputResultMenu)==getd('sicoResultsMenu)
    when(and(isCallable('axlGetCustomOutputResultMenu)
             getd('axlGetCustomOutputResultMenu)!=sicoInstalledResultsDefinition)
      putd('sicoPreviousResultsMenu getd('axlGetCustomOutputResultMenu)))
    putd('axlGetCustomOutputResultMenu getd('sicoResultsMenu)))
  sicoInstalledResultsDefinition=getd('axlGetCustomOutputResultMenu)
  errset(sicoInstallAdeWindowMenus() nil)
  errset(sicoStartAdeMenuPoll() nil)
  t)

/* ADE exposes no user menu for the data view page itself; only history entries
   (axlGetDataViewHistoryUserMenu) and results can be extended. ADE windows do
   accept an inserted menu-bar entry, so Silicon Copilot is reachable from the
   moment an ADE window opens. hi has no window-open trigger, so a light poll
   re-checks windows that ADE creates or rebuilds after load. */

procedure(sicoAdeWindowP(win)
  let((protected)
    protected=errset(axlGetWindowSession(win) nil)
    when(protected car(protected))))

procedure(sicoAdeMenuBinding(win)
  let((binding)
    binding=makeTable("sicoAdeMenuTarget" nil)
    binding['window]=win
    binding['entry]="ade_menu"
    binding))

procedure(sicoAdeWindowMenu(win)
  let((aawIcon)
    aawIcon=when(isCallable('sicoAskIcon) sicoAskIcon())
    hiCreatePulldownMenu('sicoAdePulldown "Silicon Copilot"
      list(hiCreateMenuItem(?name 'sicoAdeAsk ?itemText "Ask Silicon Copilot..."
        ?itemIcon aawIcon ?callback sicoMenuCommand(sicoAdeMenuBinding(win)))))))

procedure(sicoAdeMenuPosition(banners)
  /* Keep the familiar location after Tools; append when that menu is absent. */
  let((aamTail)
    aamTail=member('axlToolsMenu1 banners)
    if(aamTail then length(banners)-length(aamTail)+1 else length(banners))))

procedure(sicoInstallAdeWindowMenus(@optional args)
  let((aiwInstalled aiwBanners aiwMenu)
    aiwInstalled=0
    when(and(isCallable('hiGetWindowList) isCallable('hiGetBannerMenus)
             isCallable('hiInsertBannerMenu) isCallable('axlGetWindowSession))
      foreach(win hiGetWindowList()
        when(sicoAdeWindowP(win)
          aiwBanners=errset(hiGetBannerMenus(win) nil)
          aiwBanners=when(aiwBanners car(aiwBanners))
          when(and(aiwBanners listp(aiwBanners)
                   !member('sicoAdePulldown aiwBanners))
            aiwMenu=sicoAdeWindowMenu(win)
            when(and(aiwMenu car(errset(hiInsertBannerMenu(win aiwMenu
                       sicoAdeMenuPosition(aiwBanners)) nil)))
              sicoAdeWindowMenus[win]=t
              aiwInstalled=aiwInstalled+1)))))
    aiwInstalled))

procedure(sicoAdeMenuPoll()
  sicoAdeMenuPolling=nil
  errset(sicoInstallAdeWindowMenus() nil)
  when(isCallable('hiRegTimer)
    sicoAdeMenuPolling=t
    hiRegTimer("sicoAdeMenuPoll()" sicoAdeMenuPollTicks))
  t)

procedure(sicoStartAdeMenuPoll()
  unless(sicoAdeMenuPolling
    when(isCallable('hiRegTimer)
      sicoAdeMenuPolling=t
      hiRegTimer("sicoAdeMenuPoll()" sicoAdeMenuPollTicks)))
  sicoAdeMenuPolling)

procedure(sicoMenusRevision()
  "20260921.sico.menus.v1")

sicoMenusVersion="20260921.sico.menus.v1"
)
