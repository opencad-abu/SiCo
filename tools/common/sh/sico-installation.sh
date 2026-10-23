# Development launcher contract. Native releases compile their own launcher.
# Caller supplies the root derived from its own fixed location; never search CWD.

sico_installation() {
    sico_anchor=$(CDPATH='' cd -- "$1" && pwd -P) || return 2
    if [ "${SICO_HOME+x}" = x ]; then
        sico_configured=$SICO_HOME
    elif [ "${CAD_HOME+x}" = x ]; then
        echo "CAD_HOME was removed after SiCo v0.0.1; set SICO_HOME" >&2
        return 2
    elif [ "${SICO_ROOT+x}" = x ] || [ "${CAD_AGENT_ROOT+x}" = x ]; then
        echo "Migrate SICO_ROOT/CAD_AGENT_ROOT to an explicit SICO_HOME" >&2
        return 2
    else
        sico_configured=$sico_anchor
    fi
    case $sico_configured in
        /*) ;;
        *) echo "SICO_HOME must name an absolute SiCo installation" >&2; return 2 ;;
    esac
    sico_selected=$(CDPATH='' cd -- "$sico_configured" 2>/dev/null && pwd -P) || {
        echo "SICO_HOME is unavailable" >&2; return 2;
    }
    if [ "$sico_selected" != "$sico_anchor" ]; then
        echo "SICO_HOME conflicts with the launcher installation" >&2
        return 2
    fi
    sico_marker=$sico_selected/etc/config/sico-install.json
    sico_marker_real=$(readlink -f -- "$sico_marker") || return 2
    case $sico_marker_real in
        "$sico_selected"/*) ;;
        *) echo "SiCo identity escapes the installation" >&2; return 2 ;;
    esac
    if [ "$(wc -c < "$sico_marker")" -ne 75 ]; then
        echo "Invalid SiCo installation identity length" >&2
        return 2
    fi
    sico_identity=$(cat -- "$sico_marker") || return 2
    if [ "$sico_identity" != '{"format":"cad.runtime.install.v1","product":"Silicon Copilot","layout":1}' ]; then
        echo "Invalid SiCo installation identity" >&2
        return 2
    fi
    for sico_directory in bin tools tools/common; do
        [ -d "$sico_selected/$sico_directory" ] || {
            echo "Incomplete SiCo installation: $sico_directory" >&2; return 2;
        }
        sico_directory_real=$(readlink -f -- "$sico_selected/$sico_directory") || return 2
        case $sico_directory_real in
            "$sico_selected"/*) ;;
            *) echo "SiCo directory escapes the installation" >&2; return 2 ;;
        esac
    done
    SICO_HOME=$sico_selected
    unset CAD_HOME
    export SICO_HOME
}

sico_launcher_environment() {
    # Diagnose retired names; children receive current names only.
    for sico_suffix in PYTHON PYTHON_ROOT SYSTEM_LD_LIBRARY_PATH AI_PYTHON_LIBRARY_PATH CODEX_PYTHON_LIBRARY_PATH LSF_MONITOR_ORIG_LD_LIBRARY_PATH AI_PLATFORM AI_OS_RELEASE_FILE AI_PRIVATE_DBUS_DAEMON AI_PRIVATE_DBUS_LOG; do
        eval 'sico_is_set=${SICO_'"$sico_suffix"'+x}'
        eval 'sico_value=${SICO_'"$sico_suffix"'-}'
        eval 'sico_old_is_set=${CAD_'"$sico_suffix"'+x}'
        if [ "$sico_is_set" = x ]; then
            if [ "$sico_old_is_set" = x ]; then
                echo "CAD_$sico_suffix conflicts with SICO_$sico_suffix; SICO_$sico_suffix takes precedence" >&2
            fi
        elif [ "$sico_old_is_set" = x ]; then
            echo "CAD_$sico_suffix was removed after SiCo v0.0.1; set SICO_$sico_suffix" >&2
            return 2
        fi
        unset "CAD_$sico_suffix"
    done
    if [ "${SICO_PYTHON+x}" = x ] && [ -z "$SICO_PYTHON" ]; then
        echo "SICO_PYTHON must not be empty when explicitly configured" >&2
        return 2
    fi
    if [ "${SICO_PYTHON_ROOT+x}" = x ] && [ -z "$SICO_PYTHON_ROOT" ]; then
        echo "SICO_PYTHON_ROOT must not be empty when explicitly configured" >&2
        return 2
    fi
}

sico_runtime_python() {
    # The bundled runtime is authoritative. Source trees may provide a dev
    # marker, then the explicit compatibility selectors.
    sico_runtime_python_value=
    if [ -x "$SICO_HOME/runtime/python/bin/python3" ]; then
        sico_runtime_python_value=$SICO_HOME/runtime/python/bin/python3
        # The bundled runtime is authoritative. Replace stale site selectors
        # instead of letting source and native launchers choose differently.
        SICO_PYTHON=$sico_runtime_python_value
        SICO_PYTHON_ROOT=$SICO_HOME/runtime/python
        SICO_SYSTEM_LD_LIBRARY_PATH=$SICO_HOME/runtime/python/lib:$SICO_HOME/lib
        SICO_AI_PYTHON_LIBRARY_PATH=$SICO_SYSTEM_LD_LIBRARY_PATH
        export SICO_PYTHON SICO_PYTHON_ROOT
        export SICO_SYSTEM_LD_LIBRARY_PATH SICO_AI_PYTHON_LIBRARY_PATH
    elif [ -f "$SICO_HOME/etc/config/dev-runtime" ]; then
        sico_runtime_python_value=$(cat -- "$SICO_HOME/etc/config/dev-runtime") || return 2
        case $sico_runtime_python_value in
            /*) ;;
            *)
                echo "SiCo dev-runtime must contain an absolute Python path" >&2
                return 2
                ;;
        esac
    elif [ -n "${SICO_PYTHON-}" ]; then
        sico_runtime_python_value=$SICO_PYTHON
    elif [ -n "${SICO_PYTHON_ROOT-}" ]; then
        sico_runtime_python_value=${SICO_PYTHON_ROOT%/}/bin/python3
    else
        echo "SiCo Python is unavailable: provide \$SICO_HOME/runtime/python or \${SICO_HOME}/etc/config/dev-runtime" >&2
        return 2
    fi
    case $sico_runtime_python_value in
        */*)
            if [ ! -f "$sico_runtime_python_value" ] || [ ! -x "$sico_runtime_python_value" ]; then
                echo "SiCo Python is not executable: $sico_runtime_python_value" >&2
                return 2
            fi
            ;;
        *)
            command -v "$sico_runtime_python_value" >/dev/null 2>&1 || {
                echo "SiCo Python is not executable: $sico_runtime_python_value" >&2
                return 2
            }
            ;;
    esac
}

