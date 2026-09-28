;------------------------------------------------------------------------------
; SiCo::MTS Netlistor launcher.
;
; This file runs only in the current Proj Virtuoso process.  It snapshots the
; current target cds.lib and DD library paths, then starts a separate Python
; GUI.  It never opens or parses a source cds.lib and accepts no SKILL text
; from the child process.
;------------------------------------------------------------------------------

unless(and(boundp('mtsNetlistorVersion)
             mtsNetlistorVersion=="20260924.sico.mts.launcher.v7"
             isCallable('mtsNetlistorStart))

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  let((launcherDirectory commonSkill)
    launcherDirectory=buildString(reverse(cdr(reverse(
      parseString(simplifyFilename(get_filename(piport) t) "/")))) "/")
    commonSkill=simplifyFilename(strcat("/" launcherDirectory "/../../common/skill"))
    load(strcat(commonSkill "/SICO_toml.il"))
    load(strcat(commonSkill "/SICO_guiProtocol.il"))))

unless(boundp('mtsNetlistorProcesses)
  mtsNetlistorProcesses=makeTable("mtsNetlistorProcesses" nil)
)

procedure(mtsNetlistorRevision()
  "20260924.sico.mts.launcher.v7"
)

procedure(mtsNetlistorBrand()
  cond(
    (isCallable('SICO_logo) SICO_logo())
    (isCallable('witMenuLogo) witMenuLogo())
    (t "SiCo")
  )
)

procedure(mtsNetlistorOaNameP(value)
  and(stringp(value)
      strlen(value)>0
      strlen(value)<=255
      rexMatchp("^[A-Za-z_][A-Za-z0-9_$]*$" value))
)

procedure(mtsNetlistorParseOpenView(line)
  let((fields library cell view)
    fields=when(stringp(line) parseString(line "\t" t))
    if(length(fields)!=4 || car(fields)!="MTS_OPEN_VIEW" then
      list(nil nil nil nil "expected MTS_OPEN_VIEW with three fields")
    else
      library=cadr(fields)
      cell=caddr(fields)
      view=cadddr(fields)
      cond(
        (!mtsNetlistorOaNameP(library)
          list(nil nil nil nil "invalid result library"))
        (!mtsNetlistorOaNameP(cell)
          list(nil nil nil nil "invalid result cell"))
        (and(!member(view list("symbol" "spectreText" "spiceText"))
             view!="spectre")
          list(nil nil nil nil "unsupported result view"))
        (t list(t library cell view "")))))
)

procedure(mtsNetlistorSessionLibraryP(state library)
  let((found row)
    foreach(row when(state state['libraries])
      when(and(listp(row) car(row)==library) found=t))
    found)
)

procedure(mtsNetlistorHandleOpenView(cid line)
  let((cell library parsed protected state view viewObject window)
    state=mtsNetlistorProcesses[cid]
    parsed=mtsNetlistorParseOpenView(line)
    if(!car(parsed) then
      warn("%s::MTS Netlistor rejected an open-view request: %s\n"
        mtsNetlistorBrand() nth(4 parsed))
    else
      library=cadr(parsed)
      cell=caddr(parsed)
      view=cadddr(parsed)
      cond(
        (!state
          warn("%s::MTS Netlistor ignored an open-view request from an unknown child.\n"
            mtsNetlistorBrand()))
        (!mtsNetlistorSessionLibraryP(state library)
          warn("%s::MTS Netlistor result library is outside this session: %s\n"
            mtsNetlistorBrand() library))
        (t
          viewObject=ddGetObj(library cell view)
          if(!viewObject then
            warn("%s::MTS Netlistor result view is unavailable: %s/%s/%s\n"
              mtsNetlistorBrand() library cell view)
          else
            protected=errset(
              deOpenCellView(library cell view "" nil "r") t)
            window=when(protected car(protected))
            if(window then
              info("<INFO> %s::MTS Netlistor opened %s/%s/%s read-only.\n"
                mtsNetlistorBrand() library cell view)
            else
              warn("%s::MTS Netlistor could not open result view: %s/%s/%s\n"
                mtsNetlistorBrand() library cell view))))))
    window)
)

procedure(mtsNetlistorDataHandler(cid data)
  let((buffer length start index line state)
    state=mtsNetlistorProcesses[cid]
    when(state
      buffer=strcat(or(state['buffer] "") or(data ""))
      length=strlen(buffer)
      start=1
      for(index 1 length
        when(substring(buffer index 1)=="\n"
          line=substring(buffer start index-start)
          when(and(strlen(line)>=14
                   substring(line 1 14)=="MTS_OPEN_VIEW\t")
            mtsNetlistorHandleOpenView(cid line))
          start=index+1))
      state['buffer]=if(start<=length then
        substring(buffer start length-start+1) else "")
      when(strlen(state['buffer])>4096
        state['buffer]=""
        warn("%s::MTS Netlistor discarded an overlong IPC request.\n"
          mtsNetlistorBrand())))
    nil)
)

procedure(mtsNetlistorShellQuote(value)
  let((text out index character)
    text=if(value then sprintf(nil "%s" value) else "")
    out="'"
    for(index 1 strlen(text)
      character=substring(text index 1)
      if(character=="'" then
        out=strcat(out "'\"'\"'")
      else
        out=strcat(out character)
      )
    )
    strcat(out "'")
  )
)

procedure(mtsNetlistorJsonEscape(value)
  let((text out index character)
    text=if(value then sprintf(nil "%s" value) else "")
    out=""
    for(index 1 strlen(text)
      character=substring(text index 1)
      case(character
        ("\\" out=strcat(out "\\\\"))
        ("\"" out=strcat(out "\\\""))
        ("\n" out=strcat(out "\\n"))
        ("\r" out=strcat(out "\\r"))
        ("\t" out=strcat(out "\\t"))
        (t out=strcat(out character))
      )
    )
    out
  )
)

procedure(mtsNetlistorProcStartTime(pid)
  let((path input line fields start)
    path=strcat("/proc/" sprintf(nil "%d" pid) "/stat")
    unless(isFile(path) return(nil))
    input=infile(path)
    line=gets(line input)
    close(input)
    unless(line return(nil))
    ; The command name is enclosed in parentheses.  Splitting after the
    ; final ')' keeps field 22 (starttime) stable for normal Virtuoso names.
    line=car(last(parseString(line ")")))
    fields=parseString(line)
    when(length(fields)>19
      start=nth(19 fields)
      when(and(start stringp(start) rexMatchp("^[0-9]+$" start)) start)
    )
  )
)

procedure(mtsNetlistorReadToken(path)
  let((input line token)
    input=infile(path)
    unless(input return(nil))
    line=gets(line input)
    close(input)
    when(line
      token=car(parseString(line))
      when(and(token stringp(token) strlen(token)>0) token)
    )
  )
)

procedure(mtsNetlistorDigestValidP(value)
  and(stringp(value)
      strlen(value)==64
      rexMatchp("^[0-9a-fA-F]+$" value))
)

procedure(mtsNetlistorTargetLibraries()
  let((libraries rows library name readPath writePath physical)
    rows=nil
    libraries=ddGetLibList()
    foreach(library libraries
      name=when(library~>name sprintf(nil "%s" library~>name))
      readPath=errset(ddGetObjReadPath(library) nil)
      readPath=when(and(readPath car(readPath)) car(readPath))
      writePath=errset(ddGetObjWritePath(library) nil)
      writePath=when(and(writePath car(writePath)) car(writePath))
      physical=or(writePath readPath)
      when(and(name physical stringp(physical) strlen(physical)>0)
        rows=append1(rows list(name physical)))
    )
    rows
  )
)

procedure(mtsNetlistorWriteSession(path cdsLibrary ownerPid ownerStart libraries)
  let((output first row)
    output=outfile(path "w")
    unless(output return(nil))
    fprintf(output "{\n")
    fprintf(output "  \"schema_version\": 1,\n")
    fprintf(output "  \"owner_pid\": %d,\n" ownerPid)
    fprintf(output "  \"owner_start_time\": \"%s\",\n"
      mtsNetlistorJsonEscape(ownerStart))
    fprintf(output "  \"target_cds_lib\": \"%s\",\n"
      mtsNetlistorJsonEscape(cdsLibrary))
    ; Keep the digest field explicit until the target cds.lib snapshot is
    ; hashed and finalized below.  Versioning makes partial launch failures
    ; visible instead of silently accepting a stale target map.
    fprintf(output "  \"target_cds_lib_digest\": \"\",\n")
    fprintf(output "  \"target_library_paths\": {")
    first=t
    foreach(row libraries
      unless(first fprintf(output ","))
      first=nil
      fprintf(output "\n    \"%s\": \"%s\""
        mtsNetlistorJsonEscape(car(row))
        mtsNetlistorJsonEscape(cadr(row)))
    )
    when(libraries fprintf(output "\n  "))
    fprintf(output "},\n  \"tool_version\": \"1\"\n}\n")
    close(output)
    path
  )
)

procedure(mtsNetlistorPythonExe()
  let((root)
    root=SICO_envValue("SICO_PYTHON_ROOT")
    or(SICO_envValue("SICO_PYTHON")
       when(and(root strlen(root)>0)
         simplifyFilename(strcat(root "/bin/python3") t))
       "python3")
  )
)

procedure(mtsNetlistorToolsRoot()
  SICO_toolsRoot()
)

procedure(mtsNetlistorModuleRoot()
  let((moduleRoot configured)
    moduleRoot=SICO_installationPath(SICO_installationRoot() "tools/mtsnl")
    configured=SICO_envValue("MTS_NETLISTOR_ROOT")
    when(and(configured SICO_installationCanonicalPath(configured)!=moduleRoot)
      error("MTS_NETLISTOR_ROOT conflicts with the SiCo installation\n"))
    moduleRoot)
)

procedure(mtsNetlistorLaunchCommand(sessionPath parentPid)
  let((python moduleRoot entry command)
    python=mtsNetlistorPythonExe()
    moduleRoot=mtsNetlistorModuleRoot()
    entry=or(SICO_commandNative("mts-netlistor")
      when(moduleRoot simplifyFilename(strcat(moduleRoot "/bin/mts-netlistor") t)))
    unless(and(python entry isFile(entry)) return(nil))
    command=strcat("env SICO_PYTHON=" mtsNetlistorShellQuote(python) " "
                   mtsNetlistorShellQuote(entry)
                   " gui --session " mtsNetlistorShellQuote(sessionPath)
                   " --parent-pid " sprintf(nil "%d" parentPid))
    SICO_guiPythonLaunchCommand(command "MTS_NETLISTOR_ORIG_LD_LIBRARY_PATH"
      "-u LD_PRELOAD -u LD_AUDIT -u CDS_MPS_SESSION -u CDS_MPS_HOST -u CDS_MPS_PORT ")
  )
)

procedure(mtsNetlistorStopAll()
  let((cid state)
    foreach(cid mtsNetlistorProcesses
      state=mtsNetlistorProcesses[cid]
      when(and(state ipcIsAliveProcess(cid))
        errset(ipcKillProcess(cid) nil)))
      when(and(state state['session] isFile(state['session]))
        errset(deleteFile(state['session]) nil))
    mtsNetlistorProcesses=makeTable("mtsNetlistorProcesses" nil)
    nil
  )
)

procedure(mtsNetlistorRunningProcess()
  let((cid running)
    foreach(cid mtsNetlistorProcesses
      when(and(!running ipcIsAliveProcess(cid))
        running=cid))
    running
  )
)

procedure(mtsNetlistorPostFunc(cid exitStatus)
  let((state logPath)
    state=mtsNetlistorProcesses[cid]
    remove(cid mtsNetlistorProcesses)
    logPath=when(state state['log])
    when(and(state state['session] isFile(state['session]))
      errset(deleteFile(state['session]) nil))
    when(and(state exitStatus==0 isCallable('ddUpdateLibList))
      errset(ddUpdateLibList() nil))
    when(and(state exitStatus!=0)
      warn("%s::MTS Netlistor exited with status %L. Log: %s\n"
        mtsNetlistorBrand() exitStatus or(logPath "(unavailable)")))
    unless(state
      warn("%s::MTS Netlistor received an IPC callback for unknown child %L.\n"
        mtsNetlistorBrand() cid))
    )
  )

procedure(mtsNetlistorStart()
  prog((cdsLibrary ownerPid ownerStart libraries root session digestPath digest
        command ipcLogPath cid state running)
    running=mtsNetlistorRunningProcess()
    when(running
      info("<INFO> %s::MTS Netlistor is already running.\n"
        mtsNetlistorBrand())
      return(running))
    ownerPid=ipcGetPid()
    unless(and(integerp(ownerPid) ownerPid>1)
      warn("%s::MTS Netlistor cannot determine Virtuoso PID.\n"
        mtsNetlistorBrand())
      return(nil))
    cdsLibrary=ddGetStartup("cds.lib")
    unless(and(cdsLibrary stringp(cdsLibrary) isFile(cdsLibrary))
      warn("%s::MTS Netlistor cannot resolve the current target cds.lib.\n"
        mtsNetlistorBrand())
      return(nil))
    ownerStart=mtsNetlistorProcStartTime(ownerPid)
    unless(ownerStart
      warn("%s::MTS Netlistor cannot verify Virtuoso process identity.\n"
        mtsNetlistorBrand())
      return(nil))
    libraries=mtsNetlistorTargetLibraries()
    unless(libraries
      warn("%s::MTS Netlistor found no target libraries in the current session.\n"
        mtsNetlistorBrand())
      return(nil))
    root=SICO_tempDir()
    session=sprintf(nil "%s/mts-netlistor-session-%d.json" root ownerPid)
    digestPath=sprintf(nil "%s/mts-netlistor-session-%d.sha256" root ownerPid)
    unless(mtsNetlistorWriteSession(session cdsLibrary ownerPid ownerStart libraries)
      warn("%s::MTS Netlistor cannot write session descriptor.\n"
        mtsNetlistorBrand())
      return(nil))
    digest=strcat("/usr/bin/sha256sum " mtsNetlistorShellQuote(cdsLibrary)
                  " > " mtsNetlistorShellQuote(digestPath))
    unless(system(digest)==0
      warn("%s::MTS Netlistor cannot hash target cds.lib.\n"
        mtsNetlistorBrand())
      deleteFile(session)
      return(nil))
    digest=mtsNetlistorReadToken(digestPath)
    unless(mtsNetlistorDigestValidP(digest)
      warn("%s::MTS Netlistor received an invalid target cds.lib digest.\n"
        mtsNetlistorBrand())
      deleteFile(session)
      deleteFile(digestPath)
      return(nil))
    ; Replace the empty digest field without evaluating user data as SKILL.
    command=strcat("/bin/sed -i 's/\\\"target_cds_lib_digest\\\": \\\"\\\"/\\\"target_cds_lib_digest\\\": \\\""
                   digest "\\\"/' " mtsNetlistorShellQuote(session))
    unless(system(command)==0
      warn("%s::MTS Netlistor cannot finalize session descriptor.\n"
        mtsNetlistorBrand())
      deleteFile(session)
      deleteFile(digestPath)
      return(nil))
    deleteFile(digestPath)
    command=mtsNetlistorLaunchCommand(session ownerPid)
    unless(command
      warn("%s::MTS Netlistor Python entry is unavailable.\n"
        mtsNetlistorBrand())
      deleteFile(session)
      return(nil))
    ipcLogPath=sprintf(nil "%s/mts-netlistor-ipc-%d.log" root ownerPid)
    cid=ipcBeginProcess(command "" 'mtsNetlistorDataHandler nil
          'mtsNetlistorPostFunc ipcLogPath)
    unless(cid
      warn("%s::MTS Netlistor GUI could not be started.\n"
        mtsNetlistorBrand())
      deleteFile(session)
      return(nil))
    state=makeTable("mtsNetlistorProcess" nil)
    state['session]=session
    state['pid]=ownerPid
    state['log]=ipcLogPath
    state['libraries]=libraries
    state['buffer]=""
    mtsNetlistorProcesses[cid]=state
    when(isCallable('ipcActivateBatch)
      ipcActivateBatch(cid))
    info("<INFO> %s::MTS Netlistor started.\n"
      mtsNetlistorBrand())
    return(cid)
  )
)

procedure(mtsNetlistorInitializeRuntime()
  regExitBefore('mtsNetlistorStopAll)
  t
)

unless(getShellEnvVar("VIRTUOSO_CONTEXT_BUILD")=="1"
  mtsNetlistorInitializeRuntime()
)
mtsNetlistorVersion="20260924.sico.mts.launcher.v7"
)
