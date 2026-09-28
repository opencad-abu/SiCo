; Render RCE completion details as escaped HTML.

unless(and(boundp('RCEsummaryHtmlVersion)
             RCEsummaryHtmlVersion=="20260919.gui.modules.v1")
procedure(rceSummaryHtmlEscape(value)
  let((source result index character)
    source=rceSummaryString(value)
    result=""
    for(index 1 strlen(source)
      character=substring(source index 1)
      result=strcat(result
        case(character
          ("&" "&amp;")
          ("<" "&lt;")
          (">" "&gt;")
          ("\"" "&quot;")
          (t character)
        )
      )
    )
    result
  )
)

procedure(rceSummaryRow(label value)
  strcat("<tr><td valign=\"top\" width=\"145\"><b>"
          rceSummaryHtmlEscape(label)
          "</b></td><td><font face=\"monospace\">"
          rceSummaryHtmlEscape(value)
          "</font></td></tr>")
)

procedure(rceSummaryDetailsHtml(meta exitStatus success reason netlistReady
                                  reducedNetlistReady)
  let((html outputTarget reducedOutputTarget fileSize reducedFileSize
        ignoredLvsMismatch)
    outputTarget=if(meta['fileOutput] then
      rceSummaryJoin(meta['netlistPaths] "\n")
                  else meta['viewTarget])
    ignoredLvsMismatch=rceSummaryIgnoredLvsMismatchP(meta)
    reducedOutputTarget=rceSummaryJoin(meta['reducedNetlistPaths] "\n")
    fileSize=rceSummaryPathSizes(meta['netlistPaths] netlistReady)
    reducedFileSize=rceSummaryPathSizes(
      meta['reducedNetlistPaths] reducedNetlistReady)
    html="<table cellspacing=\"5\" cellpadding=\"2\">"
    foreach(row
      list(
        list("Extractor" meta['tool])
        list("Input Type" meta['inputType])
        list("Input" meta['input])
        list("Top Cell" meta['topCell])
        list("Top Cell Source" meta['topCellSource])
        list("Use Name From" meta['nameSource])
        list("Process Corners" rceSummaryJoin(meta['corners] ", "))
        list("RC Type" meta['rcType])
        list("Temperatures" rceSummaryJoin(meta['temperatures] ", "))
        list("Output Type" meta['outputType])
        list(if(meta['fileOutput] then "Netlist" else "OA View") outputTarget)
        list("Reduced Netlist" reducedOutputTarget)
        list("File Size" fileSize)
        list("Reduced File Size" reducedFileSize)
        list("Run Directory" meta['runDir])
        list("Launch Log" meta['launchLog])
        list("Started" meta['startedAt])
        list("Finished" getCurrentTime())
        list("Exit Status" sprintf(nil "%L" exitStatus))
      )
      when(and(cadr(row) strlen(rceSummaryString(cadr(row)))>0)
        html=strcat(html rceSummaryRow(car(row) cadr(row)))
      )
    )
    html=strcat(html "</table>")
    when(ignoredLvsMismatch
      html=strcat(html "<p><b>Warning:</b> <font color=\"#b26a00\">"
                  "LVS reported INCORRECT and was explicitly ignored. Generated "
                  "output is for non-signoff debug only; review the launch log."
                  "</font></p>")
    )
    unless(success
      html=strcat(html "<p><b>Failure:</b> <font color=\"#c62828\">"
                  rceSummaryHtmlEscape(reason) "</font></p>")
    )
    html
  )
)

RCEsummaryHtmlVersion="20260919.gui.modules.v1"
)
