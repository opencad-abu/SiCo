; Evaluation and context collection in the current Virtuoso process.

unless(and(boundp('aiEvaluatorVersion)
             aiEvaluatorVersion=="20260922.ai.eval.v5")

procedure(aiReadTextFile(path @optional maxBytes)
  let((port line text)
    text=""
    when(and(path isFile(path)
               or(!maxBytes fileLength(path)<=maxBytes))
      port=infile(path)
      when(port
        while(gets(line port)
          text=strcat(text line))
        close(port)
        text))
  )
)

procedure(aiWriteTextFile(path text)
  let((port ok)
    port=outfile(path "w")
    when(port
      ok=car(errset(fprintf(port "%s" text) nil))
      close(port))
    ok
  )
)

; SKILL string offsets count bytes. Do not split UTF-8 at a result boundary.
procedure(aiEvalTextPrefix(aepText aepLimit)
  let((aepEnd aepByte)
    aepEnd=min(strlen(aepText) aepLimit)
    when(aepEnd<strlen(aepText)
      aepByte=charToInt(concat(substring(aepText aepEnd+1 1)))
      while(and(aepEnd>0 aepByte>=128 aepByte<192)
        aepEnd=aepEnd-1
        aepByte=charToInt(concat(substring(aepText aepEnd+1 1)))))
    if(aepEnd>0 substring(aepText 1 aepEnd) "")))

procedure(aiBoundResultText(value)
  let((text bounded)
    text=if(stringp(value) then value else sprintf(nil "%L" value))
    bounded=aiEvalTextPrefix(text 65536)
    ; Two escaped fields plus metadata must fit the Assistant's 180000-byte
    ; result reader and the SiCo pipe. Control characters can expand sixfold.
    while(strlen(aiJsonEscape(bounded))>80000
      bounded=aiEvalTextPrefix(bounded fix(strlen(bounded)*0.75)))
    list(bounded strlen(bounded)<strlen(text))
  )
)

procedure(aiWriteEvalResult(outputPath ok value output errorText @optional (captureMode "skill_ports"))
  let((payload valueResult outputResult errorResult)
    outputResult=aiBoundResultText(or(output ""))
    payload=if(ok then
      valueResult=aiBoundResultText(value)
      sprintf(nil
        "{\"ok\":true,\"value\":\"%s\",\"value_truncated\":%s,\"output\":\"%s\",\"output_truncated\":%s,\"output_capture\":\"%s\"}"
        aiJsonEscape(car(valueResult))
        if(cadr(valueResult) then "true" else "false")
        aiJsonEscape(car(outputResult))
        if(cadr(outputResult) then "true" else "false") aiJsonEscape(captureMode))
    else
      errorResult=aiBoundResultText(or(errorText "SKILL execution failed"))
      sprintf(nil
        "{\"ok\":false,\"error\":\"%s\",\"error_truncated\":%s,\"output\":\"%s\",\"output_truncated\":%s,\"output_capture\":\"%s\"}"
        aiJsonEscape(car(errorResult))
        if(cadr(errorResult) then "true" else "false")
        aiJsonEscape(car(outputResult))
        if(cadr(outputResult) then "true" else "false") aiJsonEscape(captureMode)))
    aiWriteTextFile(outputPath payload)
  )
)

; GUI/ADE may retain poport in lazy contexts and async jobs. Never bind these
; operations to a per-request outstring that is closed when the RPC returns.
procedure(aiEvalNativeExpression(sourcePath outputPath)
  prog((nativeCode nativeResult nativeError nativeWritten)
    nativeCode=aiReadTextFile(sourcePath 65536)
    unless(stringp(nativeCode)
      aiWriteEvalResult(outputPath nil nil "" "cannot read bounded SKILL expression" "ciw")
      return(nil))
    nativeResult=errsetstring(nativeCode nil 'il)
    unless(nativeResult nativeError=sprintf(nil "%L" errset.errset))
    nativeWritten=aiWriteEvalResult(outputPath if(nativeResult t nil) car(nativeResult) "" nativeError "ciw")
    return(and(nativeResult nativeWritten))))

; warn() defers its last message until the next warning or top-level return.
; Flush it before ending the capture, otherwise it leaks into the next request.
procedure(aiEvalFlushWarnings(aewPort)
  let((aewText)
    while(aewText=getWarn()
      fprintf(aewPort "*WARNING* %s" aewText))))

; Result: (errset-result output error). Only synchronous SKILL ports are captured;
; do not bind stdout/stderr or write unframed output to the SiCo protocol pipe.
procedure(aiEvalCaptureCall(aecFunction aecArguments)
  prog((aecPort aecResult aecError aecOutput)
    aecPort=outstring()
    unless(aecPort return(list(nil "" "cannot create output capture")))
    unwindProtect(
      progn(
        ; Preserve an earlier caller's pending warning on its original port.
        aiEvalFlushWarnings(woport)
        let(((poport aecPort) (woport aecPort) (errport aecPort))
          unwindProtect(
            progn(
              aecResult=errset(apply(aecFunction aecArguments) t)
              unless(aecResult aecError=sprintf(nil "%L" errset.errset)))
            aiEvalFlushWarnings(aecPort)))
        aecOutput=getOutstring(aecPort))
      when(openportp(aecPort) close(aecPort)))
    ; Dynamic port bindings restore the exact original ports even on errors,
    ; nonlocal exits and nested capture; close only our own temporary port.
    return(list(aecResult aecOutput aecError))))

procedure(aiEvalCapture(aesSource aesLoadFile)
  aiEvalCaptureCall(if(aesLoadFile 'load 'evalstring)
    if(aesLoadFile list(aesSource) list(aesSource 'il))))

procedure(aiEvalExpression(sourcePath outputPath)
  prog((aeeCode aeeCapture aeeResult aeeWritten)
    aeeCode=aiReadTextFile(sourcePath 65536)
    unless(stringp(aeeCode)
      aiWriteEvalResult(outputPath nil nil ""
        "cannot read SKILL expression or expression exceeds 65536 bytes")
      return(nil))
    aeeCapture=aiEvalCapture(aeeCode nil)
    aeeResult=car(aeeCapture)
    aeeWritten=aiWriteEvalResult(outputPath if(aeeResult t nil) car(aeeResult)
      cadr(aeeCapture) caddr(aeeCapture))
    return(and(aeeResult aeeWritten))))

procedure(aiLoadSkillFile(sourcePath outputPath)
  prog((alsCapture alsResult alsWritten)
    unless(and(sourcePath isFile(sourcePath))
      aiWriteEvalResult(outputPath nil nil "" "SKILL source file is unavailable")
      return(nil))
    alsCapture=aiEvalCapture(sourcePath t)
    alsResult=car(alsCapture)
    alsWritten=aiWriteEvalResult(outputPath if(alsResult t nil) car(alsResult)
      cadr(alsCapture) caddr(alsCapture))
    return(and(alsResult alsWritten))
  )
)

procedure(aiWorkingDirectory()
  let((directory)
    directory=cond(
      (isCallable('getWorkingDir) getWorkingDir())
      (isCallable('pwd) pwd())
      (t "."))
    if(and(directory stringp(directory)) then
      simplifyFilename(directory t)
    else ".")
  )
)

procedure(aiCellViewJson(cellView)
  let((protected values)
    if(!cellView then
      "null"
    else
      protected=errset(
        list(cellView~>libName cellView~>cellName cellView~>viewName) nil)
      if(!protected then
        "null"
      else
        values=car(protected)
        sprintf(nil "{\"lib\":\"%s\",\"cell\":\"%s\",\"view\":\"%s\"}"
          aiJsonEscape(or(car(values) ""))
          aiJsonEscape(or(cadr(values) ""))
          aiJsonEscape(or(caddr(values) "")))))
  )
)

procedure(aiGetContext(outputPath)
  let((window displayCellView editCellView selectionCount protected payload)
    selectionCount=0
    when(isCallable('hiGetCurrentWindow)
      protected=errset(hiGetCurrentWindow() nil)
      when(protected window=car(protected)))
    when(and(window isCallable('geGetWindowCellView))
      protected=errset(geGetWindowCellView(window) nil)
      when(protected displayCellView=car(protected)))
    when(and(window isCallable('geGetEditCellView))
      protected=errset(geGetEditCellView(window) nil)
      when(protected editCellView=car(protected)))
    when(isCallable('geGetSelSetCount)
      protected=errset(geGetSelSetCount(or(editCellView displayCellView)) nil)
      when(and(protected numberp(car(protected)))
        selectionCount=car(protected)))
    payload=sprintf(nil
      "{\"ok\":true,\"cwd\":\"%s\",\"window\":\"%s\",\"display_cellview\":%s,\"edit_cellview\":%s,\"selection_count\":%d}"
      aiJsonEscape(aiWorkingDirectory())
      aiJsonEscape(if(window then sprintf(nil "%L" window) else ""))
      aiCellViewJson(displayCellView) aiCellViewJson(editCellView)
      selectionCount)
    aiWriteTextFile(outputPath payload)
  )
)

procedure(aiEvaluatorRevision()
  "20260922.ai.eval.v5"
)

aiEvaluatorVersion="20260922.ai.eval.v5"
)
