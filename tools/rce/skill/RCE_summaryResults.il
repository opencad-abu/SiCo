; Resolve summary result selection and action eligibility.

unless(and(boundp('RCEsummaryResultsVersion)
             RCEsummaryResultsVersion=="20260919.gui.modules.v1")
procedure(rceSummaryDspfOutputP(meta)
  member(lowerCase(rceSummaryString(meta['outputType])) '("dspf" "spf"))
)

procedure(rceSummaryAnalyzableP(meta success netlistReady)
  and(netlistReady
       success
       rceSummaryDspfOutputP(meta)
       isCallable('rceDspfAnalyzerStart))
)

procedure(rceSummaryAppendOutputRecords(records paths corners category
                                          categorized)
  let((index path detail label)
    for(index 0 sub1(length(paths))
      path=nth(index paths)
      detail=cond(
        (and(length(paths)==1 length(corners)>1) "All Corners")
        (index<length(corners) nth(index corners))
        (t car(last(parseString(path "/"))))
      )
      label=if(categorized then
        if(length(paths)==1 then category else strcat(category " - " detail))
      else
        detail)
      records=append1(records list(label path category))
    )
    records
  )
)

procedure(rceSummaryOutputRecords(meta)
  let((paths reducedPaths corners records categorized)
    paths=or(meta['netlistPaths]
             when(meta['netlistPath] list(meta['netlistPath])))
    reducedPaths=or(meta['reducedNetlistPaths]
                    when(meta['reducedNetlistPath]
                      list(meta['reducedNetlistPath])))
    corners=or(meta['corners]
               when(meta['corner] list(meta['corner])))
    records=nil
    categorized=and(paths reducedPaths)
    records=rceSummaryAppendOutputRecords(
      records paths corners "Netlist" categorized)
    records=rceSummaryAppendOutputRecords(
      records reducedPaths corners "Reduced Netlist" t)
    records
  )
)

procedure(rceSummarySelectedPath(form)
  let((selected result path record)
    selected=form~>rceSummaryResult~>value
    foreach(record form~>rceSummaryResultRecords
      when(and(!result car(record)==selected)
        result=record
      )
    )
    path=cadr(result)
    or(path "")
  )
)

procedure(rceSummaryResultCB(form)
  if(rceSummaryFormValidP(form) then
    let((path ready actionable analyzable)
      path=rceSummarySelectedPath(form)
      ready=rceSummaryFileReady(path)
      form~>rceNetlistPath=if(ready then path else "")
      actionable=and(form~>rceSummarySuccess ready)
      analyzable=rceSummaryAnalyzableP(
        form~>rceSummaryMeta form~>rceSummarySuccess ready)
      form~>rceSummaryActionable=actionable
      form~>rceSummaryAnalyzable=analyzable
      hiSetFormButtonEnabled(form concat("Analyze DSPF") analyzable)
      hiSetFormButtonEnabled(form concat("Open Netlist") actionable)
      hiSetFormButtonEnabled(form concat("Copy Netlist") actionable)
      path
    )
  else
    nil
  )
)

RCEsummaryResultsVersion="20260919.gui.modules.v1"
)
