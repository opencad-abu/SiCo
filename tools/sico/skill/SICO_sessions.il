/* Window-to-conversation routing is separate from each submitted task's target. */

unless(and(boundp('sicoSessionsVersion)
           sicoSessionsVersion=="20260921.sico.sessions.v1")

unless(boundp('sicoWindowSessions) sicoWindowSessions=makeTable("sicoWindowSessions" nil))
unless(boundp('sicoDefaultSession) sicoDefaultSession=nil)

procedure(sicoSessionLive(cid)
  and(cid sicoBindings[cid] !sicoBindings[cid]['stopping]
      ipcIsAliveProcess(cid)))

procedure(sicoSessionForWindow(window)
  let((cid)
    cid=sicoWindowSessions[window]
    unless(sicoSessionLive(cid) cid=nil)
    unless(cid when(sicoSessionLive(sicoDefaultSession) cid=sicoDefaultSession))
    unless(cid
      foreach(candidate sicoBindings
        when(and(!cid sicoBindings[candidate]
                 or(sicoBindings[candidate]['desktopSession] sicoBindings[candidate]['launchKey])
                 sicoSessionLive(candidate)) cid=candidate)))
    cid))

procedure(sicoBindSession(window cid)
  sicoWindowSessions[window]=cid
  sicoDefaultSession=cid
  cid)

procedure(sicoSessionTarget(cid targetId)
  when(sicoBindings[cid]
    if(targetId=="bound" then sicoBindings[cid]
    else when(sicoBindings[cid]['targets] sicoBindings[cid]['targets][targetId]))))

procedure(sicoNewSession(cid targetId)
  let((source copy)
    source=sicoSessionTarget(cid targetId)
    when(source
      copy=makeTable("sicoNewTarget" nil)
      foreach(key list('window 'cellJson 'adeSession 'testName 'historyName 'historyHandle 'entry)
        copy[key]=source[key])
      sicoShow(copy['window] copy t))))

procedure(sicoSessionNotice(cid line)
  let((fields kind value target)
    fields=parseString(line " ") kind=car(fields) value=cadr(fields)
    when(and(length(fields)==2 stringp(value) rexMatchp("^[a-zA-Z0-9_-]+$" value))
      case(kind
        ("@accepted" sicoQuickAccepted(cid value))
        ("@rejected" sicoQuickRejected(cid value))
        ("@released"
          target=sicoSessionTarget(cid value)
          when(target sicoBindings[cid]['targets][value]=nil))
        ("@new_session" sicoNewSession(cid value))
        ("@reconnect" sicoReconnectSession(cid value))
        ("@shown"
          sicoBindings[cid]['shownCount]=1+or(sicoBindings[cid]['shownCount] 0)
          sicoBindings[cid]['sessionId]=value
          sicoDefaultSession=cid)
        ("@hidden" t)))))

procedure(sicoSessionsRevision()
  "20260921.sico.sessions.v1")

sicoSessionsVersion="20260921.sico.sessions.v1"
)
