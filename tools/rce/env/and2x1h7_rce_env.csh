# RCE Python/TOML flow test environment for Virtuoso.
# Usage:
#   setenv SICO_HOME /path/to/cad
#   setenv PROJECT_ROOT $cwd
#   source $SICO_HOME/tools/rce/env/and2x1h7_rce_env.csh
#   virtuoso &

if ( ! $?SICO_HOME ) then
  echo "<ERROR> Set SICO_HOME before sourcing this RCE environment file" > /dev/stderr
  goto rce_env_done
endif
if ( "$SICO_HOME" == "" ) then
  echo "<ERROR> SICO_HOME must not be empty" > /dev/stderr
  goto rce_env_done
endif
if ( ! $?PROJECT_ROOT ) setenv PROJECT_ROOT "$cwd"
if ( ! $?PDK_ROOT ) setenv PDK_ROOT "$PROJECT_ROOT/PDK_V1.0"
if ( ! $?RCE_RUN_ROOT ) setenv RCE_RUN_ROOT "$PROJECT_ROOT/.rce"

if ( ! $?RCE_PYTHON ) then
  if ( $?SICO_PYTHON ) then
    setenv RCE_PYTHON "$SICO_PYTHON"
  else if ( $?SICO_PYTHON_ROOT ) then
    setenv RCE_PYTHON "$SICO_PYTHON_ROOT/bin/python3"
  else
    setenv RCE_PYTHON python3
  endif
endif
if ( ! $?RCE_PYTHON_ENTRY ) setenv RCE_PYTHON_ENTRY "$SICO_HOME/tools/rce/python/rce"
if ( ! $?PYTHONDONTWRITEBYTECODE ) setenv PYTHONDONTWRITEBYTECODE 1

set _rce_logo_valid = 0
set _rce_company_valid = 0
if ( $?LOGO ) then
  set _rce_logo_upper = `printf '%s' "$LOGO" | tr '[:lower:]' '[:upper:]'`
  if ( "$LOGO" != "" && "$_rce_logo_upper" != "OCAD" ) set _rce_logo_valid = 1
endif
if ( $?COMPANY ) then
  set _rce_company_upper = `printf '%s' "$COMPANY" | tr '[:lower:]' '[:upper:]'`
  if ( "$COMPANY" != "" && "$_rce_company_upper" != "OCAD" ) set _rce_company_valid = 1
endif
if ( $_rce_logo_valid == 0 ) then
  if ( $_rce_company_valid == 1 ) then
    setenv LOGO "$COMPANY"
  else
    setenv LOGO SiCo
  endif
endif
if ( $_rce_company_valid == 0 ) setenv COMPANY SiCo
unset _rce_logo_valid
unset _rce_company_valid
if ( $?_rce_logo_upper ) unset _rce_logo_upper
if ( $?_rce_company_upper ) unset _rce_company_upper

if ( ! $?PDK_HOME ) setenv PDK_HOME "$PDK_ROOT"
if ( ! $?CDS_LIB ) setenv CDS_LIB "$PROJECT_ROOT/cds.lib"
if ( ! $?TECH_LAYER_MAP ) setenv TECH_LAYER_MAP "$PROJECT_ROOT/tech_A/tech_A.layermap"
if ( ! $?CDL_HEADER_FILE ) setenv CDL_HEADER_FILE ""

if ( ! $?RCE_DB_DIR ) setenv RCE_DB_DIR "$RCE_RUN_ROOT/virtuoso"
if ( ! $?PROJ_DATA_ROOT_DIR ) setenv PROJ_DATA_ROOT_DIR "$RCE_RUN_ROOT/virtuoso"

set _rce_lvs_file_value = ""
if ( $?RCE_LVS_FILE ) set _rce_lvs_file_value = "$RCE_LVS_FILE"
if ( "$_rce_lvs_file_value" == "" ) then
  setenv RCE_LVS_FILE "tech_A,$PDK_ROOT/pv/tech_A.lvs.cal"
endif
unset _rce_lvs_file_value
if ( ! $?QUANTUS_TECH_DIR ) setenv QUANTUS_TECH_DIR "typ,$PDK_ROOT/rc/QRC/typ;rcmax,$PDK_ROOT/rc/QRC/rcmax;rcmin,$PDK_ROOT/rc/QRC/rcmin;cmax,$PDK_ROOT/rc/QRC/cmax;cmin,$PDK_ROOT/rc/QRC/cmin"
if ( ! $?STARRC_TECH_DIR ) setenv STARRC_TECH_DIR "typ,$PDK_ROOT/rc/QRC/typ"
if ( ! $?CALXRC_TECH_DIR ) setenv CALXRC_TECH_DIR "tech_A,$PDK_ROOT/pv"

if ( ! $?RCE_DEF_TOOL ) setenv RCE_DEF_TOOL QRC
if ( ! $?LVS_DEF_TOOL ) setenv LVS_DEF_TOOL Calibre
if ( ! $?RCE_DEF_TEMP ) setenv RCE_DEF_TEMP 25
if ( ! $?RCE_TEMP_LIST ) setenv RCE_TEMP_LIST "25,85,125"
if ( ! $?RCE_OUTPUT_CHOICES ) setenv RCE_OUTPUT_CHOICES "dspf,sp,extview"
if ( ! $?SICO_FLOW_CPU_ALLOW ) setenv SICO_FLOW_CPU_ALLOW "1,2,4,8"
if ( ! $?MULTI_CPU_ALLOWED ) setenv MULTI_CPU_ALLOWED "1,2,4,8"

# Test design defaults for manual GUI selection:
#   Input Type : OA
#   SCH_LIB    : worklib
#   SCH_CELL   : AND2X1H7
#   SCH_VIEW   : schematic
#   LAY_LIB    : worklib
#   LAY_CELL   : AND2X1H7
#   LAY_VIEW   : layout_drc
#   Run Type   : Local Host

mkdir -p "$RCE_DB_DIR"

rce_env_done:
