; Deterministic rectangular symbol geometry. No reference label code is evaluated.
procedure(aiTsLess(atsA atsB) alphalessp(car(atsA) car(atsB)))

procedure(aiTsPorts(atsCv)
  sort(foreach(mapcar atsTerm atsCv~>terminals
    list(atsTerm~>name atsTerm~>direction atsTerm~>numBits
      when(aiTplGlobal(atsTerm~>net) atsTerm~>net~>name)
      atsTerm~>net~>sigType atsTerm~>net~>name)) 'aiTsLess))

procedure(aiTsCheckPorts(atcCv atcRows atcSymbol)
  prog((atcTerm)
    unless(length(atcCv~>terminals)==length(atcRows) error("terminal count differs"))
    foreach(atcRow atcRows
      atcTerm=dbFindTermByName(atcCv car(atcRow))
      unless(and(atcTerm atcTerm~>net atcTerm~>direction==cadr(atcRow)
        atcTerm~>numBits==caddr(atcRow) atcTerm~>net~>sigType==nth(4 atcRow))
        error("terminal direction/width/signal differs: %s" car(atcRow)))
      if(atcSymbol then
        unless(atcTerm~>net~>name==car(atcRow) error("symbol terminal net differs"))
      else
        unless(equal(when(aiTplGlobal(atcTerm~>net) atcTerm~>net~>name) nth(3 atcRow))
          error("schematic global net differs: %s" car(atcRow)))))
    return(t)))

procedure(aiTsSnapshot(atsCv)
  when(and(atsCv~>cellViewType=="schematicSymbol" or(atsCv~>instances atsCv~>mosaics))
    error("unexpected symbol instances/mosaics"))
  append(list(aiTsPorts(atsCv)
    sort(foreach(mapcar atsShape atsCv~>shapes aiTplFigure(atsShape)) 'alphalessp)
    sort(foreach(mapcar atsTerm atsCv~>terminals
      list(atsTerm~>name sort(foreach(mapcar atsPin atsTerm~>pins aiTplFigure(atsPin~>fig)) 'alphalessp))) 'aiTsLess)
    atsCv~>bBox atsCv~>cadTemplateRef atsCv~>cadTemplateSymbolDigest)
    when(atsCv~>partName list(list("partName" atsCv~>partName)))))

procedure(aiTsBuild(atbCv atbEntry)
  prog((atbMaster atbPoint atbRight atbWidth atbHeight atbLabel atbPin atbLabels atbBox atbOtherBox atbShape)
    atbWidth=atbEntry['width] atbHeight=atbEntry['height]
    unwindProtect(progn(
      atbMaster=dbOpenCellViewByType("basic" "sympin" "symbolNN" "" "r")
      unless(and(atbMaster !dbIsCellViewModified(atbMaster)) error("symbol pin master missing/modified"))
      unless(schCreateSymbolShape(atbCv "rectangle" "outline" list(list(0 -atbHeight) list(atbWidth 0)))
        error("symbol outline failed"))
      foreach(atbRow atbEntry['rows]
        atbPoint=list(nth(5 atbRow) nth(6 atbRow)) atbRight=nth(7 atbRow)
        atbPin=schCreateSymbolPin(atbCv atbMaster car(atbRow) cadr(atbRow) atbPoint "R0")
        unless(atbPin error("symbol pin creation failed"))
        ; Net sigType uses "supply"; the documented sch pin choice "power" is different.
        dbFindTermByName(atbCv car(atbRow))~>net~>sigType=nth(4 atbRow)
        unless(schCreateSymbolShape(atbCv "line" "outline"
          list(atbPoint list(if(atbRight atbWidth 0) yCoord(atbPoint)))) error("symbol stub failed"))
        atbLabel=schCreateSymbolLabel(atbCv list(if(atbRight atbWidth-0.125 0.125) yCoord(atbPoint))
          "pin name" car(atbRow) if(atbRight "centerRight" "centerLeft") "R0" "stick" 0.125 "normalLabel")
        unless(atbLabel error("pin label failed"))
        ; Native font bounds must fit the body, not merely the SVG estimate.
        unless(and(xCoord(car(atbLabel~>bBox))>=0 xCoord(cadr(atbLabel~>bBox))<=atbWidth)
          error("pin label exceeds body width"))
        atbBox=atbLabel~>bBox
        foreach(atbPrevious atbLabels
          atbOtherBox=atbPrevious~>bBox
          when(and(xCoord(car(atbBox))<xCoord(cadr(atbOtherBox))
            xCoord(cadr(atbBox))>xCoord(car(atbOtherBox))
            yCoord(car(atbBox))<yCoord(cadr(atbOtherBox))
            yCoord(cadr(atbBox))>yCoord(car(atbOtherBox))) error("native pin label overlap")))
        atbLabels=cons(atbLabel atbLabels)
        aiCircuitAnalogPinLabel(atbCv car(atbRow) atbPoint atbRight))
      unless(schCreateSymbolLabel(atbCv list(atbWidth/2 0.375) "symbol shape" atbEntry['cell]
        "centerCenter" "R0" "stick" 0.125 "normalLabel") error("title failed"))
      unless(schCreateSymbolLabel(atbCv list(0 -atbHeight-0.375) "instance label" "[@instanceName]"
        "upperLeft" "R0" "stick" 0.125 "NLPLabel") error("instance label failed"))
      atbShape=aiCircuitCreateSymbolBox(atbCv)
      unless(equal(atbShape~>bBox list(
        list(if(exists(atbR atbEntry['rows] !nth(7 atbR)) -0.3125 0) -atbHeight)
        list(if(exists(atbR atbEntry['rows] nth(7 atbR)) atbWidth+0.3125 atbWidth) 0)))
        error("selection box differs from planned anchors/body"))
      unless(dbReplaceProp(atbCv "cadTemplateRef" "string" atbEntry['template]) error("provenance write failed"))
      unless(dbReplaceProp(atbCv "cadTemplateSymbolDigest" "string" atbEntry['digest]) error("digest write failed"))
      aiTsCheckPorts(atbCv atbEntry['rows] t) t)
      when(atbMaster dbClose(atbMaster)))
    return(t)))

procedure(aiTsVic(atvCv)
  prog((atvOld atvResult)
    atvOld=schGetEnv("vicViewList")
    unwindProtect(progn(
      schSetEnv("vicViewList" list("schematic"))
      ; Markers can only be added to this newly created symbol, never the source schematic.
      atvResult=schVIC(atvCv)) schSetEnv("vicViewList" atvOld))
    unless(equal(atvResult list(0 0)) error("symbol/schematic VIC failed: %L" atvResult))
    return(atvResult)))
