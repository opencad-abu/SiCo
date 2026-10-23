; Headless workflow environment, input policy and command selection.
; Loaded with AI_flows.il by the versioned AI parent.
unless(and(boundp('aiWorkflowVersion)
             aiWorkflowVersion=="20261020.sico.flow.v7"
             boundp('aiWorkflowState)
             aiWorkflowState['exitCleanupRegistered])

procedure(aiWorkflowEnv(name)
  let((value)
    value=getShellEnvVar(name)
    when(and(stringp(value) strlen(value)>0) value)))

procedure(aiWorkflowTempEnvironment()
  SICO_stateEnvironment())

procedure(aiWorkflowShellQuote(value)
  let((text output index character)
    text=if(value then sprintf(nil "%s" value) else "")
    output="'"
    for(index 1 strlen(text)
      character=substring(text index 1)
      output=strcat(output
        if(character=="'" then "'\"'\"'" else character)))
    strcat(output "'")))

procedure(aiWorkflowJsonEscape(value)
  let((text output index character code)
    text=if(stringp(value) then value else sprintf(nil "%L" value))
    output=""
    for(index 1 strlen(text)
      character=substring(text index 1)
      code=charToInt(concat(character))
      output=strcat(output
        case(character
          ("\\" "\\\\")
          ("\"" "\\\"")
          ("\b" "\\b")
          ("\f" "\\f")
          ("\n" "\\n")
          ("\r" "\\r")
          ("\t" "\\t")
          (t if(code<32 then sprintf(nil "\\u%04x" code) else character)))))
    output))

procedure(aiWorkflowError(code message)
  sprintf(nil
    "{\"ok\":false,\"error\":{\"code\":\"%s\",\"message\":\"%s\"}}"
    aiWorkflowJsonEscape(code) aiWorkflowJsonEscape(message)))

procedure(aiWorkflowSafeTextP(value limit)
  let(((safe t) index code)
    if(or(!stringp(value) strlen(value)<1 strlen(value)>limit) then
      nil
    else
      for(index 1 strlen(value)
        code=charToInt(concat(substring(value index 1)))
        when(or(code<32 code==127) safe=nil))
      safe)))

procedure(aiWorkflowInputPath(path)
  let((result resolved)
    when(and(aiWorkflowSafeTextP(path 8192)
               substring(path 1 1)=="/")
      result=errset(simplifyFilename(path) nil)
      resolved=car(result)
      when(and(stringp(resolved) substring(resolved 1 1)=="/"
                 isFile(resolved) fileLength(resolved)>0)
        resolved))))

procedure(aiWorkflowToolsRoot()
  let((result launcherRoot)
    when(isCallable('witMenuToolsRoot)
      result=errset(witMenuToolsRoot() nil)
      when(and(result stringp(car(result)) strlen(car(result))>0)
        launcherRoot=simplifyFilename(strcat(car(result) "/.."))))
    SICO_installationPath(SICO_installationRoot(launcherRoot) "tools")))

procedure(aiWorkflowPython(flow)
  let((installed productionRoot productionPython sharedPython)
    installed=SICO_installedPython()
    sharedPython=when(not(installed)
      SICO_environmentValue("SICO_PYTHON" list("CAD_PYTHON")))
    productionRoot=when(not(or(installed sharedPython))
      SICO_environmentValue("SICO_PYTHON_ROOT" list("CAD_PYTHON_ROOT")))
    when(and(getShellEnvVar("SICO_PYTHON_ROOT") productionRoot=="")
      error("SICO_PYTHON_ROOT must not be empty\n"))
    unless(and(sharedPython strlen(sharedPython)>0)
      sharedPython=when(getShellEnvVar("SICO_PYTHON") ""))
    productionPython=when(and(productionRoot
                                isExecutable(strcat(productionRoot "/bin/python3")))
      strcat(productionRoot "/bin/python3"))
    case(flow
      ("rce" or(aiWorkflowEnv("RCE_PYTHON") installed sharedPython
                 productionPython "/App/others/python3.9.13/bin/python3"))
      ("drc" or(aiWorkflowEnv("DRC_PYTHON") installed sharedPython
                 aiWorkflowEnv("RCE_PYTHON") productionPython "python3"))
      (("lvs" "gds" "cdl")
        or(aiWorkflowEnv("LVS_PYTHON") installed sharedPython
            aiWorkflowEnv("RCE_PYTHON") productionPython "python3"))
      ("lef" or(aiWorkflowEnv("LEF_PYTHON") installed sharedPython
                 aiWorkflowEnv("RCE_PYTHON") productionPython "python3"))
      ("dspf" or(aiWorkflowEnv("RCE_ANALYZER_PYTHON")
                  aiWorkflowEnv("RCE_PYTHON") installed sharedPython
                  productionPython "/App/others/python3.9.13/bin/python3")))))

procedure(aiWorkflowExecutableP(executable)
  let((path)
    when(aiWorkflowSafeTextP(executable 8192)
      path=aiWorkflowEnv("PATH")
      if(index(executable "/") then
        isExecutable(executable)
      else
        and(path isExecutable(executable parseString(path ":" t)))))))

procedure(aiWorkflowEntry(flow toolsRoot)
  let((configured relative entry native)
    native=SICO_commandNative(aiWorkflowCommandName(flow))
    case(flow
      (("rce" "dspf")
        configured=aiWorkflowEnv("RCE_PYTHON_ENTRY")
        relative="rce/python/rce")
      ("drc"
        configured=aiWorkflowEnv("DRC_PYTHON_ENTRY")
        relative="drc/python/drc")
      (("lvs" "gds" "cdl")
        configured=aiWorkflowEnv("LVS_PYTHON_ENTRY")
        relative="lvs/python/lvs")
      ("lef"
        configured=aiWorkflowEnv("LEF_PYTHON_ENTRY")
        relative="lef/python/lef"))
    entry=or(native configured when(toolsRoot strcat(toolsRoot "/" relative)))
    when(aiWorkflowSafeTextP(entry 8192)
      entry=simplifyFilename(entry t)
      when(and(isFile(entry) fileLength(entry)>0) entry))))

procedure(aiWorkflowCommandName(flow)
  case(flow
    (("rce" "dspf") "rce")
    (("lvs" "gds" "cdl") "lvs")
    ("drc" "drc")
    ("lef" "lefgen")
    (t error("Unknown SiCo workflow: %L\n" flow))))

procedure(aiWorkflowLibraryPath()
  or(SICO_environmentValue("SICO_SYSTEM_LD_LIBRARY_PATH"
       list("CAD_SYSTEM_LD_LIBRARY_PATH")) ""))

procedure(aiWorkflowStopValues(flow)
  case(flow
    ("rce" list("cdl" "gds" "lvs" "query" "extract"
                "xrc_lvs" "xrc_pdb" "xrc_fmt"))
    ("drc" list("gds" "drc"))
    ("lvs" list("cdl" "gds" "lvs"))
    (t nil)))

procedure(aiWorkflowValidate(flow mode dryRun stopAfter)
  cond(
    (or(!stringp(flow) !member(flow list("rce" "drc" "lvs" "gds" "cdl" "lef")))
      "flow must be one of rce, drc, lvs, gds, cdl, or lef")
    (or(!stringp(mode) !member(mode list("run" "generate")))
      "mode must be run or generate")
    (not(or(dryRun==t dryRun==nil)) "dryRun must be t or nil")
    (and(stopAfter
          or(!stringp(stopAfter)
              !member(stopAfter aiWorkflowStopValues(flow))))
      "stopAfter is not valid for the selected flow")
    (and(flow=="rce" mode=="generate" or(dryRun stopAfter))
      "RCE generate does not accept dryRun or stopAfter")
    (t nil)))
)
