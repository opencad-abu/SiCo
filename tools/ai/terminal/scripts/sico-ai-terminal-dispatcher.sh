#!/bin/bash
set -euo pipefail

program_name=${0##*/}
case $program_name in
    sico-ai-terminal)
        program_label=terminal
        ;;
    *)
        printf 'cad-ai-dispatcher: unsupported program name: %s\n' "$program_name" >&2
        exit 1
        ;;
esac

fatal() {
    printf '%s: %s\n' "$program_name" "$*" >&2
    exit 1
}

path_directory() {
    local path=$1
    if [[ $path == */* ]]; then
        path=${path%/*}
        [[ -n $path ]] || path=/
    else
        path=.
    fi
    printf '%s\n' "$path"
}

script_directory() {
    local source=${BASH_SOURCE[0]}
    local directory
    unset CDPATH
    while [[ -h $source ]]; do
        directory=$(cd -P -- "$(path_directory "$source")" && pwd)
        source=$(/usr/bin/readlink -- "$source")
        if [[ $source != /* ]]; then
            source=$directory/$source
        fi
    done
    cd -P -- "$(path_directory "$source")" && pwd
}

strip_os_release_quotes() {
    local value=$1
    if [[ ${#value} -ge 2 ]]; then
        case $value in
            \"*\"|\'*\') value=${value:1:${#value}-2} ;;
        esac
    fi
    printf '%s\n' "$value"
}

os_release_value() {
    local file=$1
    local key=$2
    local line
    while IFS= read -r line || [[ -n $line ]]; do
        case $line in
            "$key"=*)
                strip_os_release_quotes "${line#*=}"
                return 0
                ;;
        esac
    done <"$file"
    return 1
}

is_rhel_family() {
    local identifier=${1,,}
    local identifier_like=${2,,}
    case " $identifier $identifier_like " in
        *" rhel "*|*" redhat "*|*" centos "*|*" rocky "*|*" almalinux "*|*" ol "*|*" oraclelinux "*|*" cloudlinux "*|*" anolis "*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

select_platform() {
    local requested=${SICO_AI_PLATFORM:-}
    local os_release_file=${SICO_AI_OS_RELEASE_FILE:-}
    local candidate
    local identifier
    local identifier_like
    local version_id
    local major

    case $requested in
        rhel7|rhel8)
            printf '%s\n' "$requested"
            return 0
            ;;
        '')
            ;;
        *)
            fatal 'SICO_AI_PLATFORM must be rhel7 or rhel8'
            ;;
    esac

    if [[ -n $os_release_file ]]; then
        [[ -f $os_release_file && -r $os_release_file ]] \
            || fatal "SICO_AI_OS_RELEASE_FILE is not a readable regular file: $os_release_file"
    else
        for candidate in /etc/os-release /usr/lib/os-release; do
            if [[ -f $candidate && -r $candidate ]]; then
                os_release_file=$candidate
                break
            fi
        done
        [[ -n $os_release_file ]] || fatal 'cannot find a readable os-release file'
    fi

    if ! identifier=$(os_release_value "$os_release_file" ID); then
        fatal "os-release is missing ID: $os_release_file"
    fi
    identifier_like=$(os_release_value "$os_release_file" ID_LIKE || true)
    if ! version_id=$(os_release_value "$os_release_file" VERSION_ID); then
        fatal "os-release is missing VERSION_ID: $os_release_file"
    fi
    is_rhel_family "$identifier" "$identifier_like" \
        || fatal "unsupported Linux family: ${identifier:-unknown}"

    if [[ ! $version_id =~ ^[0-9]+([.][0-9]+)*$ ]]; then
        fatal "invalid os-release VERSION_ID: ${version_id:-unknown}"
    fi
    major=${version_id%%.*}
    case $major in
        7) printf 'rhel7\n' ;;
        8|9) printf 'rhel8\n' ;;
        *) fatal "unsupported RHEL-family major version: ${version_id:-unknown}" ;;
    esac
}

select_python() {
    local configured=${SICO_PYTHON:-}
    local resolved
    if [[ -z $configured && -n ${SICO_PYTHON_ROOT:-} ]]; then
        configured=${SICO_PYTHON_ROOT%/}/bin/python3
    fi
    [[ -n $configured ]] \
        || fatal 'SICO_PYTHON or SICO_PYTHON_ROOT is required'
    if [[ $configured == */* ]]; then
        resolved=$configured
    else
        resolved=$(command -v -- "$configured") \
            || fatal "SICO_PYTHON is not available in PATH: $configured"
    fi
    [[ $resolved == /* ]] \
        || fatal "SICO_PYTHON must resolve to an absolute path: $configured"
    [[ -f $resolved && -x $resolved ]] \
        || fatal "SICO_PYTHON is not an executable file: $resolved"
    printf '%s\n' "$resolved"
}

dispatcher_directory=$(script_directory)
install_root=$(unset CDPATH; cd -P -- "$dispatcher_directory/.." && pwd)
product_root=$(unset CDPATH; cd -P -- "$install_root/../.." && pwd)
source "$product_root/tools/common/sh/sico-installation.sh"
PATH=/usr/bin:/bin sico_installation "$product_root" || exit $?
sico_launcher_environment || exit $?
if ! platform=$(select_platform); then
    exit 1
fi
if ! python=$(select_python); then
    exit 1
fi
export SICO_PYTHON=$python

runtime_root="$install_root/runtime/$platform"
binding="$runtime_root/python/QTermWidget.abi3.so"
library="$runtime_root/lib/libqtermwidget5.so.1"
launcher="$install_root/bin/sico-ai-terminal-pyqt"
data_root="$SICO_HOME/share/qtermwidget5"
[[ -f $binding && ! -L $binding ]] \
    || fatal "selected $platform QTermWidget binding is unavailable: $binding"
[[ -f $library && ! -L $library ]] \
    || fatal "selected $platform QTermWidget library is unavailable: $library"
[[ -x $launcher && ! -L $launcher ]] \
    || fatal "$program_label launcher is unavailable: $launcher"
[[ -s $data_root/kb-layouts/default.keytab \
        && -s $data_root/color-schemes/BreezeModified.colorscheme ]] \
    || fatal "QTermWidget data is unavailable: $data_root"
unset CAD_AI_QTERMWIDGET_DATA_PATH
export SICO_AI_QTERMWIDGET_RUNTIME=$runtime_root
export SICO_AI_QTERMWIDGET_DATA_PATH=$data_root
source "$product_root/tools/common/sh/sico-temporary.sh"
sico_exec_with_temporary "$python" "$python" "$launcher" "$@"
