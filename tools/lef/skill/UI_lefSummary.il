; Native LEF completion summary. Keep this independent of Python/Qt versions.
unless(and(boundp('lefSummaryUiVersion)
             lefSummaryUiVersion=="20260901.summary.lifecycle.v2")
procedure(lefSummaryDisposeForm(form)
  let((deleted)
    when(and(form
             if(isCallable('hiIsForm) then hiIsForm(form) else t))
      ; hiDeleteForm is legal only after the form is unmapped and outside a
      ; callback.  Keep the handle if deletion fails so callers cannot turn a
      ; still-live form into a stale global reference.
      when(hiIsFormDisplayed(form)
        hiFormClose(form))
      unless(hiIsFormDisplayed(form)
        deleted=hiDeleteForm(form)))
    deleted
  )
)
when(and(boundp('lefSummaryForm) lefSummaryForm)
  when(lefSummaryDisposeForm(lefSummaryForm)
    lefSummaryForm=nil)
)
procedure(lefSummaryString(value)
  cond(
    (null(value) "")
    (stringp(value) value)
    (symbolp(value) get_pname(value))
    (t sprintf(nil "%L" value))
  )
)
procedure(lefSummaryHtmlEscape(value)
  let((source result index character)
    source=lefSummaryString(value)
    result=""
    for(index 1 strlen(source)
      character=substring(source index 1)
      result=strcat(result
        case(character
          ("&" "&amp;")
          ("<" "&lt;")
          (">" "&gt;")
          ("\"" "&quot;")
          (t character)))
    )
    result
  )
)
procedure(lefSummaryFileReady(path)
  and(path stringp(path) strlen(path)>0 isFile(path) fileLength(path)>0)
)
procedure(lefSummaryFirstLine(path)
  let((input line)
    when(and(path isFile(path))
      input=infile(path)
      when(input
        gets(line input)
        close(input)))
    or(line "")
  )
)
procedure(lefSummaryCellText(form)
  let((cellList inlineCells)
    cellList=lefCellListValue(form)
    inlineCells=lefInlineCellsValue()
    cond(
      (cellList strcat("Cell list: " simplifyFilename(cellList t)))
      (and(inlineCells length(inlineCells)>1)
        sprintf(nil "%L" inlineCells))
      (t form~>lefCell~>value)
    )
  )
)
procedure(lefSummaryCaptureRun(form runDir outputLef launchLog flowLogRequest)
  let((meta optionsFile)
    meta=makeTable("lefSummaryRunMeta" nil)
    meta['library]=form~>lefLib~>value
    meta['cells]=lefSummaryCellText(form)
    meta['layoutView]=form~>layoutView~>value
    meta['abstractView]=form~>abstractView~>value
    meta['bin]=form~>binName~>value
    optionsFile=form~>optionsFile~>value
    meta['optionsFile]=if(and(optionsFile optionsFile!="") then
      simplifyFilename(optionsFile t) else "")
    meta['runDir]=simplifyFilename(runDir t)
    meta['outputLef]=simplifyFilename(outputLef t)
    meta['launchLog]=simplifyFilename(launchLog t)
    meta['flowLogRequest]=flowLogRequest
    meta['startedAt]=getCurrentTime()
    meta
  )
)
procedure(lefSummaryFailureReason(meta exitStatus outputReady)
  let((reason)
    reason=lefSummaryFirstLine(strcat(meta['runDir] "/log/exit-abnormally"))
    cond(
      (and(reason strlen(reason)>0) reason)
      (exitStatus!=0
        sprintf(nil "LEF Generator exited with status %L. See the launch log."
                exitStatus))
      (not(outputReady)
        sprintf(nil "Generated LEF is missing or empty: %s" meta['outputLef]))
      (t "")
    )
  )
)
procedure(lefSummaryRow(label value)
  strcat("<tr><td valign=\"top\" width=\"135\"><b>"
          lefSummaryHtmlEscape(label)
          "</b></td><td><font face=\"monospace\">"
          lefSummaryHtmlEscape(value)
          "</font></td></tr>")
)
procedure(lefSummaryDetailsHtml(meta exitStatus success reason outputReady)
  let((html fileSize)
    fileSize=if(outputReady then
      sprintf(nil "%L bytes" fileLength(meta['outputLef])) else "")
    html="<table cellspacing=\"5\" cellpadding=\"2\">"
    foreach(row
      list(
        list("Library" meta['library])
        list("Cells" meta['cells])
        list("Layout View" meta['layoutView])
        list("Abstract View" meta['abstractView])
        list("Cell Bin" meta['bin])
        list("Options File" meta['optionsFile])
        list("LEF File" meta['outputLef])
        list("File Size" fileSize)
        list("Run Directory" meta['runDir])
        list("Launch Log" meta['launchLog])
        list("Started" meta['startedAt])
        list("Finished" getCurrentTime())
        list("Exit Status" sprintf(nil "%L" exitStatus)))
      when(and(cadr(row) strlen(lefSummaryString(cadr(row)))>0)
        html=strcat(html lefSummaryRow(car(row) cadr(row)))))
    html=strcat(html "</table>")
    unless(success
      html=strcat(html "<p><b>Failure:</b> <font color=\"#c62828\">"
                  lefSummaryHtmlEscape(reason) "</font></p>"))
    html
  )
)
procedure(lefSummaryEnsureForm()
  let((statusField detailsField layout openButton copyButton logButton)
    when(and(boundp('lefSummaryForm) lefSummaryForm
              if(isCallable('hiIsForm) then !hiIsForm(lefSummaryForm) else nil))
      lefSummaryForm=nil)
    unless(and(boundp('lefSummaryForm) lefSummaryForm
               if(isCallable('hiIsForm) then hiIsForm(lefSummaryForm) else t))
      openButton=concat("Open LEF File")
      copyButton=concat("Copy LEF File")
      logButton=concat("Open Log")
      statusField=hiCreateLabel(
        ?name 'lefSummaryStatus ?labelText " " ?justification 'left)
      detailsField=hiCreateHypertextField(
        ?name 'lefSummaryDetails ?value ""
        ?hasVerticalScrollbar t ?hasHorizontalScrollbar nil)
      layout=hiCreateVerticalBoxLayout(
        'lefSummaryLayout
        ?items list(statusField list(detailsField 'stretch 1))
        ?margins list(12 12 12 12) ?spacing 8)
      lefSummaryForm=hiCreateLayoutForm(
        'lefSummaryForm
        strcat(cond(
                 (isCallable('SICO_logo) SICO_logo())
                 (isCallable('witMenuLogo) witMenuLogo())
                 (t "SiCo"))
                "::LEF Generation Summary")
        layout
        ?buttonLayout list(
          'Empty
          list(openButton 'lefSummaryOpenCB)
          list(copyButton 'lefSummaryCopyCB)
          list(logButton 'lefSummaryOpenLogCB)
          list('Close 'hiFormClose))
        ?buttonDisabled list(openButton copyButton logButton)
        ?dialogStyle 'modeless
        ?initialSize list(760 480)
        ?minSize list(620 360)
        ?sizePolicy 'expanding))
    lefSummaryForm
  )
)
procedure(lefSummaryOpenCB(form)
  let((path command cid)
    path=form~>lefSummaryMeta['outputLef]
    if(lefSummaryFileReady(path) then
      command=strcat("runUserCmd gvim -- " SICO_shellQuote(path))
      cid=ipcBeginProcess(command "" nil nil nil)
      unless(cid warn("LEF cannot start gvim for output: %s\n" path))
      cid
    else
      warn("LEF output is missing or empty: %L\n" path))
  )
)
procedure(lefSummaryOpenLogCB(form)
  let((meta path requestId)
    meta=form~>lefSummaryMeta
    path=meta['launchLog]
    requestId=meta['flowLogRequest]
    cond(
      (and(requestId isCallable('GUI_flowLogShow)
            GUI_flowLogShow(requestId)) t)
      (and(path stringp(path) strlen(path)>0 isFile(path)
           isCallable('GUI_flowLogOpen))
        requestId=GUI_flowLogOpen("LEF" path)
        meta['flowLogRequest]=requestId
        requestId)
      (t
        warn("LEF launch log is unavailable: %L\n" path)
        nil))
  )
)
procedure(lefSummaryCopyFile(source destination)
  let((status)
    cond(
      (not(lefSummaryFileReady(source)) nil)
      (or(!destination destination=="" isDir(destination)) nil)
      (source==destination t)
      (t
        status=system(strcat("cp -f -- " SICO_shellQuote(source) " "
                             SICO_shellQuote(destination)))
        and(status==0 lefSummaryFileReady(destination)))
    )
  )
)
procedure(lefSummaryCopyCB(form)
  let((source parts fileName selection destination)
    source=form~>lefSummaryMeta['outputLef]
    if(lefSummaryFileReady(source) then
      parts=parseString(source "/")
      fileName=car(last(parts))
      selection=hiDisplayFileDialog(
        ?dialogName 'lefSummaryCopyDialog
        ?workingDir pwd()
        ?filter "LEF Files (*.lef);;All Files (*)"
        ?mode 'anyFile
        ?modal t
        ?defaultValue fileName
        ?caption "Copy LEF File"
        ?acceptMode 'save
        ?confirmOverwrite t)
      when(and(selection car(selection) cadr(selection))
        destination=simplifyFilename(strcat(car(selection) "/" cadr(selection)) t)
        if(lefSummaryCopyFile(source destination) then
          GUI_messageApp('i "LEF" strcat("Copied LEF file to:\n\t" destination))
        else
          GUI_messageApp('e "LEF" strcat("Cannot copy LEF file to:\n\t" destination))))
    else
      warn("LEF output is missing or empty: %L\n" source))
  )
)
procedure(lefSummaryDisplay(meta exitStatus)
  let((form outputReady success reason statusText details logReady displayed)
    outputReady=lefSummaryFileReady(meta['outputLef])
    success=and(exitStatus==0 outputReady)
    reason=lefSummaryFailureReason(meta exitStatus outputReady)
    form=lefSummaryEnsureForm()
    form~>lefSummaryMeta=meta
    form~>lefSummarySuccess=success
    statusText=if(success then
      "<b><font color=\"#188038\" size=5>LEF generation completed successfully</font></b>"
      else
      "<b><font color=\"#c62828\" size=5>LEF generation failed or is incomplete</font></b>")
    details=lefSummaryDetailsHtml(meta exitStatus success reason outputReady)
    form~>lefSummaryStatus~>labelText=statusText
    form~>lefSummaryDetails~>value=details
    logReady=and(meta['launchLog] stringp(meta['launchLog])
                 strlen(meta['launchLog])>0 isFile(meta['launchLog]))
    hiSetFormButtonEnabled(form concat("Open LEF File") success)
    hiSetFormButtonEnabled(form concat("Copy LEF File") success)
    hiSetFormButtonEnabled(form concat("Open Log") logReady)
    when(and(form
             if(isCallable('hiIsForm) then hiIsForm(form) else t)
             hiIsFormDisplayed(form))
      hiFormClose(form))
    hiDisplayForm(form)
    displayed=hiIsFormDisplayed(form)
    if(displayed then
      info("<INFO> LEF::Displayed generation summary.\n")
    else
      warn("<WARN> LEF::Could not display generation summary.\n"))
    and(success displayed)
  )
)
procedure(lefSummaryUiRevision()
  "20260901.summary.lifecycle.v2"
)
lefSummaryUiVersion="20260901.summary.lifecycle.v2"
)
