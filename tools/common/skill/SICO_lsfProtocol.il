; Read and validate queue/host discovery transfer records.

unless(and(boundp('sicoCADlsfProtocolModulesVersion)
             sicoCADlsfProtocolModulesVersion=="20260919.common.modules.v1")
procedure(SICO_lsfProtocolRecords(path allowedKinds acceptedStatuses)
  let((document status valid version)
    document=SICO_guiProtocolRead(path allowedKinds)
    valid=car(document)
    status=caddr(document)
    version=cadddr(document)
    list(and(valid version=="1" member(status acceptedStatuses))
         cadr(document) status)
  )
)

procedure(SICO_lsfReadQueues(path)
  let((document queues record valid)
    document=SICO_lsfProtocolRecords(
      path list("QUEUE") list("ready" "partial"))
    valid=car(document)
    foreach(record cadr(document)
      if(member(cadr(record) queues) then valid=nil
      else queues=append1(queues cadr(record))))
    list(valid queues caddr(document))
  )
)

procedure(SICO_lsfReadHosts(path expectedQueue)
  let((document hosts record selected selectedCount valid)
    document=SICO_lsfProtocolRecords(
      path list("SELECTED_QUEUE" "HOST") list("ready" "partial"))
    valid=car(document)
    selectedCount=0
    foreach(record cadr(document)
      case(car(record)
        ("SELECTED_QUEUE"
          selectedCount=selectedCount+1
          selected=cadr(record))
        ("HOST"
          if(member(cadr(record) hosts) then valid=nil
          else hosts=append1(hosts cadr(record))))))
    when(or(selectedCount!=1 selected!=expectedQueue)
      valid=nil)
    list(valid hosts caddr(document))
  )
)

sicoCADlsfProtocolModulesVersion="20260919.common.modules.v1"
)
