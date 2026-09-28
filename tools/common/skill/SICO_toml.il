;------------------------------------------------------------------------------
; Shared TOML, shell, environment, and path helpers for CAD frontends.
;------------------------------------------------------------------------------

unless(and(boundp('sicoTomlVersion)
             sicoTomlVersion=="20260922.sico.temp.v6")

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  let((commonSkill)
    commonSkill=strcat("/" buildString(reverse(cdr(reverse(
      parseString(simplifyFilename(get_filename(piport) t) "/")))) "/"))
    foreach(name list("SICO_environment.il" "SICO_installation.il" "SICO_state.il"
                      "SICO_commands.il")
      load(strcat(commonSkill "/" name)))))

procedure(SICO_initializeWorkingDir()
  SICO_stateInitialize()
)

unless(or(getShellEnvVar("VIRTUOSO_CONTEXT_BUILD")=="1"
          and(boundp('cadContextBuilding) cadContextBuilding))
  SICO_initializeWorkingDir()
)

procedure(SICO_tomlString(value)
  let((s out i ch)
    s=if(value then sprintf(nil "%s" value) else "")
    out=""
    for(i 1 strlen(s)
      ch=substring(s i 1)
      case(ch
        ("\\" out=strcat(out "\\\\"))
        ("\"" out=strcat(out "\\\""))
        ("\n" out=strcat(out "\\n"))
        ("\t" out=strcat(out "\\t"))
        (t out=strcat(out ch))
      )
    )
    strcat("\"" out "\"")
  )
)

procedure(SICO_tomlBool(value)
  if(value then "true" else "false")
)

procedure(SICO_shellQuote(value)
  let((s out i ch)
    s=if(value then sprintf(nil "%s" value) else "")
    out="'"
    for(i 1 strlen(s)
      ch=substring(s i 1)
      if(ch == "'" then
        out=strcat(out "'\"'\"'")
      else
        out=strcat(out ch)
      )
    )
    strcat(out "'")
  )
)

procedure(SICO_absolutePath(path)
  let((value)
    value=if(path then sprintf(nil "%s" path) else "")
    if(strlen(value)>0 then simplifyFilename(value t) else "")
  )
)

procedure(SICO_tempDir()
  SICO_stateDirectory()
)

procedure(SICO_tempPath(name)
  let((dir value)
    dir=SICO_tempDir()
    value=if(name then sprintf(nil "%s" name) else "")
    if(and(dir strlen(value)>0 value!="." value!=".."
           !index(value "/")) then
      simplifyFilename(strcat(dir "/" value) t)
    else
      nil)
  )
)

procedure(SICO_tempEnvironment()
  SICO_stateEnvironment()
)

procedure(SICO_fileStem(path)
  let((value name parts extension stemLength)
    value=if(path then sprintf(nil "%s" path) else "")
    if(strlen(value)>0 then
      name=car(last(parseString(value "/")))
      parts=parseString(name ".")
      if(length(parts)>1 then
        extension=car(last(parts))
        stemLength=strlen(name)-strlen(extension)-1
        if(stemLength>0 then substring(name 1 stemLength) else name)
      else
        name)
    else
      "")
  )
)

procedure(SICO_logo()
  let((logo company)
    logo=SICO_envValue("LOGO")
    company=SICO_envValue("COMPANY")
    when(and(logo upperCase(logo)=="OCAD")
      logo=nil)
    when(and(company upperCase(company)=="OCAD")
      company=nil)
    or(logo company "SiCo")
  )
)

procedure(SICO_toolsRoot()
  SICO_installationPath(SICO_installationRoot() "tools")
)

procedure(SICO_pythonLibraryPath()
  or(SICO_envValue("SICO_SYSTEM_LD_LIBRARY_PATH") "")
)

procedure(SICO_nativeEntry(name)
  ; Compatibility facade; remove after all frontend consumers use SICO_commands.
  SICO_commandNative(name))

procedure(SICO_backendCommand(name python entry)
  SICO_commandInvocation(name python entry))

procedure(SICO_tomlWriteSection(outFile name)
  fprintf(outFile "\n[%s]\n" name)
)

procedure(SICO_tomlWriteString(outFile key value)
  fprintf(outFile "%s = %s\n" key SICO_tomlString(value))
)

procedure(SICO_tomlWriteStringList(outFile key values)
  let((rendered separator value)
    rendered="["
    separator=""
    foreach(value values
      rendered=strcat(rendered separator SICO_tomlString(value))
      separator=", "
    )
    fprintf(outFile "%s = %s]\n" key rendered)
  )
)

procedure(SICO_tomlWriteBool(outFile key value)
  fprintf(outFile "%s = %s\n" key SICO_tomlBool(value))
)

procedure(SICO_tomlRevision()
  "20260922.sico.temp.v6"
)

sicoTomlVersion="20260922.sico.temp.v6"
)
