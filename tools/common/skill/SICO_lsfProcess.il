; Own discovery process cancellation and exit cleanup.

unless(and(boundp('sicoCADlsfProcessModulesVersion)
             sicoCADlsfProcessModulesVersion=="20260919.common.modules.v1")
procedure(SICO_lsfDeleteFile(path)
  when(and(path isFile(path))
    errset(deleteFile(path) nil))
  nil
)

procedure(SICO_lsfSignalSucceededP(signalResult)
  and(signalResult car(signalResult)==t)
)

procedure(SICO_lsfCancelState(table cid @optional removeState)
  let((alive signalResult state)
    state=when(cid table[cid])
    when(state
      state['cancelled]=t
      alive=errset(ipcIsAliveProcess(cid) nil)
      when(and(alive car(alive))
        signalResult=when(isCallable('ipcSignalProcess)
          errset(ipcSignalProcess(cid 'TERM) nil))
        unless(SICO_lsfSignalSucceededP(signalResult)
          errset(ipcKillProcess(cid) nil)))
      SICO_lsfDeleteFile(state['resultFile])
      when(removeState remove(cid table)))
    nil
  )
)

procedure(SICO_lsfCancelFormDiscovery(form)
  when(SICO_lsfFormValidP(form)
    when(isCallable('SICO_lsfMonitorCancelForm)
      SICO_lsfMonitorCancelForm(form))
    SICO_lsfCancelState(cadLsfQueueDiscoveryState
      get(form 'cadLsfQueueDiscoveryCid))
    SICO_lsfCancelState(cadLsfDiscoveryState
      get(form 'cadLsfDiscoveryCid))
    putpropq(form nil cadLsfQueueDiscoveryCid)
    putpropq(form nil cadLsfDiscoveryCid)
    putpropq(form add1(or(get(form 'cadLsfQueueRequestToken) 0))
      cadLsfQueueRequestToken)
    putpropq(form add1(or(get(form 'cadLsfHostRequestToken) 0))
      cadLsfHostRequestToken))
  t
)

procedure(SICO_lsfStopAll()
  let((cid cids)
    foreach(cid cadLsfQueueDiscoveryState cids=cons(cid cids))
    foreach(cid cids SICO_lsfCancelState(cadLsfQueueDiscoveryState cid t))
    cids=nil
    foreach(cid cadLsfDiscoveryState cids=cons(cid cids))
    foreach(cid cids SICO_lsfCancelState(cadLsfDiscoveryState cid t))
    nil
  )
)

procedure(SICO_lsfRegisterExitCleanup()
  unless(cadLsfExitCleanupRegistered
    regExitBefore('SICO_lsfStopAll)
    cadLsfExitCleanupRegistered=t)
  cadLsfExitCleanupRegistered
)

procedure(SICO_lsfBeginProcess(command dataHandler errHandler postFunc)
  when(command ipcBeginProcess(command "" dataHandler errHandler postFunc))
)

unless(boundp('cadLsfExitCleanupRegistered)
  cadLsfExitCleanupRegistered=nil)
sicoCADlsfProcessModulesVersion="20260919.common.modules.v1"
)
