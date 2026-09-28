; Handle execution mode/queue changes and initialize LSF cleanup.

unless(and(boundp('sicoCADlsfChoicesModulesVersion)
             sicoCADlsfChoicesModulesVersion=="20260924.flow.environment.v3")
procedure(SICO_lsfQueueChoicesCB(form)
  when(and(SICO_lsfFormValidP(form)
           !get(form 'cadLsfQueueUpdateActive)
           form~>runType~>value=="LSF Farm")
    SICO_lsfClearPreferredValues(form)
    SICO_lsfStartHostDiscovery(form)
  )
)

procedure(SICO_lsfChoicesCB(form)
  when(SICO_lsfFormValidP(form)
    if(form~>runType~>value!="LSF Farm" then
      progn(
        SICO_lsfCancelFormDiscovery(form)
        SICO_lsfClearVerifiedSelection(form)
        SICO_lsfClearPreferredValues(form)
        SICO_lsfUpdateQueueField(form list("") "")
        form~>queueName~>enabled=nil
        form~>srvName~>items=list(SICO_lsfLocalHost())
        form~>srvName~>value=SICO_lsfLocalHost()
        form~>srvName~>enabled=nil
      )
    else
      SICO_lsfStartQueueDiscovery(form)
    )
    when(get(form 'lsfMonitor)
      form~>lsfMonitor~>enabled=form~>runType~>value=="LSF Farm"))
  t
)

procedure(SICO_lsfInitializeRuntime()
  SICO_lsfRegisterExitCleanup()
)

unless(getShellEnvVar("VIRTUOSO_CONTEXT_BUILD")=="1"
  SICO_lsfRegisterExitCleanup())
procedure(SICO_lsfModulesRevision()
  "20260924.flow.environment.v3"
)
sicoCADlsfChoicesModulesVersion="20260924.flow.environment.v3"
)
