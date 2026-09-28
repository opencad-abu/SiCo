unless(and(boundp('sicoEnvironmentVersion)
           sicoEnvironmentVersion=="20260924.flow.environment.v3")

; Retired environment names are rejected; diagnostics never contain values.

procedure(SICO_environmentValue(currentName legacyNames)
  let((selected)
    selected=getShellEnvVar(currentName)
    unless(selected
      foreach(legacyName legacyNames
        when(getShellEnvVar(legacyName)
          error("%s was removed after SiCo v0.0.1; set %s\n" legacyName currentName))))
    selected))

; Flow names have one active spelling. The retired name is diagnostic only.
procedure(SICO_flowEnvironmentValue(name)
  let((retired selected)
    retired=case(name
      ("DRC_DB_DIR" "PROJ_DRC_DB_DIR")
      ("LVS_DB_DIR" "PROJ_LVS_DB_DIR")
      ("RCE_DB_DIR" "PROJ_RCE_DB_DIR")
      ("GDS_DB_DIR" "PROJ_GDS_DB_DIR")
      ("CDL_DB_DIR" "PROJ_CDL_DB_DIR")
      ("LEF_DB_DIR" "PROJ_LEF_DB_DIR")
      ("DRC_CONFIG" "PROJ_DRC_CONFIG")
      ("LVS_CONFIG" "PROJ_LVS_CONFIG")
      ("RCE_CONFIG" "PROJ_RCE_CONFIG")
      ("LEF_CONFIG" "PROJ_LEF_CONFIG")
      ("DRC_FILE" "PROJ_DRC_FILE")
      ("LVS_FILE" "PROJ_LVS_FILE")
      ("RCE_LVS_FILE" "PROJ_RCE_LVS_FILE")
      ("QUANTUS_TECH_DIR" "PROJ_QUANTUS_TECH_DIR")
      ("STARRC_TECH_DIR" "PROJ_STARRC_TECH_DIR")
      ("CALXRC_TECH_DIR" "PROJ_CALXRC_TECH_DIR")
      ("HCELL_FILE" "PROJ_HCELL_FILE")
      ("LEFGEN_OPT_FILE" "PROJ_LEFGEN_OPT_FILE")
      ("LEF_CELL_LIST" "PROJ_LEF_CELL_LIST")
      ("RCE_DEF_TOOL" "EXT_DEF_TOOL")
      ("RCE_CORNER" "EXT_CORNER")
      ("RCE_DEF_TEMP" "EXT_DEF_TEMP")
      ("RCE_TEMP_LIST" "EXT_TEMP_LIST")
      ("RCE_OUTPUT_CHOICES" "EXT_OUTPUT_CHOICES")
      ("SICO_FLOW_CPU_ALLOW" "EXT_CPU_ALLOW")
      ("SICO_LSF_BSUB" "EXT_LSF_BSUB")
      ("SICO_LSF_BKILL" "EXT_LSF_BKILL")
      ("SICO_LSF_BQUEUES" "EXT_LSF_BQUEUES"))
    selected=getShellEnvVar(name)
    when(and(retired !selected getShellEnvVar(retired))
      error("%s was renamed; set %s (see docs/SICO_FLOW_ENV_MIGRATION.md)\n"
            retired name))
    selected))

procedure(SICO_envValue(name)
  let((value legacy)
    legacy=case(name
      ("SICO_PYTHON" "CAD_PYTHON")
      ("SICO_PYTHON_ROOT" "CAD_PYTHON_ROOT")
      ("SICO_SYSTEM_LD_LIBRARY_PATH" "CAD_SYSTEM_LD_LIBRARY_PATH")
      ("SICO_LSF_PYTHON" "CAD_LSF_PYTHON")
      ("SICO_LSF_PYTHON_ENTRY" "CAD_LSF_PYTHON_ENTRY")
      ("SICO_BATCH_PYTHON_ENTRY" "CAD_BATCH_PYTHON_ENTRY")
      ("SICO_PROFILE_PYTHON_ENTRY" "CAD_PROFILE_PYTHON_ENTRY"))
    value=if(legacy then SICO_environmentValue(name list(legacy)) else SICO_flowEnvironmentValue(name))
    when(and(legacy getShellEnvVar(name) strlen(value)==0
             name!="SICO_SYSTEM_LD_LIBRARY_PATH")
      error("%s must not be empty\n" name))
    if(and(value strlen(value)>0) then value else nil)))

sicoEnvironmentVersion="20260924.flow.environment.v3"
)