sico_runtime_library_path() {
    if [ -x "$SICO_HOME/runtime/python/bin/python3" ]; then
        sico_runtime_library_path_value=$SICO_HOME/runtime/python/lib:$SICO_HOME/lib
        return 0
    fi
    if [ -f "$SICO_HOME/etc/config/dev-runtime" ]; then
        sico_dev_python=$(cat -- "$SICO_HOME/etc/config/dev-runtime") || return 2
        sico_dev_root=$(CDPATH='' cd -- "$(dirname -- "$sico_dev_python")/.." && pwd -P) || return 2
        sico_runtime_library_path_value=$sico_dev_root/lib:$SICO_HOME/lib
        return 0
    fi
    case ${1:-ai} in
        ai)
            if [ "${SICO_AI_PYTHON_LIBRARY_PATH+x}" = x ]; then
                sico_runtime_library_path_value=$SICO_AI_PYTHON_LIBRARY_PATH
            elif [ "${SICO_SYSTEM_LD_LIBRARY_PATH+x}" = x ]; then
                sico_runtime_library_path_value=$SICO_SYSTEM_LD_LIBRARY_PATH
            else
                sico_runtime_library_path_value=
            fi
            ;;
        flow)
            if [ "${SICO_SYSTEM_LD_LIBRARY_PATH+x}" = x ]; then
                sico_runtime_library_path_value=$SICO_SYSTEM_LD_LIBRARY_PATH
            elif [ "${SICO_AI_PYTHON_LIBRARY_PATH+x}" = x ]; then
                sico_runtime_library_path_value=$SICO_AI_PYTHON_LIBRARY_PATH
            else
                sico_runtime_library_path_value=
            fi
            ;;
        *)
            echo "sico_runtime_library_path: unknown mode: $1" >&2
            return 2
            ;;
    esac
}
