# RCE Python/TOML flow test environment for Virtuoso.
# Usage:
#   export SICO_HOME="/path/to/cad"
#   export PROJECT_ROOT="${PWD}"
#   source "${SICO_HOME}/tools/rce/env/and2x1h7_rce_env.bash"
#   virtuoso &

if [[ -z ${SICO_HOME:-} ]]; then
    _rce_env_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
    SICO_HOME="$(cd -- "${_rce_env_dir}/../../.." && pwd -P)"
    unset _rce_env_dir
fi
export SICO_HOME
export PROJECT_ROOT="${PROJECT_ROOT:-${PWD}}"
export PDK_ROOT="${PDK_ROOT:-${PROJECT_ROOT}/ETIP_N55_PDK_V1.0}"
RCE_RUN_ROOT="${RCE_RUN_ROOT:-${PROJECT_ROOT}/.rce}"
export RCE_RUN_ROOT

if [[ -z ${RCE_PYTHON:-} ]]; then
    if [[ -n ${SICO_PYTHON:-} ]]; then
        RCE_PYTHON=${SICO_PYTHON}
    elif [[ -n ${SICO_PYTHON_ROOT:-} ]]; then
        RCE_PYTHON=${SICO_PYTHON_ROOT%/}/bin/python3
    else
        RCE_PYTHON=python3
    fi
fi
export RCE_PYTHON
export RCE_PYTHON_ENTRY="${RCE_PYTHON_ENTRY:-${SICO_HOME}/tools/rce/python/rce}"
export PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"

if [[ -z ${LOGO:-} || ${LOGO^^} == OCAD ]]; then
    if [[ -n ${COMPANY:-} && ${COMPANY^^} != OCAD ]]; then
        export LOGO="${COMPANY}"
    else
        export LOGO=SiCo
    fi
else
    export LOGO
fi
if [[ -z ${COMPANY:-} || ${COMPANY^^} == OCAD ]]; then
    export COMPANY=SiCo
else
    export COMPANY
fi

export ETIP_N55_PDK_HOME="${ETIP_N55_PDK_HOME:-${PDK_ROOT}}"
export CDS_LIB="${CDS_LIB:-${PROJECT_ROOT}/cds.lib}"
export TECH_LAYER_MAP="${TECH_LAYER_MAP:-${PROJECT_ROOT}/ETIPN55/ETIPN55.layermap}"
export CDL_HEADER_FILE="${CDL_HEADER_FILE:-}"

export RCE_DB_DIR="${RCE_DB_DIR:-${RCE_RUN_ROOT}/virtuoso}"
export PROJ_DATA_ROOT_DIR="${PROJ_DATA_ROOT_DIR:-${RCE_RUN_ROOT}/virtuoso}"

if [[ -z ${RCE_LVS_FILE:-} ]]; then
    export RCE_LVS_FILE="ETIPN55,${PDK_ROOT}/pv/ETIPN55.lvs.cal"
fi
export QUANTUS_TECH_DIR="${QUANTUS_TECH_DIR:-Default,${PDK_ROOT}/rc/QRC}"
export STARRC_TECH_DIR="${STARRC_TECH_DIR:-Default,${PDK_ROOT}/rc/StarRC}"
export CALXRC_TECH_DIR="${CALXRC_TECH_DIR:-Default,${PDK_ROOT}/XRC}"

export RCE_DEF_TOOL="${RCE_DEF_TOOL:-QRC}"
export LVS_DEF_TOOL="${LVS_DEF_TOOL:-Calibre}"
export RCE_DEF_TEMP="${RCE_DEF_TEMP:-25}"
export RCE_TEMP_LIST="${RCE_TEMP_LIST:-25,85,125}"
export RCE_OUTPUT_CHOICES="${RCE_OUTPUT_CHOICES:-dspf,sp,extview}"
export SICO_FLOW_CPU_ALLOW="${SICO_FLOW_CPU_ALLOW:-1,2,4,8}"
export MULTI_CPU_ALLOWED="${MULTI_CPU_ALLOWED:-1,2,4,8}"

# Test design defaults for manual GUI selection:
#   Input Type : OA
#   SCH_LIB    : ESCN55H7
#   SCH_CELL   : AND2X1H7
#   SCH_VIEW   : schematic
#   LAY_LIB    : ESCN55H7
#   LAY_CELL   : AND2X1H7
#   LAY_VIEW   : layout_drc
#   Run Type   : Local Host

mkdir -p "${RCE_DB_DIR}"
