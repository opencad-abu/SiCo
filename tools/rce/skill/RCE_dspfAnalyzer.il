; Process and OpenAccess bridge for the external PyQt5 DSPF analyzer.

unless(and(boundp('rceDspfAnalyzerVersion)
             rceDspfAnalyzerVersion=="20260722.dspf.analyzer.v5"
             isCallable('rceDspfProtocolRevision)
             rceDspfProtocolRevision()=="20260721.dspf.protocol.v3"
             isCallable('rceDspfGeometryRevision)
             rceDspfGeometryRevision()=="20260721.dspf.geometry.v1")

unless(boundp('rceDspfAnalyzerState)
  rceDspfAnalyzerState=makeTable("rceDspfAnalyzerState" nil)
)

procedure(rceDspfOaName(dspfName)
  let((result index character)
    result=""
    for(index 1 strlen(dspfName)
      character=substring(dspfName index 1)
      result=strcat(result
        case(character
          ("[" "<")
          ("]" ">")
          (t character))))
    result
  )
)

procedure(rceDspfLayoutMatches(window)
  let((cellView editCellView)
    cellView=when(and(window windowp(window)) geGetWindowCellView(window))
    editCellView=when(cellView geGetEditCellView(window))
    and(cellView
         editCellView==cellView
         cellView~>libName==rceDspfAnalyzerState['layoutLib]
         cellView~>cellName==rceDspfAnalyzerState['layoutCell]
         cellView~>viewName==rceDspfAnalyzerState['layoutView])
  )
)

procedure(rceDspfClearHighlight()
  let((window highlightSet)
    window=rceDspfAnalyzerState['window]
    highlightSet=rceDspfAnalyzerState['highlightSet]
    when(highlightSet
      errset(geDeleteHilightSet(highlightSet) t)
      when(rceDspfLayoutMatches(window)
        errset(geRefreshWindow(window) t)))
    rceDspfAnalyzerState['highlightSet]=nil
  )
)

procedure(rceDspfLayoutWindow()
  prog((window candidate opened)
    window=rceDspfAnalyzerState['window]
    when(rceDspfLayoutMatches(window) return(window))
    rceDspfAnalyzerState['window]=nil
    rceDspfAnalyzerState['openedWindow]=nil
    unless(and(rceDspfAnalyzerState['layoutLib]
                 strlen(rceDspfAnalyzerState['layoutLib])>0
                 rceDspfAnalyzerState['layoutCell]
                 strlen(rceDspfAnalyzerState['layoutCell])>0
                 rceDspfAnalyzerState['layoutView]
                 strlen(rceDspfAnalyzerState['layoutView])>0)
      return(nil))
    foreach(candidate hiGetWindowList('window)
      when(and(!window rceDspfLayoutMatches(candidate)) window=candidate))
    unless(window
      window=deOpenCellView(rceDspfAnalyzerState['layoutLib]
                            rceDspfAnalyzerState['layoutCell]
                            rceDspfAnalyzerState['layoutView]
                            "" nil "r")
      when(window opened=t))
    unless(rceDspfLayoutMatches(window)
      when(and(opened window windowp(window))
        errset(hiCloseWindow(window) t))
      rceDspfAnalyzerState['window]=nil
      rceDspfAnalyzerState['openedWindow]=nil
      return(nil))
    rceDspfAnalyzerState['window]=window
    rceDspfAnalyzerState['openedWindow]=opened
    return(window)
  )
)

procedure(rceDspfHighlightNet(cid id name regions hasRegions)
  prog((window cellView oaName net candidates selection figures scope created
         highlightSet figureCount bbox)
    unless(and(stringp(name) strlen(name)>0 strlen(name)<=4096)
      rceDspfSendError(cid id "invalid_params"
        "net name must contain 1 to 4096 characters")
      return(nil))
    window=rceDspfLayoutWindow()
    unless(window
      rceDspfSendError(cid id "layout_unavailable"
        "the exact RCE layout context is unavailable")
      return(nil))
    cellView=geGetWindowCellView(window)
    oaName=rceDspfOaName(name)
    net=dbFindNetByName(cellView oaName)
    unless(net
      candidates=rceDspfCaseCandidates(cellView oaName)
      rceDspfSendError(cid id "net_not_found"
        if(candidates then
          sprintf(nil
            "exact-case net %L was not found; OA has case-only candidate(s): %L"
            oaName candidates)
        else sprintf(nil "exact-case net %L was not found in the RCE layout" oaName)))
      return(nil))
    selection=rceDspfSelectHighlightFigures(net regions hasRegions)
    figures=car(selection)
    scope=cadr(selection)
    unless(figures
      rceDspfSendError(cid id "net_has_no_geometry"
        "the exact OA net has no top-level figure to highlight")
      return(nil))
    rceDspfClearHighlight()
    created=rceDspfCreateNetHighlight(cellView figures)
    unless(created
      rceDspfSendError(cid id "highlight_failed"
        "Virtuoso could not highlight the exact OA net figures")
      return(nil))
    highlightSet=car(created)
    figureCount=cadr(created)
    rceDspfAnalyzerState['window]=window
    rceDspfAnalyzerState['highlightSet]=highlightSet
    bbox=rceDspfFigureBBox(figures)
    when(and(bbox isCallable('hiZoomIn))
      errset(hiZoomIn(window bbox) t))
    geRefreshWindow(window bbox)
    rceDspfSendHighlightResult(cid id name figureCount scope)
    return(t)
  )
)

procedure(rceDspfCurrentNet(cid id)
  prog((window selected object protected net name names)
    window=rceDspfLayoutWindow()
    unless(window
      rceDspfSendError(cid id "layout_unavailable"
        "the exact RCE layout context is unavailable")
      return(nil))
    selected=geGetSelSet(window)
    foreach(object selected
      protected=errset(object~>net t)
      net=when(protected car(protected))
      when(net
        name=net~>name
        when(and(stringp(name) strlen(name)>0 not(member(name names)))
          names=cons(name names))))
    return(
      cond(
        (null(names)
          rceDspfSendError(cid id "no_selected_net"
            "select one connected OA layout figure in the RCE layout"))
        (cdr(names)
          rceDspfSendError(cid id "ambiguous_selection"
            "the OA selection contains more than one net"))
        (t rceDspfSendName(cid id car(names)))))
  )
)

procedure(rceDspfHandleRequest(cid request)
  let((id method params name regions)
    id=request['id]
    method=request['method]
    params=request['params]
    name=params['name]
    regions=params['regions]
    case(method
      ("oa.highlight_net"
        rceDspfHighlightNet(cid id name regions params['hasRegions]))
      ("oa.current_net"
        if(or(params['hasName] params['hasRegions]) then
          rceDspfSendError(cid id "invalid_params" "oa.current_net takes no params")
        else rceDspfCurrentNet(cid id)))
      ("bridge.ping"
        if(or(params['hasName] params['hasRegions]) then
          rceDspfSendError(cid id "invalid_params" "bridge.ping takes no params")
        else rceDspfSendJson(cid
          sprintf(nil "{\"id\":%d,\"ok\":true,\"result\":{\"alive\":true}}" id))))
      ("gui.select_net"
        rceDspfSendError(cid id "wrong_direction" "gui.select_net is SKILL outbound"))
      (t rceDspfSendError(cid id "unknown_method" "unsupported bridge method")))
  )
)

procedure(rceDspfHandleLine(cid line)
  let((parsed id)
    if(strlen(line)>65536 then
      rceDspfSendError(cid nil "line_too_long"
        "JSONL message exceeds 65536 characters")
    else
      parsed=rceDspfJsonParseRequest(line)
      id=nth(4 parsed)
      if(car(parsed) then
        rceDspfHandleRequest(cid cadr(parsed))
      else rceDspfSendError(cid id "invalid_json" nth(3 parsed))))
  )
)

procedure(rceDspfAnalyzerDataHandler(cid data)
  let((buffer length start index line)
    when(cid==rceDspfAnalyzerState['cid]
      buffer=strcat(or(rceDspfAnalyzerState['buffer] "") or(data ""))
      length=strlen(buffer)
      start=1
      for(index 1 length
        when(substring(buffer index 1)=="\n"
          line=substring(buffer start index-start)
          when(strlen(line)>0 rceDspfHandleLine(cid line))
          start=index+1))
      rceDspfAnalyzerState['buffer]=if(start<=length then
        substring(buffer start length-start+1) else "")
      when(strlen(rceDspfAnalyzerState['buffer])>65536
        rceDspfAnalyzerState['buffer]=""
        warn("RCE DSPF analyzer discarded an overlong unterminated JSONL message.\n")))
  )
)

procedure(rceDspfAnalyzerErrHandler(cid data)
  when(and(cid==rceDspfAnalyzerState['cid] data strlen(data)>0)
    warn("RCE DSPF analyzer: %s" data))
)

procedure(rceDspfAnalyzerCleanup(cid)
  let((window opened)
    when(or(!cid cid==rceDspfAnalyzerState['cid])
      window=rceDspfAnalyzerState['window]
      opened=rceDspfAnalyzerState['openedWindow]
      rceDspfClearHighlight()
      when(and(opened window windowp(window))
        errset(hiCloseWindow(window) t))
      rceDspfAnalyzerState['cid]=nil
      rceDspfAnalyzerState['buffer]=""
      rceDspfAnalyzerState['window]=nil
      rceDspfAnalyzerState['openedWindow]=nil))
  )

procedure(rceDspfAnalyzerPostFunc(cid exitStatus)
  when(cid==rceDspfAnalyzerState['cid]
    rceDspfAnalyzerCleanup(cid)
    info("<INFO> RCE DSPF analyzer exited with status %L.\n" exitStatus))
)

procedure(rceDspfAnalyzerStop()
  let((cid)
    cid=rceDspfAnalyzerState['cid]
    when(cid
      rceDspfAnalyzerCleanup(cid)
      when(ipcIsAliveProcess(cid)
        errset(ipcKillProcess(cid) t))))
)

procedure(rceDspfAnalyzerStart(source meta)
  prog((python entry environment command cid logFile)
    unless(and(source isFile(source) fileLength(source)>0)
      warn("RCE DSPF analyzer source is missing or empty: %L\n" source)
      return(nil))
    rceDspfAnalyzerStop()
    rceDspfAnalyzerState['layoutLib]=meta['layoutLib]
    rceDspfAnalyzerState['layoutCell]=meta['layoutCell]
    rceDspfAnalyzerState['layoutView]=meta['layoutView]
    rceDspfAnalyzerState['buffer]=""
    python=or(SICO_envValue("RCE_ANALYZER_PYTHON") RCE_pythonExe())
    entry=RCE_pythonEntry()
    environment=rceDspfLaunchEnvironment(nil)
    unless(environment return(nil))
    logFile=strcat(meta['runDir] "/log/dspf-analyzer.log")
    command=strcat(
      environment
      "RCE_DSPF_LAYOUT_LIB=" SICO_shellQuote(or(meta['layoutLib] "")) " "
      "RCE_DSPF_LAYOUT_CELL=" SICO_shellQuote(or(meta['layoutCell] "")) " "
      "RCE_DSPF_LAYOUT_VIEW=" SICO_shellQuote(or(meta['layoutView] "")) " "
      SICO_backendCommand("rce" python entry)
      " dspf-gui --bridge stdio " SICO_shellQuote(source))
    cid=ipcBeginProcess(command "" 'rceDspfAnalyzerDataHandler
      'rceDspfAnalyzerErrHandler 'rceDspfAnalyzerPostFunc logFile)
    unless(cid
      warn("RCE could not start the DSPF analyzer. Log: %s\n" logFile)
      return(nil))
    rceDspfAnalyzerState['cid]=cid
    rceDspfAnalyzerState['source]=source
    rceDspfAnalyzerState['logFile]=logFile
    info("<INFO> RCE DSPF analyzer started for %s.\n" source)
    return(cid)
  )
)

procedure(rceDspfAnalyzerRevision()
  "20260722.dspf.analyzer.v5"
)

rceDspfAnalyzerVersion="20260722.dspf.analyzer.v5"
)
