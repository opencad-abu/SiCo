; Shared execution host identities and LSF revision contract.

unless(and(boundp('sicoCADlsfModulesVersion)
             sicoCADlsfModulesVersion=="20260919.common.modules.v1")
procedure(SICO_lsfRevision()
  sicoLsfVersion
)

procedure(SICO_lsfFormValidP(form)
  and(form
      if(isCallable('hiIsForm) then hiIsForm(form) else t))
)

procedure(SICO_lsfUniqueHosts(candidates)
  let((hosts host)
    hosts=nil
    foreach(host candidates
      when(and(host strlen(host)>0 !member(host hosts))
        hosts=append1(hosts host)
      )
    )
    hosts
  )
)

procedure(SICO_lsfLocalHost()
  or(getShellEnvVar("HOSTNAME") "localhost")
)

procedure(SICO_lsfAutoHost()
  "Auto (LSF Scheduler)"
)

sicoLsfVersion="20260901.form.lifecycle.v1"
sicoCADlsfModulesVersion="20260919.common.modules.v1"
)
