; Schematic symbol master-local geometry, read access only; borrowed UI IDs stay open.
procedure(aiPdkDbAttrs(apDbObj apDbNames)
  let((apDbPairs apDbRead)
    foreach(apDbName apDbNames
      apDbRead=errset(dbGet(apDbObj apDbName) nil)
      apDbPairs=cons(list(symbolToString(apDbName)
        if(apDbRead aiPdkRaw(car(apDbRead))
          aiPdkObj(list(list("type" "unavailable") list("status" "unavailable") list("value" nil)))))
        apDbPairs))
    aiPdkObj(reverse(apDbPairs))))

procedure(aiPdkPorts(apPortsCv)
  let((apPortsItems apPortsWidth apPortsMembers apPortsExpr apPortsOrder apPortsNet apPortsIndex)
    when(length(apPortsCv~>terminals)>4096 error("terminal limit"))
    foreach(apPortsTerm apPortsCv~>terminals
      apPortsWidth=car(errset(dbGetNameNumBit(apPortsTerm~>name) nil))
      apPortsMembers=nil apPortsOrder="unavailable"
      when(and(numberp(apPortsWidth) apPortsWidth>0 apPortsWidth<=2048)
        apPortsIndex=0 apPortsOrder="complete"
        while(apPortsIndex<apPortsWidth
          apPortsMembers=cons(dbGetMemName(apPortsTerm~>name apPortsIndex) apPortsMembers)
          apPortsIndex=apPortsIndex+1))
      apPortsExpr=errset(dbGetTermNetExpr(apPortsTerm) nil)
      apPortsNet=apPortsTerm~>net
      apPortsItems=cons(aiPdkObj(list(list("name" apPortsTerm~>name)
        list("direction" aiPdkRaw(apPortsTerm~>direction)) list("width" apPortsWidth)
        list("members" aiPdkArray(reverse(apPortsMembers))) list("members_status" apPortsOrder)
        list("net_expression" if(apPortsExpr aiPdkRaw(car(apPortsExpr)) nil))
        list("net_expression_status" if(apPortsExpr "known" "unavailable"))
        list("net_name" when(apPortsNet apPortsNet~>name))
        list("pin_count" length(apPortsTerm~>pins)))) apPortsItems))
    aiPdkSection("complete" "DB terminals; dbGetNameNumBit/dbGetMemName (left to right); dbGetTermNetExpr"
      reverse(apPortsItems))))

