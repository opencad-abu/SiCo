;------------------------------------------------------------------------------
; RCE-specific runtime helpers and compatibility aliases for SICO_toml.il.
;------------------------------------------------------------------------------

unless(and(boundp('rceTomlVersion)
             rceTomlVersion=="20260818.common.compat.v2")

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  let((commonSkill)
    commonSkill=strcat("/" buildString(reverse(cdr(reverse(
      parseString(simplifyFilename(get_filename(piport) t) "/")))) "/")
      "/../../common/skill/")
    load(strcat(commonSkill "SICO_toml.il"))))

procedure(RCE_tomlString(value) SICO_tomlString(value))
procedure(RCE_tomlBool(value) SICO_tomlBool(value))
procedure(RCE_shellQuote(value) SICO_shellQuote(value))
procedure(RCE_envValue(name) SICO_envValue(name))
procedure(RCE_absolutePath(path) SICO_absolutePath(path))
procedure(RCE_fileStem(path) SICO_fileStem(path))
procedure(RCE_logo() SICO_logo())
procedure(RCE_cadToolsRoot() SICO_toolsRoot())
procedure(RCE_pythonLibraryPath() SICO_pythonLibraryPath())
procedure(RCE_tomlWriteSection(outFile name)
  SICO_tomlWriteSection(outFile name))
procedure(RCE_tomlWriteString(outFile key value)
  SICO_tomlWriteString(outFile key value))
procedure(RCE_tomlWriteStringList(outFile key values)
  SICO_tomlWriteStringList(outFile key values))
procedure(RCE_tomlWriteBool(outFile key value)
  SICO_tomlWriteBool(outFile key value))

procedure(RCE_tomlDefaultOutput(runDir cell outType)
  let((normalized suffix)
    normalized=lowerCase(outType)
    suffix=case(normalized
      (("sp" "spice" "hspice") "sp")
      ("dspf" "dspf")
      ("spf" "spf")
      ("spef" "spef")
      ("spectre" "scs")
      (t normalized)
    )
    if(member(normalized
                '("view" "extview" "smartview" "calibreview" "starrcview")) then
      ""
    else
      strcat(runDir "/" cell "." suffix)
    )
  )
)

procedure(RCE_pythonEntry()
  let((toolsRoot)
    toolsRoot=SICO_toolsRoot()
    or(
      SICO_nativeEntry("rce")
      SICO_envValue("RCE_PYTHON_ENTRY")
      if(toolsRoot then
        simplifyFilename(strcat(toolsRoot "/rce/python/rce") t))
    )
  )
)

procedure(RCE_pythonExe()
  let((pythonRoot)
    pythonRoot=SICO_envValue("SICO_PYTHON_ROOT")
    or(SICO_envValue("RCE_PYTHON")
       SICO_envValue("SICO_PYTHON")
       when(pythonRoot
         simplifyFilename(strcat(pythonRoot "/bin/python3") t))
       "python3"))
)

procedure(RCE_tomlRevision()
  "20260803.multi.corner.v5"
)

rceTomlVersion="20260818.common.compat.v2"
)
