; Create and display the native RCE completion form.

unless(and(boundp('UIrceSummaryVersion)
             UIrceSummaryVersion=="20260919.gui.modules.v1")
procedure(rceSummaryFormValidP(form)
  and(form
      if(isCallable('hiIsForm) then hiIsForm(form) else t))
)

procedure(rceSummaryEnsureForm()
  let((statusField resultField detailsField layout openButton copyButton analyzeButton
        logButton)
    unless(rceSummaryFormValidP(when(boundp('rceSummaryForm) rceSummaryForm))
      openButton=concat("Open Netlist")
      copyButton=concat("Copy Netlist")
      analyzeButton=concat("Analyze DSPF")
      logButton=concat("Open Log")
      statusField=hiCreateLabel(
        ?name 'rceSummaryStatus
        ?labelText " "
        ?justification 'left)
      resultField=hiCreateComboField(
        ?name 'rceSummaryResult
        ?prompt "Result:"
        ?items list("")
        ?defValue ""
        ?editable nil
        ?callback "(rceSummaryResultCB hiGetCurrentForm())"
        ?invisible t)
      detailsField=hiCreateHypertextField(
        ?name 'rceSummaryDetails
        ?value ""
        ?hasVerticalScrollbar t
        ?hasHorizontalScrollbar nil)
      layout=hiCreateVerticalBoxLayout(
        'rceSummaryLayout
        ?items list(statusField resultField list(detailsField 'stretch 1))
        ?margins list(12 12 12 12)
        ?spacing 8)
      rceSummaryForm=hiCreateLayoutForm(
        'rceSummaryForm
        strcat(cond(
                 (isCallable('SICO_logo) SICO_logo())
                 (isCallable('witMenuLogo) witMenuLogo())
                 (t "SiCo"))
                "::RCE Extraction Summary")
        layout
        ?buttonLayout list(
          'Empty
          list(analyzeButton 'rceSummaryAnalyzeCB)
          list(openButton 'rceSummaryOpenCB)
          list(copyButton 'rceSummaryCopyCB)
          list(logButton 'rceSummaryOpenLogCB)
          list('Close 'hiFormClose))
        ?buttonDisabled list(analyzeButton openButton copyButton logButton)
        ?dialogStyle 'modeless
        ?initialSize list(780 510)
        ?minSize list(640 380)
        ?sizePolicy 'expanding)
    )
    rceSummaryForm
  )
)

procedure(rceSummaryDisplay(meta exitStatus viewOk)
  let((form netlistReady reducedNetlistReady reductionRequired success reason
        statusText details logReady displayed ignoredLvsMismatch outputRecords
        labels record)
    unless(meta['netlistPaths]
      meta['netlistPaths]=when(meta['netlistPath] list(meta['netlistPath]))
    )
    unless(meta['reducedNetlistPaths]
      meta['reducedNetlistPaths]=when(meta['reducedNetlistPath]
                                   list(meta['reducedNetlistPath])))
    netlistReady=rceSummaryPathsReady(meta['netlistPaths])
    reductionRequired=and(meta['reducedNetlistPaths] t)
    reducedNetlistReady=or(!reductionRequired
                            rceSummaryPathsReady(meta['reducedNetlistPaths]))
    ignoredLvsMismatch=rceSummaryIgnoredLvsMismatchP(meta)
    success=and(exitStatus==0 viewOk
                 or(not(meta['fileOutput])
                    and(netlistReady reducedNetlistReady)))
    reason=rceSummaryFailureReason(
      meta exitStatus viewOk netlistReady reducedNetlistReady)
    form=rceSummaryEnsureForm()
    when(rceSummaryFormValidP(form)
      form~>rceSummarySuccess=success
      form~>rceSummaryMeta=meta
      outputRecords=rceSummaryOutputRecords(meta)
      labels=nil
      foreach(record outputRecords
        labels=append1(labels car(record))
      )
      form~>rceSummaryResultRecords=outputRecords
      form~>rceSummaryResult~>items=or(labels list(""))
      form~>rceSummaryResult~>value=or(car(labels) "")
      form~>rceSummaryResult~>invisible=length(outputRecords)<=1
      statusText=cond(
        (and(ignoredLvsMismatch success)
          "<b><font color=\"#b26a00\" size=5>Output available with ignored LVS mismatch</font></b>")
        (ignoredLvsMismatch
          "<b><font color=\"#b26a00\" size=5>Ignored LVS mismatch; RCE run reported errors</font></b>")
        (success
          "<b><font color=\"#188038\" size=5>Extraction completed successfully</font></b>")
        (t
          "<b><font color=\"#c62828\" size=5>Extraction failed or is incomplete</font></b>")
      )
      details=rceSummaryDetailsHtml(
        meta exitStatus success reason netlistReady reducedNetlistReady)
      form~>rceSummaryStatus~>labelText=statusText
      form~>rceSummaryDetails~>value=details
      logReady=and(meta['launchLog] stringp(meta['launchLog])
                    strlen(meta['launchLog])>0 isFile(meta['launchLog]))
      form~>rceSummaryIgnoredLvsMismatch=ignoredLvsMismatch
      rceSummaryResultCB(form)
      hiSetFormButtonEnabled(form concat("Open Log") logReady)
      when(hiIsFormDisplayed(form)
        hiFormClose(form)
      )
      hiDisplayForm(form)
      displayed=hiIsFormDisplayed(form)
      if(displayed then
        info("<INFO> RCE::Displayed extraction summary.\n")
      else
        warn("<WARN> RCE::Could not display extraction summary.\n")
      )
      and(success not(ignoredLvsMismatch) displayed)
    )
  )
)

procedure(rceSummaryUiRevision()
  "20260911.flow.audit.v1"
)

when(and(boundp('rceSummaryForm) rceSummaryForm
          !rceSummaryFormValidP(rceSummaryForm))
  rceSummaryForm=nil)
procedure(rceSummaryModulesRevision()
  "20260919.gui.modules.v1"
)
rceSummaryUiVersion="20260911.flow.audit.v1"
UIrceSummaryVersion="20260919.gui.modules.v1"
)
