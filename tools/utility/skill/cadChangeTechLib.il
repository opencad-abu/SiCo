;==============================================================================
; Change instance masters in matching design cellViews from a mapping file.
;
; Mapping format (one row per mapping, whitespace separated):
;   sourceLib sourceCell sourceView targetLib targetCell targetView
;
; Blank lines and lines beginning with ';' or '#' are ignored.  A source
; field may be '*' to match any value or a full-name regular expression.
; Target cellViews must exist.
;==============================================================================

unless(and(boundp('sicoChangeTechLibCoreVersion)
             sicoChangeTechLibCoreVersion=="20260811.mapping.v7"
             isCallable('cadChangeTechLib)
             isCallable('cadChangeTechLibMatchingCellNames)
             isCallable('cadChangeTechLibReadMappingFile))

unless(boundp('cadChangeTechLibLastReport)
  cadChangeTechLibLastReport=nil
)

procedure(cadChangeTechLibRowField(row index)
  nth(index row)
)

procedure(cadChangeTechLibBlankP(value)
  or(value==nil value=="")
)

procedure(cadChangeTechLibMappingRowP(row)
  and(row length(row)==6)
)

procedure(cadChangeTechLibReadMappingFile(mappingfile)
  let((port line fields mappings errors lineNumber first)
    mappings=nil
    errors=nil
    lineNumber=0
    port=infile(mappingfile)
    if(!port then
      list(nil list(sprintf(nil
        "Cannot open mapping file: %s" mappingfile)))
    else
      while(gets(line port)
        lineNumber=lineNumber+1
        fields=parseString(line " \t\r\n")
        when(fields
          first=car(fields)
          if(rexMatchp("^[#;]" first) then
            nil
          else
            if(length(fields)==6 then
              mappings=cons(fields mappings)
            else
              errors=cons(sprintf(nil
                "%s:%d: expected 6 fields, got %d"
                mappingfile lineNumber length(fields)) errors)
            )
          )
        )
      )
      close(port)
      list(reverse(mappings) reverse(errors))
    )
  )
)

procedure(cadChangeTechLibWriteMappingFile(mappingfile mappings)
  let((port ok)
    port=outfile(mappingfile "w")
    if(!port then
      warn("[cadChangeTechLib] Cannot write mapping file: %s\n" mappingfile)
      nil
    else
      fprintf(port "; cadChangeTechLib mapping file v1\n")
      fprintf(port "; sourceLib sourceCell sourceView targetLib targetCell targetView\n")
      foreach(row mappings
        when(cadChangeTechLibMappingRowP(row)
          fprintf(port "%s\t%s\t%s\t%s\t%s\t%s\n"
            cadChangeTechLibRowField(row 0)
            cadChangeTechLibRowField(row 1)
            cadChangeTechLibRowField(row 2)
            cadChangeTechLibRowField(row 3)
            cadChangeTechLibRowField(row 4)
            cadChangeTechLibRowField(row 5))
        )
      )
      ok=close(port)
      ok
    )
  )
)

procedure(cadChangeTechLibValidateMappings(mappings)
  let((errors index row sourceLib sourceCell sourceView targetLib targetCell targetView)
    errors=nil
    index=0
    foreach(row mappings
      index=index+1
      if(!cadChangeTechLibMappingRowP(row) then
        errors=cons(sprintf(nil "mapping row %d is malformed" index) errors)
      else
        sourceLib=cadChangeTechLibRowField(row 0)
        sourceCell=cadChangeTechLibRowField(row 1)
        sourceView=cadChangeTechLibRowField(row 2)
        targetLib=cadChangeTechLibRowField(row 3)
        targetCell=cadChangeTechLibRowField(row 4)
        targetView=cadChangeTechLibRowField(row 5)
        when(or(cadChangeTechLibBlankP(sourceLib)
                cadChangeTechLibBlankP(sourceCell)
                cadChangeTechLibBlankP(sourceView))
          errors=cons(sprintf(nil "mapping row %d has an empty source field" index)
                      errors)
        )
        when(and(!cadChangeTechLibBlankP(sourceLib)
                 !cadChangeTechLibPatternValidP(sourceLib))
          errors=cons(sprintf(nil
            "mapping row %d source library pattern is invalid: %s"
            index sourceLib) errors)
        )
        when(and(!cadChangeTechLibBlankP(sourceCell)
                 !cadChangeTechLibPatternValidP(sourceCell))
          errors=cons(sprintf(nil
            "mapping row %d source cell pattern is invalid: %s"
            index sourceCell) errors)
        )
        when(and(!cadChangeTechLibBlankP(sourceView)
                 !cadChangeTechLibPatternValidP(sourceView))
          errors=cons(sprintf(nil
            "mapping row %d source view pattern is invalid: %s"
            index sourceView) errors)
        )
        when(or(cadChangeTechLibBlankP(targetLib)
                cadChangeTechLibBlankP(targetCell)
                cadChangeTechLibBlankP(targetView))
          errors=cons(sprintf(nil "mapping row %d has an empty target field" index)
                      errors)
        )
        when(and(!cadChangeTechLibBlankP(targetLib)
                 !cadChangeTechLibBlankP(targetCell)
                 !cadChangeTechLibBlankP(targetView)
                 !ddGetObj(targetLib targetCell targetView))
          errors=cons(sprintf(nil
            "mapping row %d target %s/%s/%s does not exist"
            index targetLib targetCell targetView) errors)
        )
      )
    )
    reverse(errors)
  )
)

procedure(cadChangeTechLibPatternMatch(value pattern)
  let((protected)
    if(pattern=="*" then
      t
    else
      if(or(cadChangeTechLibBlankP(value)
            cadChangeTechLibBlankP(pattern)) then
        nil
      else
        protected=errset(
          rexMatchp(strcat("^" pattern "$") value) nil)
        and(protected car(protected))
      )
    )
  )
)

procedure(cadChangeTechLibPatternValidP(pattern)
  let((protected)
    if(cadChangeTechLibBlankP(pattern) then
      nil
    else
      if(pattern=="*" then
        t
      else
        protected=errset(rexMatchp(strcat("^" pattern "$") "") nil)
        if(protected then t else nil)
      )
    )
  )
)

procedure(cadChangeTechLibMatchingCellNames(
           designLib designCellPattern designView)
  let((libObj cellNames)
    cellNames=nil
    libObj=if(cadChangeTechLibBlankP(designLib)
      then nil else ddGetObj(designLib))
    when(libObj
      foreach(cell libObj~>cells
        when(and(cadChangeTechLibPatternMatch(
                   cell~>name designCellPattern)
                 member(designView cell~>views~>name))
          cellNames=cons(cell~>name cellNames)
        )
      )
    )
    reverse(cellNames)
  )
)

procedure(cadChangeTechLibFindMapping(libName cellName viewName mappings)
  let((found)
    found=nil
    foreach(row mappings
      when(and(!found
               cadChangeTechLibMappingRowP(row)
               cadChangeTechLibPatternMatch(
                 libName cadChangeTechLibRowField(row 0))
               cadChangeTechLibPatternMatch(
                 cellName cadChangeTechLibRowField(row 1))
               cadChangeTechLibPatternMatch(
                 viewName cadChangeTechLibRowField(row 2)))
        found=row
      )
    )
    found
  )
)

procedure(cadChangeTechLibResolveHeader(cellView instH)
  let((candidate master superMaster)
    candidate=instH
    master=if(and(instH~>master dbValidP(instH~>master)) then
              instH~>master else nil)
    superMaster=if(and(master dbValidP(master)) then
                   master~>superMaster else nil)
    when(and(!instH~>variants superMaster)
      candidate=car(setof(iH cellView~>instHeaders
        and(dbValidP(iH)
            iH~>variants
            iH~>master==superMaster)))
      unless(candidate
        candidate=car(setof(iH cellView~>instHeaders
          and(dbValidP(iH)
            iH~>variants
            iH~>libName==instH~>libName
            iH~>cellName==instH~>cellName
            iH~>viewName==instH~>viewName)))
      )
      unless(candidate
        candidate=car(setof(iH cellView~>instHeaders
          and(dbValidP(iH)
            iH~>variants
            iH~>libName==instH~>libName
            iH~>cellName==instH~>cellName)))
      )
      unless(candidate
        candidate=instH
      )
    )
    candidate
  )
)

procedure(cadChangeTechLibHeaderKey(instH)
  sprintf(nil "%s|%s|%s"
    if(instH~>libName then instH~>libName else "")
    if(instH~>cellName then instH~>cellName else "")
    if(instH~>viewName then instH~>viewName else ""))
)

procedure(cadChangeTechLibSetMaster(instH mapping)
  let((targetLib targetCell targetView result)
    targetLib=cadChangeTechLibRowField(mapping 3)
    targetCell=cadChangeTechLibRowField(mapping 4)
    targetView=cadChangeTechLibRowField(mapping 5)
    result=nil
    if(isCallable('dbSetInstHeaderMasterName) then
      result=errset(
        dbSetInstHeaderMasterName(instH targetLib targetCell targetView)
        nil)
    else
      ; Compatibility fallback for older Virtuoso releases.
      result=errset(
        progn(
          instH~>libName=targetLib
          instH~>cellName=targetCell
          instH~>viewName=targetView
          t)
        nil)
    )
    if(result then car(result) else nil)
  )
)

procedure(cadChangeTechLibPrintReport(report)
  let((success changed matched errors)
    success=cadr(member('success report))
    changed=cadr(member('changed report))
    matched=cadr(member('matched report))
    errors=cadr(member('errors report))
    printf("[cadChangeTechLib] matched=%d changed=%d success=%L\n"
      matched changed success)
    foreach(error errors
      warn("[cadChangeTechLib] %s\n" error)
    )
    report
  )
)

procedure(cadChangeTechLibProcessCell(
           designLib designCell designView mappings)
  let((errors cellView
        instHeaders processedKeys changedCount matchedCount instH targetH
        sourceLib sourceCell sourceView mapping key currentSame saveResult
        schematicResult report)
    errors=nil
    changedCount=0
    matchedCount=0
    processedKeys=nil

    cellView=dbOpenCellViewByType(designLib designCell designView nil "a")
    if(!cellView then
      errors=list(sprintf(nil "Cannot open design cellView for edit: %s/%s/%s"
                         designLib designCell designView))
    else
      printf("[cadChangeTechLib] Processing %s/%s/%s with %d mapping row(s)\n"
        designLib designCell designView length(mappings))
      instHeaders=foreach(header cellView~>instHeaders header)
      foreach(instH instHeaders
        when(dbValidP(instH)
          targetH=cadChangeTechLibResolveHeader(cellView instH)
          when(dbValidP(targetH)
            key=cadChangeTechLibHeaderKey(targetH)
            unless(member(key processedKeys)
              processedKeys=cons(key processedKeys)
              sourceLib=if(targetH~>libName then targetH~>libName else "")
              sourceCell=if(targetH~>cellName then targetH~>cellName else "")
              sourceView=if(targetH~>viewName then targetH~>viewName else "")
              mapping=cadChangeTechLibFindMapping(
                sourceLib sourceCell sourceView mappings)
              when(mapping
                matchedCount=matchedCount+1
                currentSame=and(sourceLib==cadChangeTechLibRowField(mapping 3)
                                sourceCell==cadChangeTechLibRowField(mapping 4)
                                sourceView==cadChangeTechLibRowField(mapping 5))
                unless(currentSame
                  if(cadChangeTechLibSetMaster(targetH mapping) then
                    changedCount=changedCount+1
                    printf("[cadChangeTechLib] %s: %s/%s/%s -> %s/%s/%s\n"
                      key sourceLib sourceCell sourceView
                      cadChangeTechLibRowField(mapping 3)
                      cadChangeTechLibRowField(mapping 4)
                      cadChangeTechLibRowField(mapping 5))
                  else
                    errors=append1(errors sprintf(nil
                      "Failed to set master for %s in %s/%s/%s"
                      key designLib designCell designView))
                  )
                )
              )
            )
          )
        )
      )

      when(and(cellView~>cellViewType=="schematic" changedCount>0)
        schematicResult=errset(schCheck(cellView) nil)
        if(!schematicResult then
          errors=append1(errors sprintf(nil
            "schCheck failed for %s/%s/%s"
            designLib designCell designView))
        else
          when(car(schematicResult)
            errors=append1(errors sprintf(nil
              "schCheck reported errors for %s/%s/%s: %L"
              designLib designCell designView car(schematicResult)))
          )
        )
      )
      saveResult=errset(dbSave(cellView) nil)
      unless(and(saveResult car(saveResult))
        errors=append1(errors sprintf(nil "dbSave failed for %s/%s/%s"
          designLib designCell designView))
      )
      dbClose(cellView)
    )
    report=list('success null(errors) 'changed changedCount
                'matched matchedCount 'errors errors)
    report
  )
)

procedure(cadChangeTechLib(designLib designCell designView mappingfile)
  let((readResult mappings parseErrors validationErrors errors cellNames
        cellName cellReport changedCount matchedCount report)
    errors=nil
    changedCount=0
    matchedCount=0

    if(or(cadChangeTechLibBlankP(designLib)
          cadChangeTechLibBlankP(designCell)
          cadChangeTechLibBlankP(designView)
          cadChangeTechLibBlankP(mappingfile)) then
      errors=list("Design library, cell pattern, view, and mapping file are required")
    else
      unless(ddGetObj(designLib)
        errors=append1(errors sprintf(nil
          "Design library does not exist: %s" designLib))
      )
      unless(cadChangeTechLibPatternValidP(designCell)
        errors=append1(errors sprintf(nil
          "Design cell pattern is invalid: %s" designCell))
      )
      readResult=cadChangeTechLibReadMappingFile(mappingfile)
      mappings=car(readResult)
      parseErrors=cadr(readResult)
      errors=append(errors parseErrors)
      when(!mappings
        errors=append1(errors "Mapping file contains no mapping rows")
      )
      validationErrors=cadChangeTechLibValidateMappings(mappings)
      errors=append(errors validationErrors)
    )

    unless(errors
      cellNames=cadChangeTechLibMatchingCellNames(
        designLib designCell designView)
      unless(cellNames
        errors=append1(errors sprintf(nil
          "No design cellViews match %s/%s/%s"
          designLib designCell designView))
      )
    )
    unless(errors
      foreach(cellName cellNames
        cellReport=cadChangeTechLibProcessCell(
          designLib cellName designView mappings)
        changedCount=changedCount+
          cadr(member('changed cellReport))
        matchedCount=matchedCount+
          cadr(member('matched cellReport))
        errors=append(errors cadr(member('errors cellReport)))
      )
    )
    report=list('success null(errors) 'changed changedCount
                'matched matchedCount 'errors errors)
    cadChangeTechLibLastReport=report
    cadChangeTechLibPrintReport(report)
    report
  )
)

procedure(cadChangeTechLibCoreRevision()
  sicoChangeTechLibCoreVersion
)

sicoChangeTechLibCoreVersion="20260811.mapping.v7"
)

unless(and(boundp('cadChangeTechLibViaVersion)
           cadChangeTechLibViaVersion=="20260814.via.v6"
           isCallable('cadChangeTechLibReplaceViaMasters))
  cond(
    (and(isCallable('cadLoadRelative)
         cadLoadRelative("utility/skill/cadChangeTechLibVia.il"))
      t)
    (and(isCallable('witMenuLoadRelative)
         witMenuLoadRelative("utility/skill/cadChangeTechLibVia.il"))
      t)
    (and(isFile("utility/skill/cadChangeTechLibVia.il")
         errset(load("utility/skill/cadChangeTechLibVia.il") nil))
      t)
    (and(isCallable('SICO_installationRoot)
      let((viaPath)
        viaPath=strcat(SICO_installationRoot()
                       "/tools/utility/skill/cadChangeTechLibVia.il")
        when(isFile(viaPath)
          errset(load(viaPath) nil))))
      t)
    (t
      warn("cadChangeTechLib via support could not be loaded.\n"))
  )
)
