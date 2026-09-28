; Execute RCE summary analysis, open, copy and log actions.

unless(and(boundp('RCEsummaryActionsVersion)
             RCEsummaryActionsVersion=="20260919.gui.modules.v1")
procedure(rceSummaryAnalyzeCB(form)
  if(rceSummaryFormValidP(form) then
    let((meta path analyzable)
      meta=form~>rceSummaryMeta
      path=form~>rceNetlistPath
      analyzable=rceSummaryAnalyzableP(
        meta form~>rceSummarySuccess rceSummaryFileReady(path))
      form~>rceSummaryAnalyzable=analyzable
      hiSetFormButtonEnabled(form concat("Analyze DSPF") analyzable)
      if(analyzable then
        rceDspfAnalyzerStart(path meta)
      else
        warn("RCE DSPF analyzer is unavailable for this extraction result.\n"))
    )
  else
    nil
  )
)

procedure(rceSummaryOpenLogCB(form)
  if(rceSummaryFormValidP(form) then
    let((meta path requestId)
      meta=form~>rceSummaryMeta
      path=when(meta meta['launchLog])
      requestId=when(meta meta['flowLogRequest])
      cond(
        (and(requestId isCallable('GUI_flowLogShow)
              GUI_flowLogShow(requestId))
          t
        )
        (and(path stringp(path) strlen(path)>0 isFile(path)
              isCallable('GUI_flowLogOpen))
          requestId=GUI_flowLogOpen("RCE" path)
          meta['flowLogRequest]=requestId
          requestId
        )
        (t
          warn("RCE launch log is unavailable: %L\n" path)
          nil
        )
      )
    )
  else
    nil
  )
)

procedure(rceSummaryOpenCB(form)
  if(rceSummaryFormValidP(form) then
    let((path command cid)
      path=form~>rceNetlistPath
      if(rceSummaryFileReady(path) then
        command=strcat("runUserCmd gvim -- " SICO_shellQuote(path))
        cid=ipcBeginProcess(command "" nil nil nil)
        unless(cid
          warn("RCE cannot start gvim for netlist: %s\n" path)
        )
        cid
      else
        warn("RCE netlist is missing or empty: %L\n" path)
      )
    )
  else
    nil
  )
)

procedure(rceSummaryCopyFile(source destination)
  let((sourcePath destinationPath status)
    sourcePath=rceSummaryPath(source)
    destinationPath=rceSummaryPath(destination)
    cond(
      (not(rceSummaryFileReady(sourcePath)) nil)
      (or(strlen(destinationPath)==0 isDir(destinationPath)) nil)
      (sourcePath==destinationPath t)
      (t
        status=system(strcat("cp -f -- " SICO_shellQuote(sourcePath) " "
                             SICO_shellQuote(destinationPath)))
        and(status==0 rceSummaryFileReady(destinationPath))
      )
    )
  )
)

procedure(rceSummaryCopyCB(form)
  if(rceSummaryFormValidP(form) then
    let((source parts fileName selection destination)
      source=form~>rceNetlistPath
      if(rceSummaryFileReady(source) then
        parts=parseString(source "/")
        fileName=car(last(parts))
        selection=hiDisplayFileDialog(
          ?dialogName 'rceSummaryCopyDialog
          ?workingDir pwd()
          ?filter "Netlist Files (*.dspf *.spf *.sp *.spef *.scs);;All Files (*)"
          ?mode 'anyFile
          ?modal t
          ?defaultValue fileName
          ?caption "Copy RCE Netlist"
          ?acceptMode 'save
          ?confirmOverwrite t)
        when(and(selection car(selection) cadr(selection))
          destination=simplifyFilename(strcat(car(selection) "/" cadr(selection)) t)
          if(rceSummaryCopyFile(source destination) then
            if(isCallable('GUI_messageApp) then
              GUI_messageApp('i "RCE" strcat("Copied netlist to:\n\t" destination))
            else
              info("<INFO> RCE::Copied netlist to %s\n" destination)
            )
          else
            if(isCallable('GUI_messageApp) then
              GUI_messageApp('e "RCE" strcat("Cannot copy netlist to:\n\t" destination))
            else
              warn("RCE cannot copy netlist to %s\n" destination)
            )
          )
        )
      else
        warn("RCE netlist is missing or empty: %L\n" source)
      )
    )
  else
    nil
  )
)

RCEsummaryActionsVersion="20260919.gui.modules.v1"
)
