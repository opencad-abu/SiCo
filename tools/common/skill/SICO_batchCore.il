;------------------------------------------------------------------------------
; Shared manifest creation and process launch for OA multi-cell batches.
;------------------------------------------------------------------------------

unless(and(boundp('sicoBatchCoreVersion)
             sicoBatchCoreVersion=="20260923.batch.commands.v3")

unless(boundp('cadBatchSerial)
  cadBatchSerial=0)
unless(boundp('cadBatchRuns)
  cadBatchRuns=makeTable("cadBatchRuns" nil))
unless(boundp('cadBatchByCid)
  cadBatchByCid=makeTable("cadBatchByCid" nil))

procedure(cadBatchDispatchStart(form singleStart batchStart)
  if(cadBatchMultipleP(form) then
    funcall(batchStart form)
  else
    funcall(singleStart form)
  )
)

procedure(cadBatchSafeToken(value)
  let((source result index character)
    source=if(value then sprintf(nil "%s" value) else "")
    result=""
    for(index 1 strlen(source)
      character=substring(source index 1)
      result=strcat(result
        if(rexMatchp("^[A-Za-z0-9]$" character) then character else "_"))
    )
    result
  )
)

procedure(cadBatchNewId(flow)
  let((timestamp)
    cadBatchSerial=cadBatchSerial+1
    timestamp=cadBatchSafeToken(getCurrentTime())
    lowerCase(sprintf(nil "%s-%s-%d-%d"
      flow timestamp ipcGetPid() cadBatchSerial))
  )
)

procedure(cadBatchCreateRoot(form flow batchId)
  let((root status)
    root=simplifyFilename(
      strcat(form~>outDirPath~>value "/.cad-batches/" batchId) t)
    status=system(strcat("mkdir -p " SICO_shellQuote(root)))
    if(status==0 then root else nil)
  )
)

procedure(cadBatchMakeSpec(id label config runDir command cancelCommand
                             launchLog resultPath targetKey
                             publicationRequired)
  let((spec)
    spec=makeTable(strcat("cadBatchSpec" id) nil)
    spec['id]=id
    spec['label]=label
    spec['config]=config
    spec['runDir]=runDir
    spec['command]=command
    spec['cancelCommand]=or(cancelCommand "")
    spec['launchLog]=launchLog
    spec['resultPath]=or(resultPath "")
    spec['targetKey]=or(targetKey runDir)
    spec['publicationRequired]=and(publicationRequired t)
    spec
  )
)

procedure(cadBatchSpecSet(spec key value)
  spec[key]=value
  spec
)

procedure(cadBatchTaskId(index)
  sprintf(nil "%03d" index)
)

procedure(cadBatchJobName(flow batchId taskId)
  let((name)
    name=cadBatchSafeToken(strcat("cad_" lowerCase(flow) "_" batchId "_" taskId))
    if(strlen(name)>120 then substring(name 1 120) else name)
  )
)

procedure(cadBatchPythonExe(flow)
  or(cadBatchAdapterPythonExe(flow) "python3")
)

procedure(cadBatchPythonEntry()
  let((toolsRoot override)
    override=SICO_envValue("SICO_BATCH_PYTHON_ENTRY")
    toolsRoot=SICO_toolsRoot()
    or(SICO_nativeEntry("sico-batch") override
       when(toolsRoot
         simplifyFilename(strcat(toolsRoot "/common/python/sico-batch") t)))
  )
)

procedure(cadBatchControllerCommand(flow command manifest @optional argument)
  let((python entry result)
    python=cadBatchPythonExe(flow)
    entry=cadBatchPythonEntry()
    when(and(python entry)
      result=strcat(
        "exec env -u PYTHONHOME -u PYTHONPATH " SICO_tempEnvironment()
        "LD_LIBRARY_PATH="
        SICO_shellQuote(SICO_pythonLibraryPath()) " "
        SICO_backendCommand("sico-batch" python entry) " " command " "
        SICO_shellQuote(manifest))
      when(argument
        result=strcat(result " " SICO_shellQuote(argument)))
      result
    )
  )
)

procedure(cadBatchWriteManifest(meta)
  prog((outFile path spec)
    path=meta['manifest]
    unless(meta['tempDir]
      return(nil))
    outFile=outfile(path "w")
    unless(outFile
      return(nil))
    SICO_tomlWriteSection(outFile "batch")
    fprintf(outFile "schema_version = 1\n")
    SICO_tomlWriteString(outFile "batch_id" meta['id])
    SICO_tomlWriteString(outFile "flow" meta['flow])
    fprintf(outFile "parallel_cells = %d\n" meta['parallelCells])
    SICO_tomlWriteString(outFile "temp_dir" meta['tempDir])
    SICO_tomlWriteString(outFile "status_json" meta['statusJson])
    SICO_tomlWriteString(outFile "status_tsv" meta['statusTsv])
    foreach(spec meta['specs]
      fprintf(outFile "\n[[tasks]]\n")
      SICO_tomlWriteString(outFile "id" spec['id])
      SICO_tomlWriteString(outFile "label" spec['label])
      SICO_tomlWriteString(outFile "command" spec['command])
      SICO_tomlWriteString(outFile "cancel_command" spec['cancelCommand])
      SICO_tomlWriteString(outFile "run_dir" spec['runDir])
      SICO_tomlWriteString(outFile "config" spec['config])
      SICO_tomlWriteString(outFile "launch_log" spec['launchLog])
      SICO_tomlWriteString(outFile "result_path" spec['resultPath])
      SICO_tomlWriteBool(outFile "publication_required"
                         spec['publicationRequired])
    )
    close(outFile)
    return(path)
  )
)

procedure(cadBatchBuildMeta(form flow batchId root specs postProcessor)
  let((meta)
    meta=makeTable(strcat("cadBatchRun" batchId) nil)
    meta['id]=batchId
    meta['flow]=flow
    meta['root]=root
    meta['specs]=specs
    meta['parallelCells]=atoi(form~>batchParallelCells~>value)
    meta['tempDir]=SICO_tempDir()
    meta['manifest]=strcat(root "/batch.toml")
    meta['statusJson]=strcat(root "/status.json")
    meta['statusTsv]=strcat(root "/status.tsv")
    meta['batchLog]=strcat(root "/batch.launch.log")
    meta['publicationTsv]=strcat(root "/publication.tsv")
    meta['postProcessor]=postProcessor
    meta['done]=nil
    meta['cancelRequested]=nil
    meta
  )
)

procedure(cadBatchSpecById(meta id)
  let((found spec)
    foreach(spec meta['specs]
      when(and(!found spec['id]==id)
        found=spec))
    found
  )
)

procedure(cadBatchSpecsIndependentP(specs)
  let((valid runDirs configs targets spec)
    valid=t
    runDirs=nil
    configs=nil
    targets=nil
    foreach(spec specs
      when(or(member(spec['runDir] runDirs)
              member(spec['config] configs)
              member(spec['targetKey] targets))
        valid=nil)
      runDirs=cons(spec['runDir] runDirs)
      configs=cons(spec['config] configs)
      targets=cons(spec['targetKey] targets)
    )
    valid
  )
)

procedure(cadBatchLaunch(meta)
  prog((command cid flowLogRequest)
    unless(meta['tempDir]
      cadBatchMessage('e meta['flow] "Cannot create the CAD temporary directory.")
      return(nil))
    unless(cadBatchWriteManifest(meta)
      cadBatchMessage('e meta['flow] "Cannot create the batch manifest.")
      return(nil))
    command=cadBatchControllerCommand(meta['flow] "run" meta['manifest])
    unless(command
      cadBatchMessage('e meta['flow] "Cannot resolve the batch Python runtime.")
      return(nil))
    info("<INFO> %s Batch::MANIFEST=\"%s\"\n" meta['flow] meta['manifest])
    info("<INFO> %s Batch::LOG=\"%s\"\n" meta['flow] meta['batchLog])
    cid=ipcBeginProcess(command "" 'cadBatchIpcDataHandler
          'cadBatchIpcErrHandler 'cadBatchIpcPostFunc meta['batchLog])
    unless(cid
      cadBatchMessage('e meta['flow] "Failed to start the batch controller.")
      return(nil))
    meta['cid]=cid
    cadBatchRuns[meta['id]]=meta
    cadBatchByCid[cid]=meta['id]
    ipcActivateBatch(cid)
    flowLogRequest=GUI_flowLogOpen(strcat(meta['flow] " Batch") meta['batchLog])
    meta['flowLogRequest]=flowLogRequest
    cadBatchMonitorDisplay(meta)
    return(t)
  )
)

procedure(cadBatchCancel(meta)
  let((cid)
    cid=meta['cid]
    when(and(cid !meta['done])
      meta['cancelRequested]=t
      errset(ipcSignalProcess(cid 'TERM) t)
      info("<INFO> %s Batch::Cancellation requested.\n" meta['flow])
      cadBatchMonitorRefresh(meta)
      t
    )
  )
)

procedure(cadBatchIpcDataHandler(cid data)
  nil
)

procedure(cadBatchIpcErrHandler(cid data)
  nil
)

procedure(cadBatchIpcPostFunc(cid exitStatus)
  let((batchId meta processor protected)
    batchId=cadBatchByCid[cid]
    meta=cadBatchRuns[batchId]
    when(meta
      processor=meta['postProcessor]
      when(processor
        protected=errset(funcall(processor meta exitStatus) t)
        if(protected then
          when(numberp(car(protected))
            exitStatus=car(protected))
        else
          warn("<WARN> %s Batch::Post-processing failed.\n" meta['flow])
          exitStatus=1
        )
      )
      meta['exitStatus]=exitStatus
      meta['done]=t
      meta['cid]=nil
      cadBatchMonitorRefresh(meta t)
      if(exitStatus==0 then
        info("<INFO> %s Batch::Finished. STATUS=\"%s\"\n"
          meta['flow] meta['statusTsv])
      else
        warn("<WARN> %s Batch::Finished with exit status %L. STATUS=\"%s\"\n"
          meta['flow] exitStatus meta['statusTsv]))
    )
    remove(cid cadBatchByCid)
  )
)

procedure(cadBatchCoreRevision()
  "20260923.batch.commands.v3"
)

sicoBatchCoreVersion="20260923.batch.commands.v3"
)
