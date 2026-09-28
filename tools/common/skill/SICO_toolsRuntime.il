; Fresh-session initialization for the protected independent CAD context.

procedure(SICO_toolsInitialize()
  ; Resolve in the target session. Never save build host paths or session state.
  SICO_installationRoot()
  SICO_stateInitialize()
  SICO_initializeWorkingDir()
  unless(and(boundp('sicoToolsRuntimeInitialized) sicoToolsRuntimeInitialized)
    drcInitializeConfig()
    lvsInitializeConfig()
    lvsStageInitializeConfig()
    rceInitializeConfig()
    lefInitializeConfig()
    SICO_lsfInitializeRuntime()
    SICO_lsfMonitorInitializeRuntime()
    drcRuleSelectInitializeRuntime()
    rceDspfInitializeRuntime()
    cadNl2ViewInitializeRuntime()
    mtsNetlistorInitializeRuntime()
    sicoToolsRuntimeInitialized=t)
  SICO_toolsInstallMenus()
  t)

; One-way compatibility for sites using the former init procedure.
