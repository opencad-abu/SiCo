; Own host discovery requests, receipts and stale-result rejection.

unless(and(boundp('sicoCADlsfHostsModulesVersion)
             sicoCADlsfHostsModulesVersion=="20260919.common.modules.v1")
procedure(SICO_lsfDiscoveryDataHandler(cid data)
  let((state)
    state=cadLsfDiscoveryState[cid]
    when(state state['output]=strcat(or(state['output] "") data))
  )
)

procedure(SICO_lsfDiscoveryErrHandler(cid data)
  let((state)
    state=cadLsfDiscoveryState[cid]
    when(state state['errors]=strcat(or(state['errors] "") data))
  )
)

procedure(SICO_lsfDiscoveryPostFunc(cid exitStatus)
  let((form hosts preferred previous queue result state validRequest)
    state=cadLsfDiscoveryState[cid]
    remove(cid cadLsfDiscoveryState)
    when(state
      form=state['form]
      queue=state['queue]
      result=SICO_lsfReadHosts(state['resultFile] queue)
      SICO_lsfDeleteFile(state['resultFile])
      validRequest=and(!state['cancelled]
        SICO_lsfFormValidP(form)
        form~>cadLsfDiscoveryCid==cid
        get(form 'cadLsfHostRequestToken)==state['token]
        form~>runType~>value=="LSF Farm"
        form~>queueName~>value==queue)
      when(validRequest
        form~>cadLsfDiscoveryCid=nil
        preferred=get(form 'cadLsfPreferredHost)
        previous=or(preferred state['previous])
        if(and(exitStatus==0 car(result)) then
          progn(
            hosts=cadr(result)
            putpropq(form hosts cadLsfVerifiedHosts)
            putpropq(form queue cadLsfVerifiedHostQueue)
            SICO_lsfApplyHosts(form hosts previous)
            form~>srvName~>enabled=t
            if(hosts then
              info("<INFO> LSF::Discovered %d available execution hosts.\n"
                   length(hosts))
            else
              warn("<WARN> LSF::No accessible execution hosts are currently available; the scheduler remains selectable.\n"))
            when(caddr(result)=="partial"
              warn("<WARN> LSF::Host discovery is partial; available verified hosts are retained.\n"))
          )
        else
          progn(
            putpropq(form nil cadLsfVerifiedHosts)
            putpropq(form nil cadLsfVerifiedHostQueue)
            SICO_lsfApplyHosts(form nil SICO_lsfAutoHost())
            form~>srvName~>enabled=t
            warn("<WARN> LSF::Host discovery failed or returned an invalid transfer; no unverified hosts will be shown.\n")
          )
        )
        putpropq(form nil cadLsfPreferredHost)
      )
    )
  )
)

procedure(SICO_lsfStartHostDiscovery(form)
  let((cid command path preferred previous queue state token)
    when(and(SICO_lsfFormValidP(form) form~>runType~>value=="LSF Farm")
      preferred=get(form 'cadLsfPreferredHost)
      previous=or(preferred form~>srvName~>value)
      queue=or(form~>queueName~>value "")
      putpropq(form nil cadLsfVerifiedHosts)
      putpropq(form nil cadLsfVerifiedHostQueue)
      SICO_lsfCancelState(cadLsfDiscoveryState
        get(form 'cadLsfDiscoveryCid))
      token=add1(or(get(form 'cadLsfHostRequestToken) 0))
      putpropq(form token cadLsfHostRequestToken)
      form~>srvName~>items=list(SICO_lsfAutoHost())
      form~>srvName~>value=SICO_lsfAutoHost()
      form~>srvName~>enabled=nil
      form~>cadLsfDiscoveryCid=nil
      path=SICO_lsfDiscoveryPath("hosts")
      command=SICO_lsfDiscoveryCommand("hosts" queue path)
      cid=SICO_lsfBeginProcess(command 'SICO_lsfDiscoveryDataHandler
            'SICO_lsfDiscoveryErrHandler 'SICO_lsfDiscoveryPostFunc)
      if(cid then
        progn(
          state=makeTable("cadLsfDiscovery" nil)
          state['form]=form
          state['output]=""
          state['errors]=""
          state['previous]=previous
          state['queue]=queue
          state['resultFile]=path
          state['token]=token
          cadLsfDiscoveryState[cid]=state
          form~>cadLsfDiscoveryCid=cid
        )
      else
        progn(
          SICO_lsfDeleteFile(path)
          putpropq(form nil cadLsfPreferredHost)
          form~>srvName~>enabled=t
          warn("<WARN> LSF::Cannot start host discovery; the scheduler remains selectable.\n")
        )
      )
    )
    cid
  )
)

unless(boundp('cadLsfDiscoveryState)
  cadLsfDiscoveryState=makeTable("cadLsfDiscoveryState" nil))
sicoCADlsfHostsModulesVersion="20260919.common.modules.v1"
)
