; Circuit environment/JSON helpers. No target mutation or callbacks at load time.
unless(boundp('aiCircuitContexts) aiCircuitContexts=makeTable("aiCircuitContexts" nil))
unless(boundp('aiCircuitRequests) aiCircuitRequests=makeTable("aiCircuitRequests" nil))
unless(boundp('aiCircuitRequestCount) aiCircuitRequestCount=0)

procedure(aiCircuitRevision() "20260909.ai.circuit.v5")

; Window initialization may retain output ports for deferred GUI work.
procedure(aiCircuitNativeCall(acnFunction acnValues)
  if(and(boundp('aiCircuitNativePorts) aiCircuitNativePorts) then
    aiEvalFlushWarnings(woport)
    let(((poport car(aiCircuitNativePorts)) (woport cadr(aiCircuitNativePorts))
         (errport caddr(aiCircuitNativePorts)))
      unwindProtect(apply(acnFunction acnValues) aiEvalFlushWarnings(woport)))
  else apply(acnFunction acnValues)))

/* 右上格是否已被别的窗口占住（屏幕坐标、左下原点）。 */
procedure(aiCircuitUpperTaken(acpuHalf acpuHeight)
  let((acpuTaken acpuWin acpuBox)
    when(isCallable('hiGetWindowList)
      foreach(acpuWin car(errset(hiGetWindowList('all) nil))
        when(acpuWin
          acpuBox=car(errset(hiGetAbsWindowScreenBBox(acpuWin) nil))
          when(and(acpuBox xCoord(car(acpuBox))>=acpuHalf
              yCoord(car(acpuBox))>=acpuHeight)
            acpuTaken=t))))
    acpuTaken))

procedure(aiCircuitWindowBox(acpbWindow acpbBox)
  errset(hiResizeWindow(acpbWindow acpbBox) nil)
  errset(hiMoveWindow(acpbWindow car(acpbBox)) nil)
  acpbWindow)

/* SiCo 打开的编辑器窗口排在屏幕右半屏：右上 / 右下各半个，一次能看两个。
   右下格向上叠 24px 让两条标题栏都露出来；已经在右半屏的窗口不动。 */
procedure(aiCircuitPlaceRightHalf(acpwWindow)
  let((acpwPixels acpwWidth acpwHeight acpwHalf acpwLeft)
    when(acpwWindow
      acpwPixels=car(errset(hiGetScreenSize() nil))
      /* hiGetScreenSize() 返回 ((像素宽 像素高)(毫米宽 毫米高))：取像素子表，
         兼容扁平 (宽 高) 返回。 */
      when(listp(car(acpwPixels)) acpwPixels=car(acpwPixels)))
    when(and(acpwPixels fixp(car(acpwPixels)) fixp(cadr(acpwPixels))
        car(acpwPixels)>=640 cadr(acpwPixels)>=600)
      acpwWidth=car(acpwPixels)
      acpwHeight=cadr(acpwPixels)
      acpwHalf=acpwWidth/2
      acpwLeft=car(errset(hiGetAbsWindowScreenBBox(acpwWindow) nil))
      unless(and(acpwLeft xCoord(car(acpwLeft))>=acpwHalf)
        if(aiCircuitUpperTaken(acpwHalf acpwHeight/2)
          aiCircuitWindowBox(acpwWindow list(list(acpwHalf 0)
            list(acpwWidth (acpwHeight/2 + 24))))
          aiCircuitWindowBox(acpwWindow list(list(acpwHalf acpwHeight/2)
            list(acpwWidth acpwHeight)))))
    acpwWindow)))

/* SiCo 打开的编辑器窗口要弹到用户面前：窗口可能落在别的窗口后面，或已被图标化/
   未映射，用户看不到。hiRaiseWindow 只对可见窗口生效（实机核对：图标化与未映射
   窗口置顶后仍是原状态），所以先恢复显示状态再置顶。任一步失败都不阻断流程，
   返回原窗口便于串联调用。 */
procedure(aiCircuitRaiseWindow(acrwWindow)
  let((acrwState)
    when(acrwWindow
      acrwState=car(errset(hiGetWindowState(acrwWindow) nil))
      cond(
        (acrwState=='iconified errset(hiDeiconifyWindow(acrwWindow) nil))
        (acrwState=='unmapped errset(hiMapWindow(acrwWindow) nil)))
      errset(hiRaiseWindow(acrwWindow) nil))
    acrwWindow))

procedure(aiCircuitJson(acjValue)
  let((acjParts)
    cond((eq(acjValue 'jsonFalse) "false") (eq(acjValue t) "true")
      (null(acjValue) "null")
      (stringp(acjValue) strcat("\"" aiJsonEscape(acjValue) "\""))
      (numberp(acjValue) if(fixp(acjValue) sprintf(nil "%d" acjValue) sprintf(nil "%.15g" acjValue)))
      (listp(acjValue)
        acjParts=""
        foreach(acjItem acjValue
          acjParts=strcat(acjParts if(acjParts=="" "" ",") aiCircuitJson(acjItem)))
        strcat("[" acjParts "]"))
      (t aiCircuitJson(sprintf(nil "%L" acjValue))))))
procedure(aiCircuitBool(acbValue) if(acbValue t 'jsonFalse))
procedure(aiCircuitObject(acoPairs)
  let(((acoBody ""))
    foreach(acoPair acoPairs
      acoBody=strcat(acoBody if(acoBody=="" "" ",")
        aiCircuitJson(car(acoPair)) ":" aiCircuitJson(cadr(acoPair))))
    strcat("{" acoBody "}")))
procedure(aiCircuitError(aceCode aceMessage @optional aceRef)
  aiCircuitObject(list(list("ok" 'jsonFalse) list("code" aceCode)
    list("message" aceMessage) list("context_ref" aceRef))))
procedure(aiCircuitGuiP()
  and(isCallable('hiGetCIWindow) car(errset(hiGetCIWindow() nil))))
procedure(aiCircuitMode(acmPresentation)
  when(member(acmPresentation list("foreground" "background")) acmPresentation))
procedure(aiCircuitTextP(actValue)
  and(stringp(actValue) strlen(actValue)>0 strlen(actValue)<=256
    !rexMatchp("[ \t\n\r/]" actValue)))
procedure(aiCircuitTargetP(actLib actCell actView actType)
  and(aiCircuitTextP(actLib) aiCircuitTextP(actCell) aiCircuitTextP(actView)
    member(actType list("schematic" "schematicSymbol" "maestro"))))
procedure(aiCircuitViewType(actLib actCell actView)
  let((actFile)
    actFile=ddGetObj(actLib actCell actView "*")
    when(actFile ddMapGetFileViewType(actFile))))
procedure(aiCircuitCvMatches(accCv accTarget)
  and(accCv equal(list(accCv~>libName accCv~>cellName accCv~>viewName)
    list(car(accTarget) cadr(accTarget) caddr(accTarget)))))
procedure(aiCircuitSessionMatches(acsSession acsTarget)
  and(acsSession equal(list(axlGetSessionLibName(acsSession)
    axlGetSessionCellName(acsSession) axlGetSessionViewName(acsSession)) acsTarget)))
procedure(aiCircuitSessions(acsTarget)
  let((acsFound)
    foreach(acsName maeGetSessions()
      when(aiCircuitSessionMatches(acsName acsTarget) acsFound=cons(acsName acsFound)))
    acsFound))
procedure(aiCircuitMissing(acmNames)
  let((acmMissing)
    foreach(acmName acmNames
      unless(isCallable(acmName) acmMissing=cons(symbolToString(acmName) acmMissing)))
    reverse(acmMissing)))

procedure(aiCircuitOpenApis(acaType acaMode)
  append(append(list('ddGetObj 'ddMapGetFileViewType 'ddIsObjReadable 'ddIsObjWritable)
    if(acaMode=="foreground" list('deOpenCellView 'hiGetWindowList 'deGetCellView) nil))
    append(if(acaType=="maestro" then list('maeGetSessions 'axlGetSessionLibName
      'axlGetSessionCellName 'axlGetSessionViewName 'maeOpenSetup 'maeCloseSession
      'axlIsSessionReadOnly 'maeIsSetupModified 'axlGetRunStatus 'axlGetWindowSession)
      else list('dbOpenCellViewByType 'dbClose 'dbIsCellViewModified 'dbGetOpenCellViews))
    if(and(acaMode=="foreground" acaType!="maestro")
      list('geGetWindowCellView 'geGetEditCellView) nil))))

procedure(aiCircuitPreflight(acpLib acpCell acpView acpType acpPresentation)
  prog((acpLibrary acpTarget acpParent acpMode acpReadMissing acpCreateMissing acpMaeMissing
        acpResolved acpValid acpResult acpOpenMissing)
    unless(and(aiCircuitTargetP(acpLib acpCell acpView acpType)
      member(acpPresentation list("foreground" "background")))
      return(aiCircuitError("invalid_arguments" "invalid target or presentation")))
    acpResult=errset(progn(
      acpMode=aiCircuitMode(acpPresentation)
      acpLibrary=ddGetObj(acpLib)
      acpParent=ddGetObj(acpLib acpCell)
      acpTarget=ddGetObj(acpLib acpCell acpView)
      acpResolved=when(acpTarget aiCircuitViewType(acpLib acpCell acpView))
      acpReadMissing=aiCircuitMissing(list('ddGetObj 'ddMapGetFileViewType
        'dbOpenCellViewByType 'dbClose))
      acpCreateMissing=aiCircuitMissing(list('schCreateInst 'schCreatePin 'schCreateWire
        'schCreateWireLabel 'schCreateNoteShape 'schCreateNoteLabel 'schCheck 'dbSave))
      acpMaeMissing=aiCircuitMissing(list('maeOpenSetup 'maeGetSessions 'maeSaveSetup
        'maeCloseSession 'maeCreateTest 'maeSetAnalysis 'maeRunSimulation))
      acpOpenMissing=aiCircuitMissing(aiCircuitOpenApis(acpType acpMode))
      acpValid=and(acpLibrary acpTarget acpResolved==acpType ddIsObjReadable(acpTarget)
        !acpOpenMissing
        or(acpMode=="background" aiCircuitGuiP()))
      aiCircuitObject(list(list("ok" t) list("stage" "preflight")
        list("target" list(acpLib acpCell acpView acpType))
        list("tool_version" getVersion()) list("working_directory" getWorkingDir())
        list("forced_cds_lib" ddGetForcedLib()) list("system_temp_directory" getTempDir())
        list("configured_temp_directory" getShellEnvVar("TMPDIR"))
        list("library_path" when(acpLibrary ddGetObjReadPath(acpLibrary)))
        list("target_path" when(acpTarget ddGetObjReadPath(acpTarget)))
        list("target_exists" aiCircuitBool(acpTarget)) list("actual_view_type" acpResolved)
        list("target_readable" aiCircuitBool(and(acpTarget ddIsObjReadable(acpTarget))))
        list("target_or_parent_writable" aiCircuitBool(and(or(acpTarget acpParent acpLibrary)
          ddIsObjWritable(or(acpTarget acpParent acpLibrary)))))
        list("gui_available" aiCircuitBool(aiCircuitGuiP()))
        list("effective_presentation" acpMode) list("ready_to_open" aiCircuitBool(acpValid))
        list("open_missing_apis" acpOpenMissing)
        list("db_missing_apis" acpReadMissing) list("schematic_missing_apis" acpCreateMissing)
        list("maestro_missing_apis" acpMaeMissing)
        list("write_qualified" 'jsonFalse) list("simulation_qualified" 'jsonFalse)
        list("not_checked" list("effective_CDF_and_callbacks" "models_and_sections"
          "Spectre_license_and_executable" "lock_service" "temp_space_and_inodes"))))) t)
    if(acpResult then return(car(acpResult))
      else return(aiCircuitError("preflight_failed" "Runtime query failed; inspect CIW log")))))
