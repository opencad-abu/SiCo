; Explicit initialization for the selected PDK library.
; The library name is data from the current DD session; no foundry is named here.
unless(boundp('aiPdkInitState) aiPdkInitState=makeTable("aiPdkInitState" nil))
procedure(aiPdkInitRevision() "20260919.ai.pdk.init.v1")

procedure(aiPdkInitLibrary(apilLibrary)
  prog((apilObject apilFile apilPath apilSaved apilResult)
    unless(and(stringp(apilLibrary) strlen(apilLibrary)>0 strlen(apilLibrary)<=256)
      return(list(nil "pdk_init_invalid_library" "Selected PDK library name is invalid")))
    apilObject=ddGetObj(apilLibrary)
    unless(apilObject
      return(list(nil "pdk_init_library_unavailable"
        strcat("Selected PDK library is unavailable: " apilLibrary))))
    ; Resolve the initialization file through DD. Do not infer a path from the
    ; library directory or search the filesystem for a similarly named file.
    apilFile=ddGetObj(apilLibrary nil nil "libInit.il")
    unless(apilFile
      ; Cadence's installed basic library is a pin/schematic primitive library,
      ; not a process PDK. A library merely named "basic" is not exempt.
      when(equal(simplifyFilename(ddGetObjReadPath(apilObject))
        simplifyFilename(prependInstallPath("etc/cdslib/basic")))
        return(list(t nil nil)))
      return(list(nil "pdk_init_missing" "Selected PDK has no libInit.il")))
    apilPath=ddGetObjReadPath(apilFile)
    unless(and(stringp(apilPath) strlen(apilPath)>0 isFile(apilPath) isReadable(apilPath))
      return(list(nil "pdk_init_path_missing" "Selected PDK libInit.il has no readable path")))
    apilPath=simplifyFilename(apilPath)
    apilSaved=aiPdkInitState[apilLibrary]
    when(and(apilSaved equal(cadr(apilSaved) apilPath))
      return(list(t apilPath t)))
    ; libInit.il is selected by DD, not supplied by the model. Keep the load
    ; bounded to this file and turn any CIW error into a fail-closed result.
    apilResult=errset(progn(load(apilPath) t) nil)
    unless(apilResult
      return(list(nil "pdk_init_failed" strcat("PDK libInit.il failed: " apilPath))))
    aiPdkInitState[apilLibrary]=list(apilLibrary apilPath)
    return(list(t apilPath nil))))

procedure(aiPdkInitializeLibrary(apilLibrary)
  let((apilResult)
    apilResult=aiPdkInitLibrary(apilLibrary)
    if(car(apilResult)
      then aiPdkJson(aiPdkObj(list(list("ok" t) list("library" apilLibrary)
        list("init_file" when(cadr(apilResult) "libInit.il")) list("init_path" cadr(apilResult))
        list("status" if(cadr(apilResult) "initialized" "not_required_builtin"))
        list("cached" aiPdkBool(caddr(apilResult)))
        list("initialized" aiPdkBool(cadr(apilResult))))))
      else aiPdkError(cadr(apilResult) caddr(apilResult)))))
