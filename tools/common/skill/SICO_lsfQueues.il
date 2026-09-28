; Own queue discovery requests, receipts and host-discovery chaining.

unless(and(boundp('sicoCADlsfQueuesModulesVersion)
             sicoCADlsfQueuesModulesVersion=="20260919.common.modules.v1")
procedure(SICO_lsfQueueDiscoveryDataHandler(cid data)
  let((state)
    state=cadLsfQueueDiscoveryState[cid]
    when(state state['output]=strcat(or(state['output] "") data))
  )
)

procedure(SICO_lsfQueueDiscoveryErrHandler(cid data)
  let((state)
    state=cadLsfQueueDiscoveryState[cid]
    when(state state['errors]=strcat(or(state['errors] "") data))
  )
)

procedure(SICO_lsfQueueDiscoveryPostFunc(cid exitStatus)
  let((form preferred previous queues result state validRequest)
    state=cadLsfQueueDiscoveryState[cid]
    remove(cid cadLsfQueueDiscoveryState)
    when(state
      form=state['form]
      result=SICO_lsfReadQueues(state['resultFile])
      SICO_lsfDeleteFile(state['resultFile])
      validRequest=and(!state['cancelled]
        SICO_lsfFormValidP(form)
        form~>cadLsfQueueDiscoveryCid==cid
        get(form 'cadLsfQueueRequestToken)==state['token]
        form~>runType~>value=="LSF Farm")
      when(validRequest
        form~>cadLsfQueueDiscoveryCid=nil
        preferred=get(form 'cadLsfPreferredQueue)
        previous=or(preferred state['previous])
        if(and(exitStatus==0 car(result)) then
          progn(
            queues=cadr(result)
            putpropq(form queues cadLsfVerifiedQueues)
            SICO_lsfApplyQueues(form or(queues list("")) previous)
            if(queues then
              progn(
                info("<INFO> LSF::Discovered %d accessible open queues.\n"
                     length(queues))
                form~>queueName~>enabled=t
                SICO_lsfStartHostDiscovery(form)
              )
            else
              progn(
                warn("<WARN> LSF::No accessible open execution queues were found.\n")
                form~>queueName~>enabled=nil
                form~>srvName~>items=list(SICO_lsfAutoHost())
                form~>srvName~>value=SICO_lsfAutoHost()
                form~>srvName~>enabled=nil
              )
            )
            when(caddr(result)=="partial"
              warn("<WARN> LSF::Queue cache is unavailable; verified live queue access is retained.\n"))
          )
        else
          progn(
            putpropq(form nil cadLsfVerifiedQueues)
            putpropq(form nil cadLsfVerifiedHosts)
            putpropq(form nil cadLsfVerifiedHostQueue)
            SICO_lsfApplyQueues(form list("") previous)
            form~>queueName~>enabled=nil
            form~>srvName~>items=list(SICO_lsfAutoHost())
            form~>srvName~>value=SICO_lsfAutoHost()
            form~>srvName~>enabled=nil
            warn("<WARN> LSF::Queue discovery failed or returned an invalid transfer; no unverified queues will be shown.\n")
          )
        )
        putpropq(form nil cadLsfPreferredQueue)
      )
    )
  )
)

procedure(SICO_lsfStartQueueDiscovery(form)
  let((cid command path preferred previous state token)
    when(and(SICO_lsfFormValidP(form) form~>runType~>value=="LSF Farm")
      preferred=get(form 'cadLsfPreferredQueue)
      previous=or(preferred form~>queueName~>value)
      SICO_lsfCancelState(cadLsfQueueDiscoveryState
        get(form 'cadLsfQueueDiscoveryCid))
      SICO_lsfCancelState(cadLsfDiscoveryState
        get(form 'cadLsfDiscoveryCid))
      token=add1(or(get(form 'cadLsfQueueRequestToken) 0))
      putpropq(form token cadLsfQueueRequestToken)
      SICO_lsfClearVerifiedSelection(form)
      SICO_lsfUpdateQueueField(form list("") "")
      form~>queueName~>enabled=nil
      form~>srvName~>items=list(SICO_lsfAutoHost())
      form~>srvName~>value=SICO_lsfAutoHost()
      form~>srvName~>enabled=nil
      form~>cadLsfQueueDiscoveryCid=nil
      form~>cadLsfDiscoveryCid=nil
      path=SICO_lsfDiscoveryPath("queues")
      command=SICO_lsfDiscoveryCommand("queues" nil path)
      cid=SICO_lsfBeginProcess(command 'SICO_lsfQueueDiscoveryDataHandler
            'SICO_lsfQueueDiscoveryErrHandler 'SICO_lsfQueueDiscoveryPostFunc)
      if(cid then
        progn(
          state=makeTable("cadLsfQueueDiscovery" nil)
          state['form]=form
          state['output]=""
          state['errors]=""
          state['previous]=previous
          state['resultFile]=path
          state['token]=token
          cadLsfQueueDiscoveryState[cid]=state
          form~>cadLsfQueueDiscoveryCid=cid
        )
      else
        progn(
          SICO_lsfDeleteFile(path)
          putpropq(form nil cadLsfPreferredQueue)
          warn("<WARN> LSF::Cannot start queue discovery; no unverified queues will be shown.\n")
        )
      )
    )
    cid
  )
)

unless(boundp('cadLsfQueueDiscoveryState)
  cadLsfQueueDiscoveryState=makeTable("cadLsfQueueDiscoveryState" nil))
sicoCADlsfQueuesModulesVersion="20260919.common.modules.v1"
)
