; Capture immutable-by-convention RCE run facts for the completion summary.

unless(and(boundp('RCEsummaryRunVersion)
             RCEsummaryRunVersion=="20260919.gui.modules.v1")
procedure(rceSummaryInputText(form)
  case(form~>inpType~>value
    ("OA"
      sprintf(nil "Schematic %s; Layout %s"
        rceSummaryLcv(form~>schLib~>value form~>schCell~>value
                       form~>schView~>value)
        rceSummaryLcv(form~>layLib~>value form~>layCell~>value
                       form~>layView~>value))
    )
    ("SCH+GDS"
      sprintf(nil "Schematic %s; GDS %s (cell %s)"
        rceSummaryLcv(form~>schLib~>value form~>schCell~>value
                       form~>schView~>value)
        rceSummaryPath(form~>gdsFile~>value)
        rceSummaryString(form~>gdsCell~>value))
    )
    ("CDL+LAY"
      sprintf(nil "CDL %s (cell %s); Layout %s"
        rceSummaryPath(form~>cdlFile~>value)
        rceSummaryString(form~>cdlCell~>value)
        rceSummaryLcv(form~>layLib~>value form~>layCell~>value
                       form~>layView~>value))
    )
    ("CDL+GDS"
      sprintf(nil "CDL %s (cell %s); GDS %s (cell %s)"
        rceSummaryPath(form~>cdlFile~>value)
        rceSummaryString(form~>cdlCell~>value)
        rceSummaryPath(form~>gdsFile~>value)
        rceSummaryString(form~>gdsCell~>value))
    )
    ("SVDB"
      sprintf(nil "%s (cell %s)"
        rceSummaryPath(form~>svdbDir~>value)
        rceSummaryString(form~>svdbCell~>value))
    )
    ("CCI"
      sprintf(nil "%s (cell %s)"
        rceSummaryPath(form~>cciDir~>value)
        rceSummaryString(form~>cciCell~>value))
    )
    (t rceSummaryString(form~>inpType~>value))
  )
)

procedure(rceSummaryViewTargetText(request)
  let((viewSpec requestedViewName viewName targets childRequest targetText)
    when(request
      case(car(request)
        ("native"
          rceSummaryLcv(nth(2 request) nth(3 request) nth(4 request))
        )
        ("text"
          viewSpec=when(isCallable('rceParasiticViewSpec)
                     rceParasiticViewSpec(nth(4 request)))
          requestedViewName=nth(6 request)
          viewName=or(when(and(stringp(requestedViewName)
                                strlen(requestedViewName)>0)
                        requestedViewName)
                      car(viewSpec) "parasitic")
          rceSummaryLcv(nth(1 request) nth(2 request) viewName)
        )
        ("group"
          targets=nil
          foreach(childRequest cdr(request)
            targetText=rceSummaryViewTargetText(childRequest)
            when(and(stringp(targetText) strlen(targetText)>0)
              targets=append1(targets targetText)))
          rceSummaryJoin(targets ", ")
        )
        (t "")
      )
    )
  )
)

procedure(rceSummaryCaptureRun(form runDir outputPaths outputType launchLog
                                 viewRequest @optional reducedOutputPaths)
  let((meta layoutBacked corners temperatures)
    meta=makeTable("rceSummaryRunMeta" nil)
    corners=rceSelectedProcessCorners(form)
    temperatures=rceSelectedCornerTemperatures(form)
    meta['tool]=form~>extTool~>value
    meta['inputType]=form~>inpType~>value
    meta['input]=rceSummaryInputText(form)
    meta['topCell]=form~>extractTopCell~>value
    meta['topCellSource]=form~>topCellSource~>value
    meta['nameSource]=form~>nameSource~>value
    meta['corner]=car(corners)
    meta['corners]=corners
    meta['rcType]=form~>rcType~>value
    meta['temperature]=car(temperatures)
    meta['temperatures]=temperatures
    meta['outputType]=outputType
    meta['runDir]=rceSummaryPath(runDir)
    meta['launchLog]=rceSummaryPath(launchLog)
    meta['fileOutput]=and(outputPaths car(outputPaths))
    meta['netlistPaths]=rceSummaryNormalizedPaths(outputPaths)
    meta['netlistPath]=car(meta['netlistPaths])
    meta['reducedNetlistPaths]=rceSummaryNormalizedPaths(reducedOutputPaths)
    meta['reducedNetlistPath]=car(meta['reducedNetlistPaths])
    meta['viewTarget]=rceSummaryViewTargetText(viewRequest)
    layoutBacked=member(form~>inpType~>value '("OA" "CDL+LAY"))
    meta['layoutLib]=if(layoutBacked then form~>layLib~>value else "")
    meta['layoutCell]=if(layoutBacked then form~>layCell~>value else "")
    meta['layoutView]=if(layoutBacked then form~>layView~>value else "")
    meta['startedAt]=getCurrentTime()
    meta
  )
)

procedure(rceSummaryIgnoredLvsMismatchP(meta)
  let((runDir)
    runDir=rceSummaryString(meta['runDir])
    and(strlen(runDir)>0
         rceSummaryFileReady(strcat(runDir "/log/lvs-ignored-mismatch")))
  )
)

procedure(rceSummaryFailureReason(meta exitStatus viewOk netlistReady
                                    reducedNetlistReady)
  let((exitFile reason netlistPath reducedNetlistPath path candidate)
    exitFile=strcat(meta['runDir] "/log/exit-abnormally")
    reason=rceSummaryFirstLine(exitFile)
    netlistPath=meta['netlistPath]
    foreach(candidate meta['netlistPaths]
      when(and(!path not(rceSummaryFileReady(candidate)))
        path=candidate
      )
    )
    when(path netlistPath=path)
    reducedNetlistPath=meta['reducedNetlistPath]
    path=nil
    foreach(candidate meta['reducedNetlistPaths]
      when(and(!path not(rceSummaryFileReady(candidate)))
        path=candidate
      )
    )
    when(path reducedNetlistPath=path)
    cond(
      (and(reason strlen(reason)>0) reason)
      (exitStatus!=0
        sprintf(nil "RCE process exited with status %L. See the launch log."
                 exitStatus))
      (not(viewOk) "Requested parasitic view generation failed.")
      (and(meta['fileOutput] not(netlistReady))
        sprintf(nil "Generated netlist is missing or empty: %s" netlistPath))
      (and(meta['reducedNetlistPaths] not(reducedNetlistReady))
        sprintf(nil "Reduced netlist is missing or empty: %s"
                reducedNetlistPath))
      (t "")
    )
  )
)

RCEsummaryRunVersion="20260919.gui.modules.v1"
)
