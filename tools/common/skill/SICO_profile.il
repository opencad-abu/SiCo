;------------------------------------------------------------------------------
; Shared versioned configuration-profile bridge for CAD flow forms.
;------------------------------------------------------------------------------

unless(and(boundp('sicoProfileVersion)
             sicoProfileVersion=="20260924.profile.environment.v4")

unless(boundp('cadProfileAdapters)
  cadProfileAdapters=makeTable("cadProfileAdapters" nil))

unless(boundp('cadProfileLoadData)
  cadProfileLoadData=nil)

procedure(SICO_profileFlowKey(flow)
  upperCase(if(flow then sprintf(nil "%s" flow) else ""))
)

procedure(SICO_profileRegisterAdapter(flow collectCB applyCB)
  let((adapter key)
    key=SICO_profileFlowKey(flow)
    adapter=makeTable(strcat("cadProfileAdapter" key) nil)
    adapter['flow]=key
    adapter['collectCB]=collectCB
    adapter['applyCB]=applyCB
    cadProfileAdapters[key]=adapter
    adapter
  )
)

procedure(SICO_profileAdapter(flow)
  cadProfileAdapters[SICO_profileFlowKey(flow)]
)

procedure(SICO_profileDataValue(data path @optional default)
  let((pair)
    pair=assoc(path data)
    if(pair then cadr(pair) else default)
  )
)

procedure(SICO_profileFormValue(form fieldName @optional default)
  let((field)
    field=get(form fieldName)
    if(field then field~>value else default)
  )
)

procedure(SICO_profileSetFormValue(form fieldName value)
  let((field)
    field=get(form fieldName)
    when(field
      field~>value=value
      t)
  )
)

procedure(SICO_profilePathValue(data path @optional default)
  let((resolved value)
    resolved=SICO_profileDataValue(data strcat(path ".resolved") nil)
    value=SICO_profileDataValue(data path default)
    if(and(resolved stringp(resolved) strlen(resolved)>0)
      then resolved else value)
  )
)

procedure(SICO_profileCollectFields(form specs)
  let((entries)
    entries=nil
    foreach(spec specs
      entries=append1(entries
        list(car(spec) SICO_profileFormValue(form cadr(spec) caddr(spec)))))
    entries
  )
)

procedure(SICO_profileApplyFields(form data specs)
  foreach(spec specs
    SICO_profileSetFormValue(
      form cadr(spec)
      if(cadddr(spec) then
        SICO_profilePathValue(data car(spec) caddr(spec))
      else
        SICO_profileDataValue(data car(spec) caddr(spec)))))
  t
)

procedure(SICO_profileSetLoadData(flow version data)
  cadProfileLoadData=list(SICO_profileFlowKey(flow) version data)
)

procedure(SICO_profileDefaultPath(flow)
  or(SICO_envValue(strcat(SICO_profileFlowKey(flow) "_CONFIG")) "")
)

procedure(SICO_profilePythonExe(flow)
  let((key pythonRoot)
    key=SICO_profileFlowKey(flow)
    pythonRoot=SICO_envValue("SICO_PYTHON_ROOT")
    or(SICO_envValue(strcat(key "_PYTHON"))
       SICO_envValue("SICO_PYTHON")
       when(pythonRoot
         simplifyFilename(strcat(pythonRoot "/bin/python3") t))
       "python3")
  )
)

procedure(SICO_profilePythonEntry()
  let((toolsRoot)
    toolsRoot=SICO_toolsRoot()
    or(SICO_nativeEntry("sico-profile") SICO_envValue("SICO_PROFILE_PYTHON_ENTRY")
       when(toolsRoot
         simplifyFilename(strcat(toolsRoot "/common/python/sico-profile") t)))
  )
)

procedure(SICO_profileLaunchCommand(flow args)
  strcat("env -u PYTHONHOME -u PYTHONPATH "
         SICO_tempEnvironment()
         "LD_LIBRARY_PATH=" SICO_shellQuote(SICO_pythonLibraryPath()) " "
         SICO_backendCommand("sico-profile" SICO_profilePythonExe(flow)
           SICO_profilePythonEntry()) " " args)
)

procedure(SICO_profileMessage(severity flow message)
  if(isCallable('GUI_messageApp) then
    GUI_messageApp(severity SICO_profileFlowKey(flow) message)
  else
    warn("%s Profile: %s\n" SICO_profileFlowKey(flow) message))
)

procedure(SICO_profilePath(form)
  let((value)
    value=when(form~>profileFile form~>profileFile~>value)
    if(and(value strlen(value)>0) then simplifyFilename(value t) else "")
  )
)

procedure(SICO_profileStringList(value)
  let((result item)
    result=nil
    foreach(item or(value nil)
      result=append1(result if(item then sprintf(nil "%s" item) else "")))
    result
  )
)

procedure(SICO_profileResolvedPathP(path)
  and(stringp(path) strlen(path)>9
      substring(path strlen(path)-8 9)==".resolved")
)

procedure(SICO_profileSamePathP(left right)
  and(stringp(left) stringp(right)
      strlen(left)>0 strlen(right)>0
      simplifyFilename(left t)==simplifyFilename(right t))
)

procedure(SICO_profilePathStates(data)
  let((result path rawPath)
    result=nil
    foreach(entry data
      path=car(entry)
      when(SICO_profileResolvedPathP(path)
        rawPath=substring(path 1 strlen(path)-9)
        result=cons(
          list(rawPath SICO_profileDataValue(data strcat(rawPath ".raw") "")
               cadr(entry))
          result)))
    result
  )
)

procedure(SICO_profilePreserveRawPaths(form entries destination)
  let((result states state value sourcePath)
    result=nil
    states=get(form 'cadProfilePathStates)
    sourcePath=get(form 'cadProfileSourcePath)
    foreach(entry entries
      state=assoc(car(entry) states)
      value=cadr(entry)
      if(and(state
             SICO_profileSamePathP(sourcePath destination)
             equal(value caddr(state))) then
        result=append1(result list(car(entry) cadr(state)))
      else
        result=append1(result entry))
    )
    result
  )
)

procedure(SICO_profileWriteValue(outFile value)
  let((first item)
    cond(
      (value==nil fprintf(outFile "false"))
      (stringp(value) fprintf(outFile "%s" SICO_tomlString(value)))
      (integerp(value) fprintf(outFile "%d" value))
      (listp(value)
        fprintf(outFile "[")
        first=t
        foreach(item value
          unless(first fprintf(outFile ", "))
          SICO_profileWriteValue(outFile item)
          first=nil)
        fprintf(outFile "]"))
      (t fprintf(outFile "%s" SICO_tomlBool(value))))
  )
)

procedure(SICO_profileArrayPathP(path)
  member(path '(
    "batch.tasks"
    "drc.rule_select_groups"
    "drc.rule_select_checks"
    "extract.corners"
    "extract.corner_temperatures"
    "input.cells"
    "lvs.svdb_query"
  ))
)

procedure(SICO_profileWriteEntryValue(outFile path value)
  if(and(value==nil SICO_profileArrayPathP(path)) then
    fprintf(outFile "[]")
  else
    SICO_profileWriteValue(outFile value))
)

procedure(SICO_profileWriteKey(outFile key value)
  fprintf(outFile "%s = " key)
  SICO_profileWriteValue(outFile value)
  fprintf(outFile "\n")
)

procedure(SICO_profileEntrySection(entry)
  let((dot path)
    path=car(entry)
    dot=rindex(path ".")
    if(dot then substring(path 1 strlen(path)-strlen(dot)) else "")
  )
)

procedure(SICO_profileEntryLessP(left right)
  let((leftSection rightSection)
    leftSection=SICO_profileEntrySection(left)
    rightSection=SICO_profileEntrySection(right)
    cond(
      (leftSection==rightSection alphalessp(car(left) car(right)))
      (strlen(leftSection)<strlen(rightSection) t)
      (strlen(leftSection)>strlen(rightSection) nil)
      (t alphalessp(leftSection rightSection)))
  )
)

procedure(SICO_profileUniqueEntries(entries)
  let((unique entry previous conflict)
    unique=nil
    conflict=nil
    foreach(entry entries
      previous=assoc(car(entry) unique)
      if(previous then
        unless(equal(cadr(previous) cadr(entry))
          warn("<WARN> CAD Profile: conflicting duplicate key %s; save rejected.\n"
               car(entry))
          conflict=t)
      else
        unique=cons(entry unique))
    )
    if(conflict then nil else unique)
  )
)

procedure(SICO_profileWriteEntries(outFile entries)
  prog((current dot path section key value unique)
    current=""
    unique=SICO_profileUniqueEntries(entries)
    when(and(entries !unique) return(nil))
    foreach(entry sort(copy(unique) 'SICO_profileEntryLessP)
      path=car(entry)
      value=cadr(entry)
      dot=rindex(path ".")
      if(dot then
        progn(
          section=substring(path 1 strlen(path)-strlen(dot))
          key=substring(dot 2 strlen(dot)-1)
        )
      else
        progn(section="" key=path))
      unless(section==current
        current=section
        when(strlen(section)>0
          SICO_tomlWriteSection(outFile section)))
      fprintf(outFile "%s = " key)
      SICO_profileWriteEntryValue(outFile path value)
      fprintf(outFile "\n"))
    return(t)
  )
)

procedure(SICO_profilePersistentEntryP(entry)
  let((path)
    path=car(entry)
    and(stringp(path)
        path!="profile.path"
        !SICO_profileResolvedPathP(path)
        !and(strlen(path)>4 substring(path strlen(path)-3 4)==".raw"))
  )
)

procedure(SICO_profilePersistentEntries(entries)
  setof(entry entries SICO_profilePersistentEntryP(entry))
)

procedure(SICO_profileWrite(path flow entries)
  prog((command outFile result status tempFile writeOK)
    result=nil
    tempFile=SICO_tempPath("cad-profile-saveXXXX")
    unless(tempFile return(nil))
    tempFile=makeTempFileName(tempFile)
    unwindProtect(
      progn(
        outFile=outfile(tempFile "w")
        when(outFile
          SICO_tomlWriteSection(outFile "cad_config")
          SICO_tomlWriteString(outFile "format" "sico-flow-profile")
          SICO_profileWriteKey(outFile "version" 1)
          SICO_tomlWriteString(outFile "flow" SICO_profileFlowKey(flow))
          writeOK=SICO_profileWriteEntries(
            outFile SICO_profilePersistentEntries(entries))
          close(outFile)
          outFile=nil
          when(writeOK
            command=SICO_profileLaunchCommand(
              flow strcat("validate --flow " SICO_profileFlowKey(flow) " "
                          SICO_shellQuote(tempFile)))
            status=system(command)
            when(status==0
              command=SICO_profileLaunchCommand(
                flow strcat("publish --flow " SICO_profileFlowKey(flow) " "
                            SICO_shellQuote(tempFile) " " SICO_shellQuote(path)))
              status=system(command)
              result=status==0))))
      progn(
        when(portp(outFile) close(outFile))
        when(isFile(tempFile) deleteFile(tempFile))))
    return(result)
  )
)

procedure(SICO_profileCollect(form flow)
  let((adapter callback)
    adapter=SICO_profileAdapter(flow)
    callback=when(adapter adapter['collectCB])
    when(and(callback isCallable(callback)) funcall(callback form))
  )
)

procedure(SICO_profileApply(form flow data)
  let((adapter callback protected rollback snapshot snapshotResult)
    adapter=SICO_profileAdapter(flow)
    callback=when(adapter adapter['applyCB])
    when(and(callback isCallable(callback))
      snapshotResult=errset(SICO_profileCollect(form flow) t)
      snapshot=when(snapshotResult car(snapshotResult))
      protected=errset(funcall(callback form data) t)
      if(and(protected car(protected)) then
        t
      else
        progn(
          when(snapshot
            rollback=errset(funcall(callback form snapshot) t)
            unless(and(rollback car(rollback))
              warn("%s Profile: rollback failed after an apply error.\n"
                   SICO_profileFlowKey(flow))))
          nil)))
  )
)

procedure(SICO_profileSaveTo(form flow path)
  let((entries entry parent sourcePath)
    cond(
      ((!path || path=="")
        SICO_profileMessage('e flow "Select a configuration filename first.") nil)
      (t
        path=simplifyFilename(path t)
        parent=simplifyFilename(strcat(path "/..") t)
        entry=SICO_profilePythonEntry()
        unless(and(isDir(parent) isWritable(parent))
          SICO_profileMessage('e flow
            "The configuration directory does not exist or is not writable.")
          return(nil))
        unless(and(entry isFile(entry))
          SICO_profileMessage('e flow "The shared profile parser is unavailable.")
          return(nil))
        sourcePath=get(form 'cadProfileSourcePath)
        entries=SICO_profileCollect(form flow)
        entries=SICO_profilePreserveRawPaths(form entries path)
        if(and(entries SICO_profileWrite(path flow entries)) then
          progn(
            form~>profileFile~>value=path
            putpropq(form path cadProfileSourcePath)
            unless(SICO_profileSamePathP(sourcePath path)
              putpropq(form nil cadProfilePathStates))
            info("<INFO> %s configuration saved to \"%s\".\n"
                 SICO_profileFlowKey(flow) path)
            t)
        else
          progn(
            SICO_profileMessage('e flow "Cannot save the configuration profile.")
            nil))))
  )
)

procedure(SICO_profileSaveCB(form flow)
  let((path)
    path=SICO_profilePath(form)
    if(path=="" then
      SICO_profileSaveAsCB(form flow)
      else SICO_profileSaveTo(form flow path)))
)

procedure(SICO_profileSaveAsCB(form flow)
  let((defaultPath selection path)
    defaultPath=SICO_profilePath(form)
    when(defaultPath==""
      defaultPath=strcat(lowerCase(SICO_profileFlowKey(flow)) "-profile.toml"))
    selection=hiGetAnyFile(
      ?workingDir pwd() ?filter "TOML Profiles (*.toml);;All Files (*)"
      ?defaultValue defaultPath ?caption "Save Configuration Profile"
      ?modal t ?acceptMode 'save ?confirmOverwrite t)
    when(selection
      path=simplifyFilename(buildString(selection "/") t)
      SICO_profileSaveTo(form flow path)))
)

procedure(SICO_profileLoadCB(form flow)
  prog((command dataFile entry path protected status)
    path=SICO_profilePath(form)
    unless(and(path!="" isFile(path))
      SICO_profileMessage('e flow "The configuration profile is not accessible.")
      return(nil))
    entry=SICO_profilePythonEntry()
    unless(and(entry isFile(entry))
      SICO_profileMessage('e flow "The shared profile parser is unavailable.")
      return(nil))
    dataFile=SICO_tempPath("cad-profile-loadXXXX")
    unless(dataFile return(nil))
    dataFile=makeTempFileName(dataFile)
    command=SICO_profileLaunchCommand(
      flow strcat("form-data --flow " SICO_profileFlowKey(flow) " "
                  SICO_shellQuote(path) " " SICO_shellQuote(dataFile)))
    info("<INFO> %s::Loading configuration \"%s\".\n"
         SICO_profileFlowKey(flow) path)
    status=system(command)
    unless(status==0
      when(isFile(dataFile) deleteFile(dataFile))
      SICO_profileMessage('e flow
        "Cannot parse the configuration profile. Check the CIW message.")
      return(nil))
    cadProfileLoadData=nil
    protected=errset(load(dataFile) t)
    when(isFile(dataFile) deleteFile(dataFile))
    unless(and(protected cadProfileLoadData
               car(cadProfileLoadData)==SICO_profileFlowKey(flow)
               cadr(cadProfileLoadData)==1)
      SICO_profileMessage('e flow "The configuration handoff is invalid.")
      return(nil))
    unless(SICO_profileApply(form flow caddr(cadProfileLoadData))
      SICO_profileMessage('e flow "Cannot apply the configuration profile.")
      return(nil))
    putpropq(form SICO_profilePathStates(caddr(cadProfileLoadData))
             cadProfilePathStates)
    putpropq(form path cadProfileSourcePath)
    form~>profileFile~>value=path
    info("<INFO> %s configuration loaded from \"%s\".\n"
         SICO_profileFlowKey(flow) path)
    return(t)
  )
)

procedure(SICO_profileRevision()
  "20260924.profile.environment.v4"
)

sicoProfileVersion="20260924.profile.environment.v4"
)
