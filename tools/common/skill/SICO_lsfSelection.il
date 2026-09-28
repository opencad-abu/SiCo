; Apply and validate verified host/queue selections.

unless(and(boundp('sicoCADlsfSelectionModulesVersion)
             sicoCADlsfSelectionModulesVersion=="20260919.common.modules.v1")
procedure(SICO_lsfPreferredQueue(queues previous)
  if(and(previous member(previous queues)) then previous else car(queues))
)

procedure(SICO_lsfSetPreferredValues(form queue host)
  let((preferredHost preferredQueue)
    preferredQueue=if(stringp(queue) then queue else "")
    preferredHost=if(stringp(host) then host else "")
    putpropq(form preferredQueue cadLsfPreferredQueue)
    putpropq(form preferredHost cadLsfPreferredHost)
    t
  )
)

procedure(SICO_lsfClearPreferredValues(form)
  putpropq(form nil cadLsfPreferredQueue)
  putpropq(form nil cadLsfPreferredHost)
  t
)

procedure(SICO_lsfUpdateQueueField(form items value)
  let((updateActive)
    updateActive=get(form 'cadLsfQueueUpdateActive)
    putpropq(form t cadLsfQueueUpdateActive)
    unwindProtect(
      progn(
        form~>queueName~>items=items
        form~>queueName~>value=value
      )
      putpropq(form updateActive cadLsfQueueUpdateActive)
    )
    value
  )
)

procedure(SICO_lsfApplyQueues(form queues previous)
  let((preferred)
    when(queues
      preferred=SICO_lsfPreferredQueue(queues previous)
      SICO_lsfUpdateQueueField(form queues preferred)
    )
    preferred
  )
)

procedure(SICO_lsfPreferredHost(hosts previous)
  let((requested)
    requested=if(and(stringp(previous) strlen(previous)>0)
                then previous else SICO_lsfAutoHost())
    if(member(requested hosts) then requested else car(hosts))
  )
)

procedure(SICO_lsfClearVerifiedSelection(form)
  putpropq(form nil cadLsfVerifiedQueues)
  putpropq(form nil cadLsfVerifiedHosts)
  putpropq(form nil cadLsfVerifiedHostQueue)
  t
)

procedure(SICO_lsfSelectionValidP(form)
  ; Selection validation can be called by a late Run callback after the form
  ; has been closed or deleted.  Check the HI handle before touching fields.
  if(!SICO_lsfFormValidP(form) then
    nil
  else
    let((host queue verifiedHosts verifiedQueue verifiedQueues)
      if(form~>runType~>value!="LSF Farm" then
        t
      else
        queue=or(form~>queueName~>value "")
        host=or(form~>srvName~>value "")
        verifiedQueues=or(get(form 'cadLsfVerifiedQueues) nil)
        verifiedHosts=or(get(form 'cadLsfVerifiedHosts) nil)
        verifiedQueue=get(form 'cadLsfVerifiedHostQueue)
        and(strlen(queue)>0
            member(queue verifiedQueues)
            or(and(host==SICO_lsfAutoHost()
                   verifiedQueue==queue)
               and(stringp(host)
                   strlen(host)>0
                   verifiedQueue==queue
                   member(host verifiedHosts))))
      )
    )
  )
)

procedure(SICO_lsfValidateSelection(form title)
  let((message)
    if(SICO_lsfSelectionValidP(form) then
      t
    else
      message=strcat(
        "LSF Queue/Host selection is not verified. Wait for discovery "
        "to finish and choose an available Queue/Host.")
      if(isCallable('GUI_messageApp) then
        GUI_messageApp('e or(title "LSF") message)
      else
        warn("<WARN> LSF::%s\n" message))
      nil
    )
  )
)

procedure(SICO_lsfApplyHosts(form hosts previous)
  let((choices preferred)
    choices=cons(SICO_lsfAutoHost()
                 remove(SICO_lsfAutoHost() SICO_lsfUniqueHosts(hosts)))
    when(choices
      preferred=SICO_lsfPreferredHost(choices previous)
      form~>srvName~>items=choices
      form~>srvName~>value=preferred
    )
    preferred
  )
)

sicoCADlsfSelectionModulesVersion="20260919.common.modules.v1"
)
