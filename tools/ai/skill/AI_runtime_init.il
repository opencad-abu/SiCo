; Runtime-only context initialization.  Keep session/UI registrations out of
; the saved context build process: they must bind to the loading Virtuoso
; session, never to the private Virtuoso process that compiled the context.

procedure(aiRuntimeResolveRoot()
  let((toolsRoot launcherRoot)
    when(isCallable('witMenuToolsRoot)
      toolsRoot=witMenuToolsRoot()
      when(and(stringp(toolsRoot) strlen(toolsRoot)>0)
        launcherRoot=simplifyFilename(strcat(toolsRoot "/.."))))
    SICO_installationPath(SICO_installationRoot(launcherRoot) "tools/ai")))

procedure(aiRuntimeInitialize()
  ; Capture launch state once in the target session; repeated initialization
  ; must not move an active controller's temporary directory.
  SICO_stateInitialize()
  unless(boundp('aiLaunchWorkingDir)
    aiLaunchWorkingDir=aiRuntimeWorkingDirectory())
  unless(boundp('aiWorkflowLaunchDir)
    aiWorkflowLaunchDir=aiRuntimeWorkingDirectory())
  unless(boundp('sicoLaunchWorkingDir)
    sicoLaunchWorkingDir=aiLaunchWorkingDir)
  unless(boundp('aiWindowEpoch)
    aiWindowEpoch=car(last(parseString(makeTempFileName("/tmp/cwin") "/"))))
  ; Loader globals are resolved only in the target Virtuoso session.  They are
  ; needed by menu icons and optional native Python entry points, but must not
  ; be captured while compiling the context.
  when(isCallable('aiRuntimeResolveRoot)
    let((root)
      root=aiRuntimeResolveRoot()
      when(root
        set('aiRoot root)
        set('sicoRoot SICO_installationPath(SICO_installationRoot() "tools/sico")))))
  when(isCallable('aiWorkflowRegisterExitCleanup)
    aiWorkflowRegisterExitCleanup())
  when(isCallable('aiIpcRegisterExitCleanup)
    aiIpcRegisterExitCleanup())
  when(isCallable('aiMaestroRegisterExitCleanup)
    aiMaestroRegisterExitCleanup())
  ; Replace stale registration on additive reload, then bind to this session.
  when(isCallable('dbUnregPostSaveTrigger)
    errset(dbUnregPostSaveTrigger('aiLiveModelPostSaveTrigger) nil))
  when(isCallable('dbRegPostSaveTrigger)
    errset(dbRegPostSaveTrigger('aiLiveModelPostSaveTrigger 0) nil))
  when(isCallable('sicoInstallAdeMenus)
    sicoInstallAdeMenus())
  when(isCallable('sicoInstallEditorMenus)
    sicoInstallEditorMenus())
  when(isCallable('sicoInstallNativeBindings)
    sicoInstallNativeBindings())
  when(isCallable('sicoSchedulePlatformRegistration)
    sicoSchedulePlatformRegistration())
  t)

procedure(aiRuntimeWorkingDirectory()
  SICO_stateLaunchDirectory())