procedure(aiPdkGeometry(apGeomCv)
  let((apGeomItems apGeomPinIndex apGeomFigIndex apGeomFigs apGeomCount apGeomBoxProp apGeomBoxes apGeomPins apGeomBody apGeomShapeCount)
    apGeomCount=0 apGeomShapeCount=0
    foreach(apGeomTerm apGeomCv~>terminals
      apGeomPinIndex=0
      foreach(apGeomPin apGeomTerm~>pins
        apGeomFigIndex=0 apGeomFigs=apGeomPin~>figs
        ; A pin without figures is still an observed pin, not an absent terminal.
        unless(apGeomFigs
          apGeomItems=cons(aiPdkObj(list(list("terminal" apGeomTerm~>name)
            list("pin_index" apGeomPinIndex) list("figure_index" nil)
            list("status" "unavailable") list("raw" aiPdkObj(nil)))) apGeomItems))
        foreach(apGeomFig apGeomFigs
          apGeomPins=cons(apGeomFig apGeomPins)
          apGeomCount=apGeomCount+1
          when(apGeomCount>8192 error("pin figure limit"))
          apGeomItems=cons(aiPdkObj(list(list("terminal" apGeomTerm~>name)
            list("pin_index" apGeomPinIndex) list("figure_index" apGeomFigIndex)
            list("status" "complete") list("pin_raw" aiPdkDbAttrs(apGeomPin list('name 'accessDir)))
            list("raw" aiPdkDbAttrs(apGeomFig list('objType 'bBox 'lpp 'points 'width 'beginExt 'endExt)))))
            apGeomItems)
          apGeomFigIndex=apGeomFigIndex+1)
        apGeomPinIndex=apGeomPinIndex+1))
    apGeomBoxProp=dbGetPropByName(apGeomCv "instanceBBox")
    foreach(apGeomShape apGeomCv~>shapes
      apGeomShapeCount=apGeomShapeCount+1
      when(apGeomShapeCount>8192 error("symbol body shape limit"))
      unless(or(member(apGeomShape apGeomPins)
        member(apGeomShape~>objType list("label" "text"))
        equal(apGeomShape~>lpp list("instance" "drawing")))
        apGeomBody=cons(aiPdkDbAttrs(apGeomShape
          list('objType 'bBox 'lpp 'points 'width 'beginExt 'endExt 'startAngle 'stopAngle)) apGeomBody))
      when(equal(apGeomShape~>lpp list("instance" "drawing"))
        apGeomBoxes=cons(aiPdkDbAttrs(apGeomShape list('objType 'bBox 'lpp 'points)) apGeomBoxes)))
    aiPdkObj(list(list("status" "complete") list("source" "DB terminal/pin/figs; master local coordinates")
      list("coordinate_system" "master_local_schematic_user_units")
      list("bbox" aiPdkRaw(apGeomCv~>bBox))
      list("dbu_per_user_unit" aiPdkRaw(apGeomCv~>DBUPerUU))
      list("selection_bbox" when(apGeomBoxProp aiPdkRaw(apGeomBoxProp~>value)))
      list("selection_bbox_status" if(apGeomBoxProp "known" "unknown"))
      list("selection_figures" aiPdkArray(reverse(apGeomBoxes)))
      list("selection_figures_source" "DB shapes on instance/drawing, retained individually")
      list("parameterized_master" aiPdkBool(or(apGeomCv~>isParamCell apGeomCv~>superMaster)))
      list("hierarchical_instance_count" length(apGeomCv~>instances))
      list("mosaic_count" length(apGeomCv~>mosaics))
      list("body_shapes" aiPdkArray(reverse(apGeomBody)))
      list("body_outline_status" "complete") list("items" aiPdkArray(reverse(apGeomItems)))
      list("issues" aiPdkArray(nil))))))

procedure(aiPdkDatabase(apDbLib apDbCell apDbView apDbType)
  prog((apDbCv apDbOwned apDbResult apDbPorts apDbGeometry apDbModified apDbState apDbClosed apDbStamp)
    ; prog locals stay unbound until assigned; initialize every value read below.
    apDbCv=nil apDbOwned=nil apDbResult=nil apDbPorts=nil apDbGeometry=nil
    apDbModified=nil apDbState=nil apDbClosed=nil apDbStamp=nil
    unless(apDbType=="schematicSymbol"
      return(aiPdkObj(list(list("ports" aiPdkSection("unavailable" "unsupported_view_type" nil))
        list("geometry" aiPdkSection("unavailable" "unsupported_view_type" nil))))))
    apDbResult=unwindProtect(errset(progn(
      foreach(apDbOpen dbGetOpenCellViews()
        when(equal(list(apDbOpen~>libName apDbOpen~>cellName apDbOpen~>viewName)
          list(apDbLib apDbCell apDbView)) apDbCv=apDbOpen))
      unless(apDbCv
        apDbCv=dbOpenCellViewByType(apDbLib apDbCell apDbView "schematicSymbol" "r")
        apDbOwned=apDbCv)
      unless(apDbCv error("symbol cannot be opened"))
      unless(ddGetObjReadPath(dbGetCellViewDdId(apDbCv))==
        ddGetObjReadPath(ddGetObj(apDbLib apDbCell apDbView "*")) error("open master binding differs"))
      apDbModified=errset(dbIsCellViewModified(apDbCv) nil)
      apDbStamp=errset(dbGetFileTimeStamp(apDbCv) nil)
      apDbState=aiPdkObj(list(list("modified" if(apDbModified aiPdkBool(car(apDbModified)) nil))
        list("source" "dbIsCellViewModified")
        list("master_file_timestamp" if(apDbStamp aiPdkRaw(car(apDbStamp)) nil))
        list("db_modified_counter" aiPdkRaw(dbGetCellViewCounter(apDbCv)))
        list("read_scope" "current_in_memory_master")))
      apDbPorts=errset(aiPdkPorts(apDbCv) nil)
      apDbGeometry=errset(aiPdkGeometry(apDbCv) nil)
      t) nil)
      ; Close exactly our own open reference even on non-local exit.
      when(apDbOwned apDbClosed=errset(dbClose(apDbOwned) nil)))
    when(and(apDbOwned !car(apDbClosed)) error("owned read reference cleanup failed"))
    return(aiPdkObj(list(list("ports" if(and(apDbResult apDbPorts) car(apDbPorts)
        aiPdkSection("unavailable" "DB terminals" nil list("read_failed"))))
      list("geometry" if(and(apDbResult apDbGeometry) car(apDbGeometry)
        aiPdkSection("unavailable" "DB pin figures" nil list("read_failed"))))
      list("master_state" apDbState))))))
