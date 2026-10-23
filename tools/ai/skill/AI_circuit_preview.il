; Export a saved schematic directly into the caller's private .cad preview path.
; Python owns the file read and report; this native function returns metadata only.
procedure(aiCrExportSvg(aepRef aepPath)
  prog((aepEntry aepTarget aepCv aepResult aepStatus aepMessage aepSize aepRoot aepName)
    aepEntry=aiCreatedCircuits[aepRef]
    unless(and(aepEntry aepEntry['saved] aepEntry['verified])
      return(aiCircuitError("svg_preview_unavailable" "Saved readback is required")))
    unless(stringp(aepEntry['previewWorkspace])
      return(aiCircuitError("svg_preview_unavailable" "Creation project directory is unavailable")))
    aepRoot=strcat(aepEntry['previewWorkspace] "/.sico/ai/circuit_previews/")
    unless(and(stringp(aepPath) strlen(aepPath)>strlen(aepRoot) strlen(aepPath)<=8192
        substring(aepPath 1 strlen(aepRoot))==aepRoot
        simplifyFilename(aepPath)==aepPath)
      return(aiCircuitError("svg_preview_path" "SVG path must be in the workspace preview directory")))
    aepName=substring(aepPath strlen(aepRoot)+1)
    unless(and(strlen(aepName)==36 rexMatchp("^[0-9a-f]+\\.svg$" aepName))
      return(aiCircuitError("svg_preview_path" "SVG path must be a .cad SVG file")))
    ; The first receipt owns its unique file for this native creation lifetime.
    ; Replays return metadata only, even if the caller proposed a different name.
    when(aepEntry['svgPreviewReply] return(aepEntry['svgPreviewReply]))
    aepTarget=car(aepEntry['payload]) aepCv=nil
    aepStatus="export_failed" aepMessage="SVG export unavailable" aepSize=0
    aepResult=errset(unwindProtect(progn(
      unless(isCallable('schExportSVG) error("schExportSVG unavailable in this session"))
      when(isFile(aepPath) error("SVG preview destination already exists"))
      aepCv=dbOpenCellViewByType(car(aepTarget) cadr(aepTarget) caddr(aepTarget) "" "r")
      unless(and(aepCv !dbIsCellViewModified(aepCv)
          equal(aiCrSnapshot(aepCv) aepEntry['snapshot])) error("saved target changed"))
      unless(schExportSVG(?cellView aepCv ?path aepPath) error("schExportSVG failed"))
      aepSize=fileLength(aepPath)
      unless(and(aepSize fixp(aepSize) aepSize>0 aepSize<=2097152)
        error("SVG preview size is outside the 2 MiB limit"))
      unless(equal(aiCrSnapshot(aepCv) aepEntry['snapshot]) error("target changed during export"))
      aepStatus="ready" aepMessage=nil t)
      when(aepCv dbClose(aepCv))) t)
    unless(aepResult
      aepStatus="export_failed" aepMessage=or(aepMessage "schExportSVG failed"))
    aepEntry['svgPreviewReply]=aiCircuitObject(list(list("ok" aiCircuitBool(aepStatus=="ready"))
      list("schema" "cad.schematic.svg-preview.v1") list("status" aepStatus)
      list("media_type" "image/svg+xml") list("source" "Virtuoso.schExportSVG")
      list("path" aepPath) list("size_bytes" aepSize) list("message" aepMessage)
      list("cadence_expressions" "preserved_as_exported")
      list("acceptance_blocking" 'jsonFalse)))
    return(aepEntry['svgPreviewReply])))
