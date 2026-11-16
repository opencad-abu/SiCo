; Bounded plain JSON transfer over the existing eval_skill bridge, not a new IPC channel.
procedure(aiPdkIdentity(apIdLib apIdCell apIdView)
  let((apIdCellObj apIdViewObj apIdFile apIdViews apIdModified)
    apIdCellObj=ddGetObj(apIdLib apIdCell)
    apIdViewObj=ddGetObj(apIdLib apIdCell apIdView)
    unless(and(apIdCellObj apIdViewObj) error("selected master unavailable"))
    when(length(apIdCellObj~>views)>1024 error("view list limit"))
    foreach(apIdOther apIdCellObj~>views apIdViews=cons(apIdOther~>name apIdViews))
    apIdFile=ddGetObj(apIdLib apIdCell apIdView "*")
    apIdModified=when(apIdFile errset(ddGetObjLastModify(apIdFile) nil))
    aiPdkObj(list(list("target" aiPdkObj(list(list("library" apIdLib)
      list("cell" apIdCell) list("view" apIdView))))
      list("available_views" aiPdkArray(sort(apIdViews 'alphalessp)))
      list("resolved_view_path" ddGetObjReadPath(apIdViewObj))
      list("oa_view_type" when(apIdFile ddMapGetFileViewType(apIdFile)))
      list("master_file_modified" if(apIdModified aiPdkRaw(car(apIdModified)) nil))
      list("master_version_source" "ddGetObjLastModify; observed file timestamp, not a content hash")
      list("source" "ddGetObj; ddMapGetFileViewType")))))

procedure(aiPdkCellLess(apLessLeft apLessRight)
  alphalessp(apLessLeft~>name apLessRight~>name))
procedure(aiPdkDirectory(apDirLib apDirCell apDirView @optional (apDirOffset 0) (apDirLimit 32))
  prog((apDirLibs apDirCells apDirItems apDirCount apDirScanned apDirRead apDirIssues
        apDirTotal apDirTail apDirNext)
    ; prog locals stay unbound until assigned; every value read below is initialized here.
    apDirCount=0 apDirScanned=0 apDirItems=nil apDirIssues=nil apDirCells=nil
    apDirTotal=nil apDirNext=nil
    unless(and(apDirLib ddGetObj(apDirLib) fixp(apDirOffset) apDirOffset>=0
      fixp(apDirLimit) apDirLimit>=1 apDirLimit<=32) error("select a library and bounded batch"))
    apDirLibs=list(ddGetObj(apDirLib))
    foreach(apDirLibObj apDirLibs
        apDirRead=errset(progn(
          apDirCells=if(apDirCell
            then when(ddGetObj(apDirLibObj~>name apDirCell) list(ddGetObj(apDirLibObj~>name apDirCell)))
            else apDirLibObj~>cells)
          apDirTotal=length(apDirCells)
          when(or(apDirTotal>20000 apDirOffset>apDirTotal) error("directory size/offset limit"))
          apDirCells=sort(copy(apDirCells) 'aiPdkCellLess)
          apDirTail=nthcdr(apDirOffset apDirCells)
          while(and(apDirTail apDirScanned<apDirLimit)
            let((apDirCellObj)
              apDirCellObj=car(apDirTail) apDirTail=cdr(apDirTail)
              apDirScanned=apDirScanned+1
                when(ddGetObj(apDirLibObj~>name apDirCellObj~>name apDirView)
                  apDirItems=cons(aiPdkObj(list(
                    list("identity" aiPdkIdentity(apDirLibObj~>name apDirCellObj~>name apDirView))
                    list("cdf_summary" aiPdkCdfSummary(apDirCellObj)))) apDirItems)
                  apDirCount=apDirCount+1)))
          when(apDirTail apDirNext=apDirOffset+apDirScanned)
          t) nil)
        unless(apDirRead apDirIssues=cons(strcat("library_read_failed:" apDirLibObj~>name) apDirIssues)))
    return(aiPdkObj(list(list("status" if(apDirIssues "partial" "complete"))
      list("truncated" 'pdkJsonFalse) list("scanned_cells" apDirScanned)
      list("offset" apDirOffset) list("next_offset" apDirNext) list("total_cells" apDirTotal)
      list("issues" aiPdkArray(apDirIssues)) list("items" aiPdkArray(reverse(apDirItems)))
      list("source" "ddGetLibList/ddGetObj/cells/views; cdfGetCellCDF parameter names only"))))))

procedure(aiPdkDevice(apDevLib apDevCell apDevView)
  let((apDevId apDevFile apDevType apDevCdf apDevDb)
    apDevId=aiPdkIdentity(apDevLib apDevCell apDevView)
    apDevFile=ddGetObj(apDevLib apDevCell apDevView "*")
    apDevType=when(apDevFile ddMapGetFileViewType(apDevFile))
    apDevCdf=errset(aiPdkCdf(ddGetObj(apDevLib apDevCell)) nil)
    apDevDb=aiPdkDatabase(apDevLib apDevCell apDevView apDevType)
    aiPdkObj(list(list("identity" apDevId)
      list("view_paths" aiPdkViewPaths(apDevLib apDevCell))
      list("library" aiPdkLibrary(ddGetObj(apDevLib))) list("database" apDevDb)
      list("cdf" if(apDevCdf car(apDevCdf)
        aiPdkObj(list(list("presence" "unknown") list("parameters" aiPdkSection("unavailable" "cdfGetCellCDF" nil))
          list("simulators" aiPdkSection("unavailable" "cdfGetCellCDF" nil))
          list("cell_metadata" aiPdkObj(nil)) list("cell_metadata_status" "unavailable")))))
      list("project_models" aiPdkSection("not_requested" "project ADE/setup adapter pending" nil))))))

; Process-level layout facts from the OA technology database. The ASCII
; techfile is not used here; units come from tech.db and all rule lists are
; returned as observed values for the Python compiler.
procedure(aiPdkLayoutProcess(apLayLib)
  let((apLayLibObj apLayTf apLayUnits apLayLps apLayRows apLayVias apLayViaRows
       apLayRouting apLayRoutingRows apLayGrids apLaySpacing apLayElectrical)
    apLayLibObj=ddGetObj(apLayLib)
    unless(apLayLibObj error("layout technology library unavailable"))
    apLayTf=techGetTechFile(apLayLibObj)
    unless(apLayTf error("layout technology database unavailable"))
    apLayUnits=aiPdkObj(list(
      list("view_type_units" aiPdkRaw(techGetViewTypeUnits(apLayTf)))
      list("dbu_per_uu" aiPdkRaw(techGetDBUPerUU(apLayTf "maskLayout")))
      list("user_unit" aiPdkRaw(techGetUserUnit(apLayTf "maskLayout")))
      list("mfg_grid" aiPdkRaw(techGetMfgGridResolution(apLayTf)))))
    apLayLps=techGetLPsByPriority(apLayTf)
    when(length(apLayLps)>4096 error("layer-purpose limit"))
    apLayRows=nil
    foreach(apLayLp apLayLps
      let((apLayName apLayPurpose apLayMask apLayFunction apLayPacket)
        apLayName=errset(apLayLp~>layer~>name nil)
        apLayPurpose=errset(apLayLp~>purpose nil)
        apLayMask=errset(techGetLayerMaskNumber(apLayTf car(apLayName)) nil)
        apLayFunction=errset(techGetLayerFunction(apLayTf car(apLayName)) nil)
        apLayPacket=errset(techGetLPPacketName(apLayLp) nil)
        apLayRows=cons(aiPdkObj(list(
          list("layer" aiPdkRaw(if(apLayName car(apLayName) nil)))
          list("purpose" aiPdkRaw(if(apLayPurpose car(apLayPurpose) nil)))
          list("mask_number" aiPdkRaw(if(apLayMask car(apLayMask) nil)))
          list("function" aiPdkRaw(if(apLayFunction car(apLayFunction) nil)))
          list("packet" aiPdkRaw(if(apLayPacket car(apLayPacket) nil)))
          list("priority" aiPdkRaw(car(techGetLPAttr(apLayLp))))
        )) apLayRows)))
    apLayVias=techGetPrGenViaRules(apLayTf)
    when(length(apLayVias)>512 error("via rule limit"))
    apLayViaRows=nil
    foreach(apLayVia apLayVias
      apLayViaRows=cons(aiPdkObj(list(
        list("name" aiPdkRaw(apLayVia))
        list("params" aiPdkRaw(techGetPrGenViaParams(apLayTf apLayVia)))))
        apLayViaRows))
    apLayRouting=techGetPrRoutingLayers(apLayTf)
    apLayRoutingRows=nil
    foreach(apLayRoute apLayRouting
      let((apLayRouteLayer apLayPitch apLayOffset)
        apLayRouteLayer=car(apLayRoute)
        apLayPitch=errset(techGetPrRoutingPitch(apLayTf apLayRouteLayer) nil)
        apLayOffset=errset(techGetPrRoutingOffset(apLayTf apLayRouteLayer) nil)
        apLayRoutingRows=cons(aiPdkObj(list(
          list("layer" aiPdkRaw(apLayRouteLayer))
          list("direction" aiPdkRaw(cadr(apLayRoute)))
          list("pitch" aiPdkRaw(if(apLayPitch car(apLayPitch) nil)))
          list("offset" aiPdkRaw(if(apLayOffset car(apLayOffset) nil)))
        )) apLayRoutingRows)))
    apLayGrids=techGetLayerRoutingGrids(apLayTf)
    apLaySpacing=techGetOrderedSpacingRules(apLayTf)
    apLayElectrical=techGetElectricalRules(apLayTf)
    aiPdkObj(list(
      list("technology_library" apLayLib)
      list("units" apLayUnits)
      list("lpps" aiPdkArray(reverse(apLayRows)))
      list("routing_layers" aiPdkRaw(apLayRouting))
      list("routing_details" aiPdkArray(reverse(apLayRoutingRows)))
      list("routing_grids" aiPdkRaw(apLayGrids))
      list("via_rules" aiPdkArray(reverse(apLayViaRows)))
      list("spacing_rules" aiPdkRaw(apLaySpacing))
      list("electrical_rules" aiPdkRaw(apLayElectrical))
      list("source" "techGetViewTypeUnits/techGetDBUPerUU/techGetUserUnit/techGetMfgGridResolution/techGetLPsByPriority/techGetLayerMaskNumber/techGetLayerFunction/techGetLPPacketName/techGetLPAttr/techGetPrGenViaRules/techGetPrGenViaParams/techGetPrRoutingLayers/techGetLayerRoutingGrids/techGetOrderedSpacingRules/techGetElectricalRules"))))
  )

; Front-end netlist/model view locations. Paths only: no deck content is read here.
procedure(aiPdkViewPaths(apVpLib apVpCell)
  let((apVpRows apVpObj apVpPath)
    foreach(apVpView list("spectre" "auCdl" "auLvs" "hspiceD" "ams")
      apVpObj=errset(ddGetObj(apVpLib apVpCell apVpView) nil)
      when(and(apVpObj car(apVpObj))
        apVpPath=errset(ddGetObjReadPath(car(apVpObj)) nil)
        apVpRows=cons(aiPdkObj(list(list("view" apVpView)
          list("resolved_path" if(apVpPath car(apVpPath) nil))
          list("source" "ddGetObj/ddGetObjReadPath"))) apVpRows)))
    aiPdkSection(if(apVpRows "complete" "unavailable")
      "ddGetObj/ddGetObjReadPath; front-end netlist/model views" reverse(apVpRows))))

; ddCat* category registry. Every read degrades to per-category findings instead of failing.
procedure(aiPdkCategoryLabel(apCatValue)
  let((apCatProtected)
    if(stringp(apCatValue) apCatValue
      progn(apCatProtected=errset(apCatValue~>name nil)
        if(apCatProtected car(apCatProtected) "")))))

procedure(aiPdkCategoryLabels(apCatValues)
  let((apCatRows apCatName)
    foreach(apCatValue apCatValues
      apCatName=aiPdkCategoryLabel(apCatValue)
      when(and(stringp(apCatName) strlen(apCatName)>0) apCatRows=cons(apCatName apCatRows)))
    sort(reverse(apCatRows) 'alphalessp)))

; ddCatGetCatMembers returns (name type) pairs; sub-categories are kept apart from cells.
procedure(aiPdkCategoryRows(apCatPairs)
  let((apCatCells apCatChildren apCatName apCatType)
    foreach(apCatPair apCatPairs
      if(listp(apCatPair)
        then apCatName=aiPdkCategoryLabel(car(apCatPair))
          apCatType=if(stringp(cadr(apCatPair)) cadr(apCatPair) "cell")
        else apCatName=aiPdkCategoryLabel(apCatPair) apCatType="cell")
      when(and(stringp(apCatName) strlen(apCatName)>0)
        if(apCatType=="category"
          then apCatChildren=cons(aiPdkObj(list(list("name" apCatName) list("type" apCatType))) apCatChildren)
          else apCatCells=cons(apCatName apCatCells))))
    list(reverse(apCatCells) reverse(apCatChildren))))

procedure(aiPdkCategories(apCatLib)
  prog((apCatObj apCatIds apCatItems apCatIssues apCatUnassigned apCatLabel apCatOpen
        apCatId apCatRead apCatRow)
    apCatObj=nil apCatIds=nil apCatItems=nil apCatIssues=nil apCatUnassigned=nil
    apCatLabel=nil apCatOpen=nil apCatId=nil apCatRead=nil apCatRow=nil
    unless(and(stringp(apCatLib) ddGetObj(apCatLib)) error("select a library"))
    apCatObj=ddGetObj(apCatLib)
    apCatIds=errset(ddCatGetLibCats(apCatObj) nil)
    unless(apCatIds error("category registry unavailable"))
    apCatIds=car(apCatIds)
    when(length(apCatIds)>256 error("category limit"))
    ; ddCatGetCatMembers needs a category ID from ddCatFindCat (names are not accepted)
    ; and that ID must be closed again; failures stay per-category findings.
    foreach(apCatName apCatIds
      apCatLabel=aiPdkCategoryLabel(apCatName)
      apCatOpen=nil apCatId=nil apCatRead=nil apCatRow=nil
      when(stringp(apCatLabel)
        apCatOpen=errset(ddCatFindCat(apCatObj apCatLabel "r") nil))
      when(apCatOpen apCatId=car(apCatOpen))
      when(apCatId
        apCatRead=errset(ddCatGetCatMembers(apCatId) nil)
        errset(ddCatClose(apCatId) nil))
      if(apCatRead then
        apCatRow=aiPdkCategoryRows(car(apCatRead))
        when(length(car(apCatRow))>20000 error("category member limit"))
        apCatItems=cons(aiPdkObj(list(list("name" apCatLabel)
          list("members" aiPdkArray(car(apCatRow)))
          list("children" aiPdkArray(cadr(apCatRow)))
          list("source" "ddCatGetLibCats/ddCatFindCat/ddCatGetCatMembers"))) apCatItems)
        else apCatIssues=cons(strcat("category_unavailable:" apCatLabel) apCatIssues)))
    apCatUnassigned=errset(ddCatGetCellsNotInCat(apCatObj) nil)
    ; prog only returns through return(...): without it the capture publishes data: null.
    return(aiPdkObj(list(list("status" if(apCatIssues "partial" "complete"))
      list("issues" aiPdkArray(apCatIssues))
      list("unassigned" aiPdkArray(if(apCatUnassigned
        aiPdkCategoryLabels(car(apCatUnassigned)) nil)))
      list("items" aiPdkArray(reverse(apCatItems)))
      list("source" "ddCatGetLibCats/ddCatFindCat/ddCatGetCatMembers/ddCatClose/ddCatGetCellsNotInCat"))))))

procedure(aiPdkCaptureRevision() "20260922.ai.pdk.capture.v2")
procedure(aiPdkCapture(apCapToken apCapSession apCapOperation apCapLib apCapCell apCapView
  @optional (apCapOffset 0) (apCapLimit 32))
  prog((apCapContext apCapData apCapResult apCapText apCapInit)
    when(or(aiPdkTransferCount>=4 aiPdkTransfers[apCapToken])
      return(aiPdkError("transfer_limit" "Release previous discovery transfers")))
    ; A selected process library must initialize before its directory, category,
    ; or device metadata is read. The libraries operation has no selected target.
    when(and(apCapLib apCapOperation!="libraries")
      apCapInit=aiPdkInitLibrary(apCapLib)
      unless(car(apCapInit)
        return(aiPdkError(cadr(apCapInit) caddr(apCapInit)))))
    apCapResult=errset(progn(
      apCapContext=aiPdkContext(apCapSession)
      apCapData=cond((apCapOperation=="libraries" aiPdkObj(nil))
        (apCapOperation=="categories" aiPdkCategories(apCapLib))
        (apCapOperation=="directory" aiPdkDirectory(apCapLib apCapCell apCapView apCapOffset apCapLimit))
        ((apCapOperation=="device") aiPdkDevice(apCapLib apCapCell apCapView))
        ((apCapOperation=="layout") aiPdkLayoutProcess(apCapLib))
        (t error("unsupported discovery operation")))
      apCapText=aiPdkJson(aiPdkObj(list(list("context" apCapContext) list("data" apCapData))))
      when(strlen(apCapText)>8388608 error("discovery capture exceeds 8 MiB"))
      t) t)
    unless(apCapResult return(aiPdkError("capture_unavailable"
      sprintf(nil "DD/CDF/DB capture failed: %L" errset.errset))))
    aiPdkTransfers[apCapToken]=apCapText aiPdkTransferCount=aiPdkTransferCount+1
    ; Expire orphaned serialized transfers; this timer retains no DB/CDF references.
    when(isCallable('hiRegTimer)
      hiRegTimer(sprintf(nil "aiPdkDrop(%L)" apCapToken) 6000))
    return(aiPdkJson(aiPdkObj(list(list("ok" t) list("characters" strlen(apCapText))))))))

procedure(aiPdkRead(apReadToken apReadOffset)
  let((apReadText apReadHex apReadEnd apReadCode)
    apReadText=aiPdkTransfers[apReadToken]
    if(and(apReadText fixp(apReadOffset) apReadOffset>=0 apReadOffset<strlen(apReadText)) then
      ; SKILL strlen/substring count bytes. ASCII hex avoids cutting a UTF-8
      ; character at an IPC chunk boundary or changing its byte offset.
      apReadHex="" apReadEnd=min(apReadOffset+6000 strlen(apReadText))
      for(apReadIndex apReadOffset+1 apReadEnd
        apReadCode=charToInt(concat(substring(apReadText apReadIndex 1)))
        apReadHex=strcat(apReadHex sprintf(nil "%02x" apReadCode)))
      aiPdkJson(aiPdkObj(list(list("ok" t)
        list("hex" apReadHex))))
    else aiPdkError("invalid_transfer" "Unknown transfer or offset"))))

procedure(aiPdkDrop(apDropToken)
  when(aiPdkTransfers[apDropToken]
    aiPdkTransfers[apDropToken]=nil aiPdkTransferCount=aiPdkTransferCount-1)
  when(aiPdkTransferCount==0 aiPdkTransfers=makeTable("aiPdkTransfers" nil))
  aiPdkJson(aiPdkObj(list(list("ok" t)))))
