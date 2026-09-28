unless(and(boundp('sicoFlowLogVersion)
             sicoFlowLogVersion=="20260721.flow.log.owned.v2")

unless(boundp('cadFlowLogRequests)
  cadFlowLogRequests=makeTable("cadFlowLogRequests" nil)
)

unless(boundp('cadFlowLogRequestSerial)
  cadFlowLogRequestSerial=0
)

unless(boundp('cadFlowLogWindows)
  cadFlowLogWindows=makeTable("cadFlowLogWindows" nil)
)

unless(boundp('cadFlowLogWindowRequests)
  cadFlowLogWindowRequests=makeTable("cadFlowLogWindowRequests" nil)
)

procedure(GUI_flowLogBrand()
  cond(
    (isCallable('SICO_logo) SICO_logo())
    (isCallable('witMenuLogo) witMenuLogo())
    (t "SiCo")
  )
)

procedure(GUI_flowLogWindowClosed(window)
  let((requestId)
    requestId=cadFlowLogWindowRequests[window]
    when(requestId
      remove(window cadFlowLogWindowRequests)
      remove(requestId cadFlowLogWindows)
    )
    t
  )
)

procedure(GUI_flowLogCreate(flow logFile requestId)
  let((title win)
    title=strcat(GUI_flowLogBrand() "::" flow " Log")
    win=hiViewTextFile(logFile
          ?winSpec 'default
          ?title title
          ?appName strcat(GUI_flowLogBrand() "FlowLog")
          ?deviceMode 'Sparse
          ?fixedFont t)
    when(win
      cadFlowLogWindows[requestId]=win
      cadFlowLogWindowRequests[win]=requestId
      hiRegCloseProc(win 'GUI_flowLogWindowClosed)
      when(isCallable('SICO_setWindowIcon)
        SICO_setWindowIcon(win)
      )
      hiEnableTailViewfile(win)
      hiScrollWindowBottom(win)
    )
    win
  )
)

procedure(GUI_flowLogOpenRequest(requestId)
  let((request flow logFile attempts)
    request=cadFlowLogRequests[requestId]
    when(request
      flow=car(request)
      logFile=cadr(request)
      attempts=caddr(request)
      cond(
        (and(logFile isFile(logFile))
          remove(requestId cadFlowLogRequests)
          GUI_flowLogCreate(flow logFile requestId)
        )
        (attempts<50
          cadFlowLogRequests[requestId]=list(flow logFile attempts+1)
          hiRegTimer(
            sprintf(nil "GUI_flowLogOpenRequest(%d)" requestId)
            2)
        )
        (t
          remove(requestId cadFlowLogRequests)
          warn("%s Flow Log cannot access log file: %L\n" flow logFile)
          nil
        )
      )
    )
  )
)

procedure(GUI_flowLogOpen(flow logFile)
  let((requestId)
    cadFlowLogRequestSerial=cadFlowLogRequestSerial+1
    requestId=cadFlowLogRequestSerial
    cadFlowLogRequests[requestId]=list(flow logFile 0)
    GUI_flowLogOpenRequest(requestId)
    requestId
  )
)

procedure(GUI_flowLogShow(requestId)
  let((win)
    win=cadFlowLogWindows[requestId]
    cond(
      (and(win windowp(win))
        hiRaiseWindow(win)
      )
      (win
        GUI_flowLogWindowClosed(win)
        nil
      )
      (cadFlowLogRequests[requestId] t)
      (t nil)
    )
  )
)

procedure(GUI_flowLogClose(requestId)
  let((win closed)
    remove(requestId cadFlowLogRequests)
    win=cadFlowLogWindows[requestId]
    cond(
      (null(win) t)
      (not(windowp(win))
        GUI_flowLogWindowClosed(win)
      )
      (t
        closed=hiCloseWindow(win)
        when(closed
          GUI_flowLogWindowClosed(win)
        )
        closed
      )
    )
  )
)

procedure(GUI_flowLogRevision()
  "20260721.flow.log.owned.v2"
)

sicoFlowLogVersion="20260721.flow.log.owned.v2"
)
