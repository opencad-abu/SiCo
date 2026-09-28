; Installed command selection shared by independent frontends and AI workflows.
unless(and(boundp('sicoCommandsVersion)
           sicoCommandsVersion=="20260924.sico.commands.v4")

procedure(SICO_commandTool(name)
  case(name
    (("rce" "dspfana") "rce")
    (("lvs" "gdsout" "cdlout") "lvs")
    ("drc" "drc")
    ("lefgen" "lef")
    ("mts-netlistor" "mtsnl")
    (("sico-profile" "sico-batch" "sico-lsf" "lsfmonitor" "nl2view" "sico-state") "common")
    (t error("Unknown SiCo command: %L\n" name))))

procedure(SICO_commandElfP(path)
  let((port signature)
    when(and(isFile(path) isExecutable(path))
      port=infile(path)
      when(port
        signature=list(getc(port) getc(port) getc(port) getc(port))
        close(port)
        signature==list(intToChar(127) 'E 'L 'F)))))

procedure(SICO_commandNative(name)
  prog((installation tool entry runtime)
    installation=SICO_installationRoot()
    tool=SICO_commandTool(name)
    entry=SICO_installationPath(installation strcat("tools/" tool "/bin/" name))
    runtime=or(isDir(SICO_installationPath(installation "tools/common/context"))
               isDir(SICO_installationPath(installation "tools/ai/context"))
               isDir(SICO_installationPath(installation strcat("tools/" tool "/context"))))
    when(SICO_commandElfP(entry) return(entry))
    ; A source tree runs its tool sources; worker contexts installed there are
    ; explicit test dependencies, not evidence of a compiled installation.
    unless(SICO_installationSourceP()
      when(runtime error("Compiled SiCo entry is unavailable: %s\n" entry)))
    return(nil)))

procedure(SICO_commandQuote(value)
  let(((output "'") position character)
    for(position 1 strlen(value)
      character=substring(value position 1)
      output=strcat(output if(character=="'" then "'\"'\"'" else character)))
    strcat(output "'")))

procedure(SICO_commandInvocation(name python source)
  let((native)
    native=SICO_commandNative(name)
    if(native then
      strcat("env SICO_PYTHON=" SICO_commandQuote(python) " " SICO_commandQuote(native))
    else
      strcat(SICO_commandQuote(python) " -s " SICO_commandQuote(source)))))

procedure(SICO_commandsRevision() "20260924.sico.commands.v4")
sicoCommandsVersion="20260924.sico.commands.v4"
)
