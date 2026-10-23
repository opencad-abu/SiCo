; Saved native view bytes plus actual interface. Static direct views only.
; Paths stay in private capture; no shell text comes from interpreted source properties.
procedure(aiTplNativeHash(atnPath)
  prog((atnCommand atnChild atnChunk atnOutput atnDigest)
    unless(and(isFile(atnPath) fileLength(atnPath)<=536870912)
      error("Native template file unavailable or exceeds 512 MiB"))
    ; Quote locally so the collector also works without loading the UI launcher.
    atnCommand="'"
    for(atnIndex 1 strlen(atnPath)
      atnChunk=substring(atnPath atnIndex 1)
      atnCommand=strcat(atnCommand if(atnChunk=="'" "'\"'\"'" atnChunk)))
    atnCommand=strcat("exec /usr/bin/sha256sum -- " atnCommand "'\n")
    ; IPC command parsing is distinct from shell parsing: send quoted script on stdin.
    atnChild=ipcBeginProcess("/usr/bin/timeout 15 /bin/sh")
    unless(atnChild error("Cannot hash native template source"))
    unless(ipcWriteProcess(atnChild atnCommand)
      ipcKillProcess(atnChild) error("Cannot send native hash request"))
    atnOutput=""
    while(setq(atnChunk ipcReadProcess(atnChild 16))
      atnOutput=strcat(atnOutput atnChunk)
      when(strlen(atnOutput)>8192
        ipcKillProcess(atnChild) error("Native hash output exceeds budget")))
    ipcWait(atnChild 1 2)
    when(ipcIsAliveProcess(atnChild)
      ipcKillProcess(atnChild) error("Native hash timed out"))
    unless(and(ipcGetExitStatus(atnChild)==0 strlen(atnOutput)>=66)
      error("Native source hash failed"))
    atnDigest=substring(atnOutput 1 64)
    unless(rexMatchp("^[0-9a-f]+$" atnDigest) error("Invalid native source digest"))
    return(atnDigest)))

procedure(aiTplNativeView(atvCv atvRole atvBudget)
  prog((atvFiles atvTerms atvDd atvRows atvPath atvParameter atvEligible)
    unless(atvCv error("Native source master unavailable"))
    atvCv=aiTplSavedView(atvCv)
    atvDd=ddGetObj(atvCv~>libName atvCv~>cellName atvCv~>viewName)
    atvParameter=or(atvCv~>superMaster atvCv~>isParamCell)
    atvEligible=and(!atvParameter null(atvCv~>mosaics)
      or(atvRole=="source" null(atvCv~>instances)))
    when(atvEligible
      atvFiles=ddGetObjFilesEx(atvDd 2)
      unless(and(atvFiles length(atvFiles)<=64) error("Native file inventory unavailable/over budget"))
      foreach(atvFile atvFiles
        atvPath=ddGetObjReadPath(atvFile)
        atvBudget['files]=atvBudget['files]+1
        ; SKILL fixnums wrap at 2^31; doubles retain exact byte counts at this budget.
        atvBudget['bytes]=atvBudget['bytes]+float(fileLength(atvPath))
        when(or(atvBudget['files]>512 atvBudget['bytes]>2147483648.0)
          error("Native template asset exceeds 512 files / 2 GiB"))
        atvRows=cons(strcat("{\"name\":" aiTplJson(atvFile~>name)
          ",\"sha256\":" aiTplJson(aiTplNativeHash(atvPath)) "}") atvRows)))
    foreach(atvTerm atvCv~>terminals
      atvTerms=cons(strcat("{\"name\":" aiTplJson(atvTerm~>name)
        ",\"direction\":" aiTplJson(atvTerm~>direction)
        ",\"width\":" aiTplJson(atvTerm~>numBits) "}") atvTerms))
    return(strcat("{\"target\":" aiTplJson(list(atvCv~>libName atvCv~>cellName atvCv~>viewName))
      ",\"role\":" aiTplJson(atvRole)
      ",\"library_path\":" aiTplJson(ddGetObjReadPath(ddGetObj(atvCv~>libName)))
      ",\"view_type\":" aiTplJson(atvCv~>cellViewType)
      ",\"parameterized\":" if(atvParameter "true" "false")
      ",\"instance_count\":" aiTplJson(length(atvCv~>instances))
      ",\"mosaic_count\":" aiTplJson(length(atvCv~>mosaics))
      ",\"files\":[" buildString(sort(atvRows 'alphalessp) ",") "]"
      ",\"terminals\":[" buildString(sort(atvTerms 'alphalessp) ",") "]}"))))

procedure(aiTplNativeContent(atcSource)
  let((atcRows atcSeen atcKey atcBudget)
    atcBudget=makeTable("aiTplNativeBudget" 0)
    atcRows=list(aiTplNativeView(atcSource "source" atcBudget)) atcSeen=makeTable("aiTplNativeSeen" nil)
    foreach(atcInst atcSource~>instances
      atcKey=list(atcInst~>libName atcInst~>cellName atcInst~>viewName)
      unless(atcSeen[atcKey]
        atcRows=cons(aiTplNativeView(aiTplMasterView(atcInst) "master" atcBudget) atcRows)
        atcSeen[atcKey]=t))
    strcat("{\"schema\":\"cad.template.native-content.v1\",\"views\":["
      buildString(sort(atcRows 'alphalessp) ",") "]}")))
