;------------------------------------------------------------------------------
; RCE extraction technology helpers and SICO_envOptions compatibility aliases.
;------------------------------------------------------------------------------

unless(and(boundp('rceEnvOptionsVersion)
             rceEnvOptionsVersion=="20261011.calibre.pex.tool.v4")

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
    ("CalXACT" if(SICO_envValue("CALXACT_TECH_DIR")
                   then "CALXACT_TECH_DIR"
                   else "CALXRC_TECH_DIR"))
    (t         "QUANTUS_TECH_DIR")
  )
)

procedure(RCE_extTechNames(extTool)
  SICO_envPairNames(RCE_extTechEnvName(extTool))
)

procedure(RCE_extTechValue(extTool selected)
  SICO_envPairValue(RCE_extTechEnvName(extTool) selected)
)

;------------------------------------------------------------------------------
; Calibre PEX tool vocabulary.  EXT Engine lists one radio entry per tool, so
; the selected value is itself the tool name used by the form, TOML, profiles,
; run directories and view requests.  "XRC" stays accepted for hand-written
; TOML and older profiles.
;------------------------------------------------------------------------------

procedure(RCE_extToolChoices()  '("StarRC" "QRC" "CalXRC" "CalXACT"))

procedure(RCE_calibrePexCanonical(tool)
  case(tool
    ("XRC" "CalXRC")
    (t     tool)
  )
)

procedure(RCE_calibrePexToolP(tool)
  ; Keep the result boolean: callers assign it into t/nil form fields, and
  ; member() would hand them the matching tail of the list instead.
  if(member(RCE_calibrePexCanonical(tool) '("CalXRC" "CalXACT")) then
    t
  else
    nil
  )
)

; RCE_DEF_TOOL may spell the legacy alias; an unusable value falls back to the
; first menu entry so the form still builds.
procedure(RCE_extToolValue(name)
  let((tool)
    tool=RCE_calibrePexCanonical(or(name ""))
    if(member(tool RCE_extToolChoices()) then
      tool
    else
      car(RCE_extToolChoices())
    )
  )
)

; Restore a stored profile/TOML tool name.  Unknown names are rejected instead
; of silently switching the extraction engine.
procedure(RCE_setExtToolName(form name)
  let((tool)
    tool=RCE_calibrePexCanonical(or(name ""))
    if(member(tool RCE_extToolChoices()) then
      progn(
        form~>extTool~>value=tool
        t)
    else
      nil
    )
  )
)

rceEnvOptionsVersion="20261011.calibre.pex.tool.v4"
)
