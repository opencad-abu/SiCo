; Visible layout-window OASIS export. Requires AI_window_context and AI_datahub.
; The source cellview is never modified; the fragment is built in a private
; scratch library and exported with the installed XOasis batch translator.
unless(and(boundp('aiLayoutExportVersion)
           aiLayoutExportVersion=="20261023.ai.layout.export.v1")

unless(and(isCallable('aiWindowResolveState)
           isCallable('aiLayoutExportClip)
           isCallable('aiWindowCv)
           isCallable('datahubOk)
           isCallable('datahubLegacyResult))
  error("Layout export requires window and DataHub SKILL support"))

procedure(aiLayoutExportRevision() "20261023.ai.layout.export.v1")

procedure(aiLayoutExportText(altValue altLimit)
  and(stringp(altValue) strlen(altValue)<=altLimit
    datahubTextOk(altValue altLimit)))

procedure(aiLayoutExportAbsolute(altValue)
  and(aiLayoutExportText(altValue 4096)
    substring(altValue 1 1)=="/"))

procedure(aiLayoutExportParent(altPath)
  let((altParts altName altEnd)
    altParts=parseString(altPath "/")
    when(altParts
      altName=car(last(altParts))
      altEnd=strlen(altPath)-strlen(altName)-1
      if(altEnd<1 "/" substring(altPath 1 altEnd)))))

procedure(aiLayoutExportQuote(altText)
  let((altOut altChar altIndex)
    altOut="'"
    for(altIndex 1 strlen(altText)
      altChar=substring(altText altIndex 1)
      altOut=strcat(altOut if(altChar=="'" "'\"'\"'" altChar)))
    strcat(altOut "'")))

procedure(aiLayoutExportUnique()
  let((altPath altToken)
    altPath=makeTempFileName("/tmp/sico-layout-export")
    altToken=car(last(parseString(altPath "/")))
    errset(deleteFile(altPath) nil)
    if(and(stringp(altToken) rexMatchp("^[A-Za-z0-9_]+$" altToken))
      altToken
      "lxo0")))

procedure(aiLayoutExportFile(altPath)
  aiPdkObj(list(
    list("path" altPath)
    list("size" if(isFile(altPath) fileLength(altPath) 0))
    list("sha256" let((altHash)
      when(and(isCallable('aiTplNativeHash) isFile(altPath))
        altHash=errset(aiTplNativeHash(altPath) nil)
        when(altHash car(altHash))))))))

procedure(aiLayoutExportSidecar(altPath altBox altWarnings)
  let((altSidecar altPort altText)
    altSidecar=strcat(altPath ".sico-visible.json")
    when(isFile(altSidecar) return(nil))
    altText=aiPdkJson(aiPdkObj(list(
      list("schema" "cad.visible_layout_fragment.v1")
      list("layout_path" altPath)
      list("scope" "fragment")
      list("connectivity_preserved" 'pdkJsonFalse)
      list("lvs_eligible" 'pdkJsonFalse)
      list("visible_bbox" aiPdkArray(altBox))
      list("warnings" aiPdkArray(altWarnings)))))
    altPort=outfile(altSidecar "w")
    unless(altPort return(nil))
    fprintf(altPort "%s\n" altText)
    close(altPort)
    altSidecar))

procedure(aiLayoutExportRun(altCommand)
  let((altChild altOutput altChunk altWaited altStatus)
    altChild=errset(ipcBeginProcess(altCommand) nil)
    unless(altChild return(list(-1 "")))
    altChild=car(altChild)
    altOutput="" altWaited=0
    while(and(ipcIsAliveProcess(altChild) altWaited<180)
      when(setq(altChunk ipcReadProcess(altChild 1))
        when(strlen(altOutput)<8192
          altOutput=strcat(altOutput altChunk)))
      when(ipcIsAliveProcess(altChild) ipcWait(altChild 1 2))
      altWaited=altWaited+1)
    when(ipcIsAliveProcess(altChild)
      ipcKillProcess(altChild)
      return(list(-1 altOutput)))
    altStatus=ipcGetExitStatus(altChild)
    list(if(fixp(altStatus) altStatus -1) altOutput)))

procedure(aiLayoutExportOasisCommand(altExecutable altLibrary altCell altOutput
                                     altWorkRoot altLayerMap altLog altSummary)
  strcat(
    aiLayoutExportQuote(altExecutable)
    " -library " aiLayoutExportQuote(altLibrary)
    " -topCell " aiLayoutExportQuote(altCell)
    " -view layout"
    " -oasisFile " aiLayoutExportQuote(altOutput)
    " -runDir " aiLayoutExportQuote(altWorkRoot)
    " -logFile " aiLayoutExportQuote(altLog)
    " -summaryFile " aiLayoutExportQuote(altSummary)
    " -ignoreCurrentTimestamp"
    when(and(stringp(altLayerMap) strlen(altLayerMap)>0)
      strcat(" -layerMap " aiLayoutExportQuote(altLayerMap)))
    " 2>&1"))

procedure(aiLayoutExportVisibleOasis(aleWindowRef aleOutputPath aleWorkRoot aleLayerMap
    @optional aleRegion)
  prog((aleState aleWindow aleSignature aleCv aleViewBox aleDbu aleToken
        aleLibrary aleSourceLib aleTechLib aleScratch aleShape aleShapes
        aleInstances aleMosaics aleVias aleBlockages aleFlattened aleCrossing
        aleLabels aleRemoved aleWarnings aleLog aleSummary aleRoot aleExecutable
        aleStatus aleText aleResult aleLibObject aleBox aleCvBox aleOutputShapes
        aleCrossBoundary aleClipResult aleStep aleUnclipped aleCrossingVias
        aleSidecar aleRegionBox)
    unless(and(aiLayoutExportText(aleWindowRef 128)
               aiLayoutExportText(aleOutputPath 4096)
               aiLayoutExportText(aleWorkRoot 4096)
               aiLayoutExportText(aleLayerMap 4096))
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "invalid_arguments" "Invalid window export arguments")))
    aleState=aiWindowResolveState(aleWindowRef)
    unless(car(aleState)=='valid return(aiWindowStateError(aleState)))
    aleWindow=cadr(aleState) aleSignature=caddr(aleState)
    aleCv=car(aleSignature)
    unless(and(aleCv aleCv~>viewType=="maskLayout")
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "invalid_arguments" "The source window is not a layout cellview")))
    aleCvBox=aleCv~>bBox
    aleRegionBox=when(aleRegion
      and(listp(aleRegion) length(aleRegion)==4
        numberp(nth(0 aleRegion)) numberp(nth(1 aleRegion))
        numberp(nth(2 aleRegion)) numberp(nth(3 aleRegion)))
      list(list(nth(0 aleRegion) nth(1 aleRegion))
        list(nth(2 aleRegion) nth(3 aleRegion))))
    when(and(aleRegion !aleRegionBox)
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "invalid_arguments" "region_user_units must contain four numbers")))
    when(aleRegionBox
      unless(and(caar(aleRegionBox)<caadr(aleRegionBox)
          cadar(aleRegionBox)<cadadr(aleRegionBox))
        return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
          "invalid_arguments"
          "region_user_units must be a positive ordered bbox"))))
    when(aleRegionBox
      unless(aiLayoutExportInside(aleCvBox aleRegionBox)
        return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
          "invalid_arguments"
          "region_user_units must be inside the layout bbox"))))
    aleViewBox=if(aleRegionBox aleRegionBox car(errset(hiGetViewBBox(aleWindow) nil)))
    unless(and(aleViewBox length(aleViewBox)==2
               caar(aleViewBox)<caadr(aleViewBox)
               cadar(aleViewBox)<cadadr(aleViewBox))
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "source_not_captured" "The visible layout region is unavailable")))
    unless(and(aiLayoutExportAbsolute(aleWorkRoot)
               isDir(aleWorkRoot) isWritable(aleWorkRoot))
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "invalid_arguments" "work_root must be a writable absolute directory")))
    when(and(stringp(aleLayerMap) strlen(aleLayerMap)>0
             !and(aiLayoutExportAbsolute(aleLayerMap)
                  isFile(aleLayerMap) isReadable(aleLayerMap)))
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "invalid_arguments" "layer_map must be an absolute readable file")))
    aleToken=aiLayoutExportUnique()
    when(strlen(aleOutputPath)==0
      aleOutputPath=strcat(aleWorkRoot "/" aleCv~>cellName "-visible-"
        aleToken ".oasis"))
    unless(and(aiLayoutExportAbsolute(aleOutputPath)
               or(rexMatchp("\\.oasis$" aleOutputPath)
                  rexMatchp("\\.oas$" aleOutputPath))
               isDir(aiLayoutExportParent(aleOutputPath))
               isWritable(aiLayoutExportParent(aleOutputPath))
               !isFile(aleOutputPath))
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "invalid_arguments"
        "output_path must be a new .oasis file in a writable directory")))
    unless(!isFile(strcat(aleOutputPath ".sico-visible.json"))
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "invalid_arguments"
        "Visible-window sidecar already exists")))
    aleSourceLib=ddGetObj(aleCv~>libName)
    aleTechLib=when(aleSourceLib car(errset(techGetTechLibName(aleSourceLib) nil)))
    unless(and(aleTechLib ddGetObj(aleTechLib nil nil "tech.db"))
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "capability_not_available" "The layout technology is unavailable")))
    aleDbu=aleCv~>DBUPerUU
    unless(numberp(aleDbu)
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "source_not_captured" "The layout database resolution is unavailable")))
    aleBox=aiLayoutExportDbuBox(aleViewBox aleDbu)
    aleRoot=car(getInstallPath())
    aleExecutable=strcat(aleRoot "/bin/oasisout")
    unless(isFile(aleExecutable)
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "capability_not_available" "XOasis Out is unavailable")))
    aleLibrary=strcat("sico_export_" aleToken)
    aleLog=strcat(aleWorkRoot "/" aleToken ".oasis.log")
    aleSummary=strcat(aleWorkRoot "/" aleToken ".oasis.sum")
    aleWarnings=if(strlen(aleLayerMap)==0 list("layer_map_not_provided") nil)
    when(aleRegionBox aleWarnings=cons("explicit_region" aleWarnings))
    aleScratch=nil aleLibObject=nil aleStatus=nil aleOutputShapes=0
    aleCrossBoundary=0 aleUnclipped=0 aleCrossingVias=0
    aleStep="scratch"
    errset(unwindProtect(progn(
      aleLibObject=ddCreateLib(aleLibrary strcat(aleWorkRoot "/" aleLibrary))
      unless(aleLibObject error("Visible-window scratch library could not be created"))
      unless(techBindTechFile(aleLibObject aleTechLib "tech.db")
        error("Visible-window scratch technology could not be bound"))
      aleStep="cellview"
      aleScratch=dbOpenCellViewByType(aleLibrary aleCv~>cellName
        "layout" "maskLayout" "w")
      unless(aleScratch error("Visible-window scratch cellview could not be opened"))
      aleShapes=0 aleInstances=0 aleMosaics=0 aleVias=0 aleBlockages=0
      aleRemoved=0
      aleStep="copy-shapes"
      foreach(aleShape aleCv~>shapes
        if(aiLayoutExportCopyable(aleShape~>objType) then
          when(and(aleShape aleShape~>bBox
                   aiLayoutExportOverlap(aleViewBox aleShape~>bBox))
            aleStep=strcat("copy-shapes:" aleShape~>objType)
            unless(dbCopyFig(aleShape aleScratch)
              error("Visible-window shape could not be copied"))
            aleShapes=aleShapes+1)
        else
          aleRemoved=aleRemoved+1))
      aleStep="copy-instances"
      foreach(aleShape aleCv~>instances
        when(and(aleShape aleShape~>bBox
                 aiLayoutExportOverlap(aleViewBox aleShape~>bBox))
          unless(dbCopyFig(aleShape aleScratch)
            error("Visible-window instance could not be copied"))
          aleInstances=aleInstances+1
          unless(aiLayoutExportInside(aleViewBox aleShape~>bBox)
            aleCrossBoundary=aleCrossBoundary+1)))
      aleStep="copy-mosaics"
      foreach(aleShape aleCv~>mosaics
        when(and(aleShape aleShape~>bBox
                 aiLayoutExportOverlap(aleViewBox aleShape~>bBox))
          unless(dbCopyFig(aleShape aleScratch)
            error("Visible-window mosaic could not be copied"))
          aleMosaics=aleMosaics+1
          unless(aiLayoutExportInside(aleViewBox aleShape~>bBox)
            aleCrossBoundary=aleCrossBoundary+1)))
      aleStep="copy-vias"
      foreach(aleShape aleCv~>vias
        when(and(aleShape aleShape~>bBox
                 aiLayoutExportOverlap(aleViewBox aleShape~>bBox))
          unless(dbCopyFig(aleShape aleScratch)
            error("Visible-window via could not be copied"))
          aleVias=aleVias+1
          unless(aiLayoutExportInside(aleViewBox aleShape~>bBox)
            aleCrossingVias=aleCrossingVias+1)))
      aleStep="copy-blockages"
      foreach(aleShape aleCv~>blockages
        when(and(aleShape aleShape~>bBox
                 aiLayoutExportOverlap(aleViewBox aleShape~>bBox))
          unless(dbCopyFig(aleShape aleScratch)
            error("Visible-window blockage could not be copied"))
          aleBlockages=aleBlockages+1))
      aleStep="flatten"
      aleFlattened=aiLayoutExportFlatten(aleScratch)
      aleStep="clip"
      aleClipResult=aiLayoutExportClip(aleScratch aleViewBox)
      aleCrossing=car(aleClipResult) aleLabels=caddr(aleClipResult)
      aleRemoved=aleRemoved+cadddr(aleClipResult)
      aleUnclipped=nth(4 aleClipResult)
      aleOutputShapes=length(aleScratch~>shapes)
      aleStep="save"
      unless(dbSave(aleScratch)
        error("Visible-window scratch cellview could not be saved"))
      dbClose(aleScratch) aleScratch=nil
      aleStep="oasis"
      aleStatus=aiLayoutExportRun(aiLayoutExportOasisCommand(
        aleExecutable aleLibrary aleCv~>cellName aleOutputPath aleWorkRoot
        aleLayerMap aleLog aleSummary))
      t)
      progn(
        when(aleScratch errset(dbClose(aleScratch) nil) aleScratch=nil)
        when(aleLibObject
          unless(errset(ddDeleteObj(aleLibObject) nil)
            aleWarnings=cons("scratch_library_retained" aleWarnings))))) nil)
    unless(aleStatus
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "read_failed" sprintf(nil "Visible-window OASIS export failed during %s"
          aleStep))))
    unless(equal(car(aleStatus) 0)
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "read_failed" sprintf(nil "XOasis Out exited with code %d" car(aleStatus)))))
    unless(isFile(aleOutputPath)
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "read_failed" "XOasis Out did not create the requested OASIS file")))
    aleText=when(isCallable('aiReadTextFile) aiReadTextFile(aleLog 65536))
    when(and(stringp(aleText) rexMatchp("XOASIS-81043" aleText))
      aleWarnings=cons("unmapped_layer_purpose_skipped" aleWarnings))
    when(aleUnclipped>0
      aleWarnings=cons("boundary_shapes_not_clipped" aleWarnings))
    when(aleCrossingVias>0
      aleWarnings=cons("vias_not_clipped" aleWarnings))
    when(aleCrossBoundary>0
      aleWarnings=cons("cross_boundary_instances_flattened" aleWarnings))
    aleWarnings=cons("visible_fragment" aleWarnings)
    aleWarnings=cons("instances_flattened" aleWarnings)
    aleWarnings=cons("connectivity_not_preserved" aleWarnings)
    aleSidecar=aiLayoutExportSidecar(aleOutputPath aleBox aleWarnings)
    unless(aleSidecar
      return(datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "read_failed" "Visible-window sidecar could not be written")))
    aleResult=datahubOk("sico.cad.layout_export.v1" 1 "visible-oasis" "visible_oasis"
      aiPdkObj(list(
        list("window_ref" aleWindowRef)
        list("source" aiWindowCv(aleCv))
        list("source_bbox" aiPdkArray(list(
          aiPdkArray(list(caar(aleCvBox) cadar(aleCvBox)))
          aiPdkArray(list(caadr(aleCvBox) cadadr(aleCvBox))))))
        list("visible_bbox" aiPdkArray(aleBox))
        list("visible_bbox_user_units" aiPdkArray(list(
          aiPdkArray(list(caar(aleViewBox) cadar(aleViewBox)))
          aiPdkArray(list(caadr(aleViewBox) cadadr(aleViewBox))))))
        list("region_source" if(aleRegionBox "explicit" "visible"))
        list("path" aleOutputPath)
        list("size" fileLength(aleOutputPath))
        list("sha256" let((aleHash)
          when(and(isCallable('aiTplNativeHash) isFile(aleOutputPath))
            aleHash=errset(aiTplNativeHash(aleOutputPath) nil)
            when(aleHash car(aleHash)))))
        list("log" aiLayoutExportFile(aleLog))
        list("summary" aiLayoutExportFile(aleSummary))
        list("sidecar" aiLayoutExportFile(aleSidecar))
        list("scratch" aiPdkObj(list(
          list("library" aleLibrary)
          list("cell" aleCv~>cellName)
          list("view" "layout")
          list("removed" aiPdkBool(!member("scratch_library_retained" aleWarnings))))))
        list("counts" aiPdkObj(list(
          list("top_shapes" aleShapes)
          list("top_instances" aleInstances)
          list("top_mosaics" aleMosaics)
          list("top_vias" aleVias)
          list("unclipped_vias" aleCrossingVias)
          list("top_blockages" aleBlockages)
          list("flattened_instances" aleFlattened)
          list("cross_boundary_instances" aleCrossBoundary)
          list("crossing_shapes" aleCrossing)
          list("labels" aleLabels)
          list("removed_shapes" aleRemoved)
          list("unclipped_shapes" aleUnclipped)
          list("output_shapes" aleOutputShapes))))
        list("warnings" aiPdkArray(aleWarnings))
        list("scope" "fragment")
        list("connectivity_preserved" 'pdkJsonFalse)
        list("lvs_eligible" 'pdkJsonFalse)
        list("signoff_eligible" 'pdkJsonFalse)
        list("layout_system" "OASIS")
        list("layer_map" aleLayerMap)
        list("tool" "oasisout")
        list("tool_version" getVersion(t)))))
    aleText=errset(datahubLegacyJson(aleResult) nil)
    return(if(aleText car(aleText)
      datahubLegacyError("sico.cad.layout_export.v1" "visible_oasis"
        "internal" "Visible-window result serialization failed")))))

aiLayoutExportVersion="20261023.ai.layout.export.v1"
)
