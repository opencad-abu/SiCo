;==============================================================================
; Change standard via masters in matching layout cellViews.
;
; Mapping format (one row per mapping, whitespace separated):
;   sourceLib sourceViaDef targetLib targetViaDef
;==============================================================================

unless(and(boundp('cadChangeTechLibViaVersion)
             cadChangeTechLibViaVersion=="20260814.via.v6"
             isCallable('cadChangeTechLibReplaceViaMasters)
             isCallable('cadChangeTechLibReplaceViaMastersInCell)
             isCallable('cadChangeTechLibReadViaMappingFile))

procedure(cadChangeTechLibViaRowField(row index)
  nth(index row)
)

procedure(cadChangeTechLibViaBlankP(value)
  or(value==nil value=="")
)

procedure(cadChangeTechLibViaPatternMatch(value pattern)
  let((protected)
    if(pattern=="*" then
      t
    else
      if(or(cadChangeTechLibViaBlankP(value)
            cadChangeTechLibViaBlankP(pattern)) then
        nil
      else
        protected=errset(
          rexMatchp(strcat("^" pattern "$") value) nil)
        and(protected car(protected))
      )
    )
  )
)

procedure(cadChangeTechLibViaPatternValidP(pattern)
  let((protected)
    if(cadChangeTechLibViaBlankP(pattern) then
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

procedure(cadChangeTechLibViaMatchingCellNames(
           designLib designCellPattern designView)
  let((libObj cellNames)
    cellNames=nil
    libObj=if(cadChangeTechLibViaBlankP(designLib)
      then nil else ddGetObj(designLib))
    when(libObj
      foreach(cell libObj~>cells
        when(and(cadChangeTechLibViaPatternMatch(
                   cell~>name designCellPattern)
                 member(designView cell~>views~>name))
          cellNames=cons(cell~>name cellNames)
        )
      )
    )
    reverse(cellNames)
  )
)

unless(boundp('cadChangeTechLibLastViaReport)
  cadChangeTechLibLastViaReport=nil
)

procedure(cadChangeTechLibViaMappingRowP(row)
  and(row length(row)==4)
)

procedure(cadChangeTechLibReadViaMappingFile(mappingfile)
  let((port line fields mappings errors lineNumber first)
    mappings=nil
    errors=nil
    lineNumber=0
    port=infile(mappingfile)
    if(!port then
      list(nil list(sprintf(nil
        "Cannot open via mapping file: %s" mappingfile)))
    else
      while(gets(line port)
        lineNumber=lineNumber+1
        fields=parseString(line " \t\r\n")
        when(fields
          first=car(fields)
          if(rexMatchp("^[#;]" first) then
            nil
          else
            if(length(fields)==4 then
              mappings=cons(fields mappings)
            else
              errors=cons(sprintf(nil
                "%s:%d: expected 4 fields, got %d"
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

procedure(cadChangeTechLibWriteViaMappingFile(mappingfile mappings)
  let((port ok)
    port=outfile(mappingfile "w")
    if(!port then
      warn("[cadChangeTechLib] Cannot write via mapping file: %s\n"
           mappingfile)
      nil
    else
      fprintf(port "; cadChangeTechLib via mapping file v1\n")
      fprintf(port "; sourceLib sourceViaDef targetLib targetViaDef\n")
      foreach(row mappings
        when(cadChangeTechLibViaMappingRowP(row)
          fprintf(port "%s\t%s\t%s\t%s\n"
            cadChangeTechLibViaRowField(row 0)
            cadChangeTechLibViaRowField(row 1)
            cadChangeTechLibViaRowField(row 2)
            cadChangeTechLibViaRowField(row 3))
        )
      )
      ok=close(port)
      ok
    )
  )
)

procedure(cadChangeTechLibTechFileForLib(libName)
  let((libObj protected)
    libObj=if(cadChangeTechLibViaBlankP(libName) then nil else ddGetObj(libName))
    protected=if(libObj then errset(techGetTechFile(libObj) nil) else nil)
    if(protected then car(protected) else nil)
  )
)

procedure(cadChangeTechLibViaDefForLib(libName viaDefName)
  let((techId protected)
    techId=cadChangeTechLibTechFileForLib(libName)
    protected=if(techId then
      errset(techFindViaDefByName(techId viaDefName) nil)
      else nil)
    if(protected then car(protected) else nil)
  )
)

procedure(cadChangeTechLibValidateViaMappings(mappings)
  let((errors index row sourceLib sourceViaDef targetLib targetViaDef
        targetTech targetDef)
    errors=nil
    index=0
    foreach(row mappings
      index=index+1
      if(!cadChangeTechLibViaMappingRowP(row) then
        errors=cons(sprintf(nil "via mapping row %d is malformed" index) errors)
      else
        sourceLib=cadChangeTechLibViaRowField(row 0)
        sourceViaDef=cadChangeTechLibViaRowField(row 1)
        targetLib=cadChangeTechLibViaRowField(row 2)
        targetViaDef=cadChangeTechLibViaRowField(row 3)
        when(or(cadChangeTechLibViaBlankP(sourceLib)
                cadChangeTechLibViaBlankP(sourceViaDef))
          errors=cons(sprintf(nil
            "via mapping row %d has an empty source field" index) errors)
        )
        when(and(!cadChangeTechLibViaBlankP(sourceLib)
                 !cadChangeTechLibViaPatternValidP(sourceLib))
          errors=cons(sprintf(nil
            "via mapping row %d source library pattern is invalid: %s"
            index sourceLib) errors)
        )
        when(and(!cadChangeTechLibViaBlankP(sourceViaDef)
                 !cadChangeTechLibViaPatternValidP(sourceViaDef))
          errors=cons(sprintf(nil
            "via mapping row %d source ViaDef pattern is invalid: %s"
            index sourceViaDef) errors)
        )
        when(or(cadChangeTechLibViaBlankP(targetLib)
                cadChangeTechLibViaBlankP(targetViaDef))
          errors=cons(sprintf(nil
            "via mapping row %d has an empty target field" index) errors)
        )
        when(and(!cadChangeTechLibViaBlankP(targetLib)
                 !cadChangeTechLibViaBlankP(targetViaDef))
          targetTech=cadChangeTechLibTechFileForLib(targetLib)
          targetDef=if(and(targetTech targetViaDef!="*") then
            cadChangeTechLibViaDefForLib(targetLib targetViaDef) else nil)
          if(!targetTech then
            errors=cons(sprintf(nil
              "via mapping row %d target library %s has no technology file"
              index targetLib) errors)
          else
            unless(or(targetViaDef=="*" targetDef)
              errors=cons(sprintf(nil
                "via mapping row %d target %s/%s does not exist"
                index targetLib targetViaDef) errors)
            )
          )
        )
      )
    )
    reverse(errors)
  )
)

procedure(cadChangeTechLibFindViaMapping(libName viaDefName mappings)
  let((found)
    found=nil
    foreach(row mappings
      when(and(!found
               cadChangeTechLibViaMappingRowP(row)
               cadChangeTechLibViaPatternMatch(
                 libName cadChangeTechLibViaRowField(row 0))
               cadChangeTechLibViaPatternMatch(
                 viaDefName cadChangeTechLibViaRowField(row 1)))
        found=row
      )
    )
    found
  )
)

procedure(cadChangeTechLibViaSourceLib(viaHeader)
  let((master viaDef techId techDdId libName protected)
    libName=nil
    protected=errset(viaHeader~>master nil)
    master=if(protected then car(protected) else nil)
    when(and(master dbValidP(master))
      protected=errset(master~>libName nil)
      when(protected libName=car(protected))
    )
    unless(libName
      protected=errset(viaHeader~>viaDef nil)
      viaDef=if(protected then car(protected) else nil)
      when(and(viaDef dbValidP(viaDef))
        protected=errset(viaDef~>libName nil)
        when(protected libName=car(protected))
        unless(libName
          protected=errset(viaDef~>techFile nil)
          techId=if(protected then car(protected) else nil)
          protected=if(techId then
            errset(techGetTechFileDdId(techId) nil) else nil)
          techDdId=if(protected then car(protected) else nil)
          protected=if(techDdId then
            errset(techGetTechLibName(techDdId) nil) else nil)
          when(protected libName=car(protected))
        )
      )
    )
    libName
  )
)

procedure(cadChangeTechLibViaDefName(viaHeader)
  let((protected)
    protected=errset(viaHeader~>viaDefName nil)
    if(and(protected car(protected)) then car(protected) else "")
  )
)

procedure(cadChangeTechLibLayoutViewP(viewName cellView)
  if(or(member(viewName
               list("layout" "maskLayout" "maskLayoutXL" "maskLayoutEXL"))
        and(cellView dbValidP(cellView)
            member(cellView~>cellViewType
                   list("layout" "maskLayout" "maskLayoutXL" "maskLayoutEXL"))))
    then t else nil)
)

procedure(cadChangeTechLibViaReport(success changed matched errors)
  list('success success 'changed changed 'matched matched 'errors errors)
)

procedure(cadChangeTechLibReplaceViaMastersInCell(
           designLib designCell designView mappings)
  let((errors cellView
        viaObjects via viaHeader sourceLib sourceViaDef mapping targetLib
        targetViaDef targetDef origin orient overrideParams createResult
        newVia deleteResult changedCount matchedCount saveResult report)
    changedCount=0
    matchedCount=0
    errors=nil

    cellView=dbOpenCellViewByType(designLib designCell designView nil "a")
    if(!cellView then
      errors=list(sprintf(nil
        "Cannot open layout cellView for via update: %s/%s/%s"
        designLib designCell designView))
    else
      if(!cadChangeTechLibLayoutViewP(designView cellView) then
        errors=list(sprintf(nil
          "Via master update requires a layout cellView: %s/%s/%s"
          designLib designCell designView))
      else
        printf("[cadChangeTechLib] Processing %s/%s/%s with %d via mapping row(s)\n"
          designLib designCell designView length(mappings))
        viaObjects=foreach(mapcar item cellView~>vias item)
        foreach(via viaObjects
          when(and(dbValidP(via)
                   via~>viaHeader
                   dbValidP(via~>viaHeader))
            viaHeader=via~>viaHeader
            sourceLib=or(cadChangeTechLibViaSourceLib(viaHeader) "")
            sourceViaDef=cadChangeTechLibViaDefName(viaHeader)
            mapping=cadChangeTechLibFindViaMapping(
              sourceLib sourceViaDef mappings)
            when(mapping
              matchedCount=matchedCount+1
              targetLib=cadChangeTechLibViaRowField(mapping 2)
              targetViaDef=cadChangeTechLibViaRowField(mapping 3)
              when(targetViaDef=="*"
                targetViaDef=sourceViaDef
              )
              unless(and(sourceLib==targetLib sourceViaDef==targetViaDef)
                targetDef=cadChangeTechLibViaDefForLib(targetLib targetViaDef)
                if(!targetDef then
                  errors=append1(errors sprintf(nil
                    "Target ViaDef %s/%s does not exist for source %s/%s in %s/%s/%s"
                    targetLib targetViaDef sourceLib sourceViaDef
                    designLib designCell designView))
                else
                  origin=via~>origin
                  orient=via~>orient
                  overrideParams=viaHeader~>overrideParams
                  createResult=if(overrideParams
                    then errset(dbCreateVia(cellView targetDef origin orient
                                            overrideParams) nil)
                    else errset(dbCreateVia(
                                  cellView targetDef origin orient) nil))
                  newVia=if(createResult then car(createResult) else nil)
                  if(newVia then
                    deleteResult=errset(dbDeleteObject(via) nil)
                    if(and(deleteResult car(deleteResult)) then
                      changedCount=changedCount+1
                      printf("[cadChangeTechLib] via %s/%s -> %s/%s at %L\n"
                        sourceLib sourceViaDef targetLib targetViaDef origin)
                    else
                      errset(dbDeleteObject(newVia) nil)
                      errors=append1(errors sprintf(nil
                        "Failed to delete source via %s/%s at %L in %s/%s/%s"
                        sourceLib sourceViaDef origin
                        designLib designCell designView))
                    )
                  else
                    errors=append1(errors sprintf(nil
                      "Failed to create target via %s/%s at %L in %s/%s/%s; verify the design technology graph and override parameters"
                      targetLib targetViaDef origin
                      designLib designCell designView))
                  )
                )
              )
            )
          )
        )
        saveResult=errset(dbSave(cellView) nil)
        unless(and(saveResult car(saveResult))
          errors=append1(errors sprintf(nil
            "dbSave failed after via master update for %s/%s/%s"
            designLib designCell designView))
        )
      )
      dbClose(cellView)
    )
    report=cadChangeTechLibViaReport(null(errors) changedCount
                                     matchedCount errors)
    report
  )
)

procedure(cadChangeTechLibReplaceViaMasters(
           designLib designCell designView viaMappingFile)
  let((readResult mappings parseErrors validationErrors errors cellNames
        cellName cellReport changedCount matchedCount report)
    changedCount=0
    matchedCount=0
    errors=nil

    if(or(cadChangeTechLibViaBlankP(designLib)
          cadChangeTechLibViaBlankP(designCell)
          cadChangeTechLibViaBlankP(designView)
          cadChangeTechLibViaBlankP(viaMappingFile)) then
      errors=list(
        "Design library, cell pattern, view, and via mapping file are required")
    else
      unless(ddGetObj(designLib)
        errors=append1(errors sprintf(nil
          "Design library does not exist: %s" designLib))
      )
      unless(cadChangeTechLibViaPatternValidP(designCell)
        errors=append1(errors sprintf(nil
          "Design cell pattern is invalid: %s" designCell))
      )
      readResult=cadChangeTechLibReadViaMappingFile(viaMappingFile)
      mappings=car(readResult)
      parseErrors=cadr(readResult)
      errors=append(errors parseErrors)
      when(!mappings
        errors=append1(errors "Via mapping file contains no mapping rows")
      )
      validationErrors=cadChangeTechLibValidateViaMappings(mappings)
      errors=append(errors validationErrors)
    )

    unless(errors
      cellNames=cadChangeTechLibViaMatchingCellNames(
        designLib designCell designView)
      unless(cellNames
        errors=append1(errors sprintf(nil
          "No design cellViews match %s/%s/%s"
          designLib designCell designView))
      )
    )
    unless(errors
      foreach(cellName cellNames
        cellReport=cadChangeTechLibReplaceViaMastersInCell(
          designLib cellName designView mappings)
        changedCount=changedCount+
          cadr(member('changed cellReport))
        matchedCount=matchedCount+
          cadr(member('matched cellReport))
        errors=append(errors cadr(member('errors cellReport)))
      )
    )
    report=cadChangeTechLibViaReport(null(errors) changedCount
                                     matchedCount errors)
    cadChangeTechLibLastViaReport=report
    printf("[cadChangeTechLib] via matched=%d changed=%d success=%L\n"
      matchedCount changedCount cadr(member('success report)))
    foreach(error errors
      warn("[cadChangeTechLib] %s\n" error)
    )
    report
  )
)

procedure(cadChangeTechLibViaRevision()
  cadChangeTechLibViaVersion
)

cadChangeTechLibViaVersion="20260814.via.v6"
)
