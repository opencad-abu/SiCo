;------------------------------------------------------------------------------
; RCE extraction technology helpers and SICO_envOptions compatibility aliases.
;------------------------------------------------------------------------------

unless(and(boundp('rceEnvOptionsVersion)
             rceEnvOptionsVersion=="20260812.common.compat.v1")

unless(and(boundp('cadContextBuilding) cadContextBuilding)
  let((commonSkill)
    commonSkill=strcat("/" buildString(reverse(cdr(reverse(
      parseString(simplifyFilename(get_filename(piport) t) "/")))) "/")
      "/../../common/skill/")
    load(strcat(commonSkill "SICO_envOptions.il"))))

procedure(RCE_parseEnvPairs(envName) SICO_parseEnvPairs(envName))
procedure(RCE_envPairNames(envName) SICO_envPairNames(envName))
procedure(RCE_envPairValue(envName selected)
  SICO_envPairValue(envName selected))

procedure(RCE_extTechEnvName(extTool)
  case(extTool
    ("QRC"     "QUANTUS_TECH_DIR")
    ("Quantus" "QUANTUS_TECH_DIR")
    ("StarRC"  "STARRC_TECH_DIR")
    ("CalXRC"  "CALXRC_TECH_DIR")
    ("XRC"     "CALXRC_TECH_DIR")
    (t         "QUANTUS_TECH_DIR")
  )
)

procedure(RCE_extTechNames(extTool)
  SICO_envPairNames(RCE_extTechEnvName(extTool))
)

procedure(RCE_extTechValue(extTool selected)
  SICO_envPairValue(RCE_extTechEnvName(extTool) selected)
)

rceEnvOptionsVersion="20260812.common.compat.v1"
)
