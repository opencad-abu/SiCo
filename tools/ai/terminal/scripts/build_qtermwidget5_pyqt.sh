#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/sico-build-environment.sh"
unset CDPATH

qtermwidget_sha256=f0de18a8fb61ac7bde4b052c962bd3934a6f9c4fa3fd35aae3ddd833fd3b8b60
sip_abi_version=12.11
sip_abi_marker="CAD_AI_QTERMWIDGET_SIP_ABI=$sip_abi_version"

usage() {
    echo >&2 'usage: build_qtermwidget5_pyqt.sh SOURCE.tar.gz QTERMWIDGET_PREFIX INSTALL_DIR [BUILD_ROOT]'
    echo >&2
    echo >&2 'Build and install the QTermWidget 1.4.0 PyQt5 SIP extension. Required environment:'
    echo >&2 '  SICO_PYTHON                  production Python 3 executable'
    echo >&2 '  SICO_PYTHON_ROOT             production Python prefix (uses bin/python3)'
    echo >&2 '  SICO_AI_PYQT_PYTHON          legacy fallback when SICO_PYTHON* is unset'
    echo >&2 '  SICO_AI_PYQT_QMAKE           qmake from the matching Qt 5 installation'
    echo >&2 '  SICO_AI_PYQT_QT_ROOT         relocated Qt prefix (or SICO_AI_QT5_ROOT)'
    echo >&2 '  SICO_AI_PYQT5_BINDINGS_DIR   optional explicit PyQt5 SIP bindings directory'
    echo >&2
    echo >&2 'Optional: SICO_AI_PYQT_PYTHON_INCLUDE_DIR overrides Python sysconfig include.'
    echo >&2 'SICO_AI_PYQT_CC and SICO_AI_PYQT_CXX override qmake compilers for target ABI builds.'
    echo >&2 'SICO_AI_PYQT_COMPILER_LIBRARY_PATH is applied only to those compiler processes.'
    echo >&2 'SICO_AI_PYQT_CFLAGS/CXXFLAGS/LFLAGS append target sysroot and ABI flags.'
    echo >&2 'The target Python must provide PyQt5, sipbuild, and pyqtbuild. INSTALL_DIR is a'
    echo >&2 'Python module directory; the installed extension is INSTALL_DIR/QTermWidget*.so.'
    echo >&2 'The binding SIP ABI is fixed at 12.11 for the qualified production runtime.'
}

fatal() {
    echo "QTermWidget PyQt5 build: $*" >&2
    exit 1
}

if [[ $# -lt 3 || $# -gt 4 ]]; then
    usage
    exit 2
fi

source_archive=$(realpath -e -- "$1")
qtermwidget_prefix=$(realpath -e -- "$2")
install_dir=$(realpath -m -- "$3")
python=${SICO_PYTHON-}
if [[ -z $python && -n ${SICO_PYTHON_ROOT-} ]]; then
    python="$SICO_PYTHON_ROOT/bin/python3"
fi
if [[ -z $python ]]; then
    python=${SICO_AI_PYQT_PYTHON-${SICO_AI_PYQT5_PYTHON-}}
fi
qmake=${SICO_AI_PYQT_QMAKE-${SICO_AI_PYQT5_QMAKE-}}
configured_qt_root=${SICO_AI_PYQT_QT_ROOT-${SICO_AI_QT5_ROOT-}}
bindings_dir=${SICO_AI_PYQT5_BINDINGS_DIR-${SICO_AI_PYQT5_SIP_DIR-}}
python_include=${SICO_AI_PYQT_PYTHON_INCLUDE_DIR-}
compiler_cc=${SICO_AI_PYQT_CC-}
compiler_cxx=${SICO_AI_PYQT_CXX-}
compiler_library_path=${SICO_AI_PYQT_COMPILER_LIBRARY_PATH-}
compiler_cflags=${SICO_AI_PYQT_CFLAGS-}
compiler_cxxflags=${SICO_AI_PYQT_CXXFLAGS-}
linker_flags=${SICO_AI_PYQT_LFLAGS-}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
screen_lines_patch="$script_dir/../patches/qtermwidget-1.4.0-pyqt-screen-lines.patch"
if [[ ! -f $screen_lines_patch ]]; then
    screen_lines_patch="$script_dir/qtermwidget-1.4.0-pyqt-screen-lines.patch"
fi

[[ $(basename -- "$source_archive") == qtermwidget-1.4.0.tar.gz ]] \
    || fatal "source archive must be named qtermwidget-1.4.0.tar.gz"
actual_sha256=$(sha256sum "$source_archive" | awk '{print $1}')
[[ $actual_sha256 == "$qtermwidget_sha256" ]] \
    || fatal "source archive SHA256 mismatch: $source_archive"
[[ -n $python ]] || fatal \
    "SICO_PYTHON or SICO_PYTHON_ROOT is required (legacy SICO_AI_PYQT_PYTHON is also supported)"
if [[ -z $qmake && -n $configured_qt_root ]]; then
    if [[ -x "$configured_qt_root/usr/bin/qmake-qt5" ]]; then
        qmake="$configured_qt_root/usr/bin/qmake-qt5"
    elif [[ -x "$configured_qt_root/usr/lib64/qt5/bin/qmake" ]]; then
        qmake="$configured_qt_root/usr/lib64/qt5/bin/qmake"
    fi
fi
[[ -n $qmake ]] || fatal \
    "SICO_AI_PYQT_QMAKE is required (or provide qmake below SICO_AI_QT5_ROOT/SICO_AI_PYQT_QT_ROOT)"
python=$(realpath -e -- "$python")
qmake=$(realpath -e -- "$qmake")
[[ -x $python ]] || fatal "target Python is not executable: $python"
[[ -x $qmake ]] || fatal "target qmake is not executable: $qmake"
[[ -f $screen_lines_patch ]] || fatal "SIP compatibility patch is unavailable"
[[ $install_dir != / ]] || fatal "refusing unsafe install directory: $install_dir"
if [[ -e $install_dir && ! -d $install_dir ]]; then
    fatal "install path is not a directory: $install_dir"
fi

resolve_compiler() {
    local variable_name=$1
    local configured=$2
    local resolved
    [[ -n $configured ]] || return 0
    if [[ $configured == */* ]]; then
        resolved=$(realpath -e -- "$configured") \
            || fatal "$variable_name does not exist: $configured"
    else
        resolved=$(command -v -- "$configured") \
            || fatal "$variable_name is not available in PATH: $configured"
        resolved=$(realpath -e -- "$resolved")
    fi
    [[ -x $resolved ]] || fatal "$variable_name is not executable: $resolved"
    printf '%s\n' "$resolved"
}

compiler_cc=$(resolve_compiler SICO_AI_PYQT_CC "$compiler_cc")
compiler_cxx=$(resolve_compiler SICO_AI_PYQT_CXX "$compiler_cxx")
if [[ -n $compiler_library_path ]]; then
    IFS=: read -r -a compiler_library_directories <<<"$compiler_library_path"
    compiler_library_path=
    for compiler_library_directory in "${compiler_library_directories[@]}"; do
        [[ -n $compiler_library_directory ]] || fatal \
            "SICO_AI_PYQT_COMPILER_LIBRARY_PATH contains an empty entry"
        compiler_library_directory=$(realpath -e -- "$compiler_library_directory") \
            || fatal "compiler library directory does not exist: $compiler_library_directory"
        [[ -d $compiler_library_directory ]] || fatal \
            "compiler library path entry is not a directory: $compiler_library_directory"
        compiler_library_path+="${compiler_library_path:+:}$compiler_library_directory"
    done
    [[ -n $compiler_cc || -n $compiler_cxx ]] || fatal \
        "SICO_AI_PYQT_COMPILER_LIBRARY_PATH requires SICO_AI_PYQT_CC or SICO_AI_PYQT_CXX"
fi

qtermwidget_include="$qtermwidget_prefix/include/qtermwidget5"
qtermwidget_library="$qtermwidget_prefix/lib"
if [[ ! -f $qtermwidget_library/libqtermwidget5.so ]]; then
    qtermwidget_library="$qtermwidget_prefix/lib64"
fi
[[ -f $qtermwidget_include/qtermwidget.h ]] \
    || fatal "QTermWidget headers are unavailable below: $qtermwidget_prefix"
[[ -f $qtermwidget_library/libqtermwidget5.so ]] \
    || fatal "QTermWidget development library is unavailable below: $qtermwidget_prefix"
if LC_ALL=C grep -aFq \
        'the terminal process is still running, trying to stop it by SIGHUP' \
        "$qtermwidget_library/libqtermwidget5.so"; then
    fatal "QTermWidget prefix lacks the required session-shutdown patch"
fi

# The site Python is not necessarily linked with an RPATH to its own lib/
# directory.  In particular, the shared CAD 3.9.13 executable otherwise picks
# up the host libpython and reports a different patch level.  Start with the
# explicit site path and fall back to the selected interpreter's prefix.
python_library_path=${SICO_AI_PYTHON_LIBRARY_PATH-${SICO_SYSTEM_LD_LIBRARY_PATH-}}
if [[ -z $python_library_path ]]; then
    python_prefix=$(realpath -m -- "$(dirname -- "$python")/..")
    for candidate in "$python_prefix/lib" "$python_prefix/lib64"; do
        if compgen -G "$candidate/libpython*.so*" >/dev/null; then
            python_library_path=$candidate
            break
        fi
    done
fi
python_exec() {
    env -u PYTHONPATH -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH \
        -u LD_PRELOAD -u LD_AUDIT \
        PYTHONNOUSERSITE=1 \
        LD_LIBRARY_PATH="${python_library_path:-}" \
        "$python" -s "$@"
}

qmake_qt_version=$($qmake -query QT_VERSION)
[[ $qmake_qt_version =~ ^5[.][0-9]+[.][0-9]+$ ]] \
    || fatal "SICO_AI_PYQT_QMAKE must select Qt 5"

qt5_runtime_supports() {
    local build_version=$1
    local runtime_version=$2
    local build_minor
    local build_patch
    local runtime_minor
    local runtime_patch
    [[ $build_version =~ ^5[.]([0-9]+)[.]([0-9]+)$ ]] || return 1
    build_minor=${BASH_REMATCH[1]}
    build_patch=${BASH_REMATCH[2]}
    [[ $runtime_version =~ ^5[.]([0-9]+)[.]([0-9]+)$ ]] || return 1
    runtime_minor=${BASH_REMATCH[1]}
    runtime_patch=${BASH_REMATCH[2]}
    [[ $runtime_minor == "$build_minor" ]] \
        && ((10#$runtime_patch >= 10#$build_patch))
}

# Distribution qmake binaries are often built with /usr as their install
# prefix and then copied below a private SDK root.  Their -query paths are
# consequently unusable for the target build.  Prefer an explicit prefix and
# otherwise derive one from qmake's location, checking for the actual QtCore
# library before accepting a candidate.
find_qt_library_dir() {
    local root=$1
    local candidate
    for candidate in \
        "$root/lib64" "$root/lib" "$root/usr/lib64" "$root/usr/lib"; do
        if [[ -f $candidate/libQt5Core.so.5 || -f $candidate/libQt5Core.so ]]; then
            realpath -e -- "$candidate"
            return 0
        fi
    done
    return 1
}

qt_root_candidates=()
if [[ -n $configured_qt_root ]]; then
    configured_qt_root=$(realpath -e -- "$configured_qt_root")
    qt_root_candidates=("$configured_qt_root" "$configured_qt_root/usr")
else
    qmake_directory=$(dirname -- "$qmake")
    qt_root_candidates=(
        "$qmake_directory/.."
        "$qmake_directory/../.."
        "$qmake_directory/../../.."
        "$qmake_directory/../../../.."
    )
fi
qt_root=
qt_library_dir=
for qt_candidate in "${qt_root_candidates[@]}"; do
    qt_candidate=$(realpath -m -- "$qt_candidate")
    if qt_library_candidate=$(find_qt_library_dir "$qt_candidate"); then
        qt_library_dir=$qt_library_candidate
        case $qt_library_dir in
            */usr/lib64|*/usr/lib)
                qt_root=${qt_library_dir%/lib64}
                [[ $qt_root == */usr ]] || qt_root=${qt_library_dir%/lib}
                ;;
            */lib64)
                qt_root=${qt_library_dir%/lib64}
                ;;
            */lib)
                qt_root=${qt_library_dir%/lib}
                ;;
            *)
                qt_root=$qt_candidate
                ;;
        esac
        break
    fi
done
[[ -n $qt_root && -n $qt_library_dir ]] || fatal \
    "cannot locate Qt5 under qmake; set SICO_AI_PYQT_QT_ROOT or SICO_AI_QT5_ROOT"

qt_core_library="$qt_library_dir/libQt5Core.so.5"
if [[ ! -f $qt_core_library ]]; then
    qt_core_library="$qt_library_dir/libQt5Core.so"
fi
[[ -f $qt_core_library ]] \
    || fatal "Qt5 library directory is incomplete: $qt_library_dir"
qt_core_library=$(realpath -e -- "$qt_core_library")
qtermwidget_runtime=$(realpath -e -- "$qtermwidget_library/libqtermwidget5.so")
qtermwidget_runtime_qt=$(
    env LD_LIBRARY_PATH="$qtermwidget_library:$qt_library_dir" \
        ldd "$qtermwidget_runtime" \
        | sed -n 's|^[[:space:]]*libQt5Core[.]so[.]5 => \([^ ]*\).*|\1|p'
)
[[ -f $qtermwidget_runtime_qt ]] \
    || fatal "cannot resolve the Qt runtime used by QTermWidget: $qtermwidget_runtime"

if [[ -z $bindings_dir ]]; then
    bindings_dir=$(
        python_exec - <<'PY'
from pathlib import Path
import PyQt5
print(Path(PyQt5.__file__).resolve().parent / "bindings")
PY
    ) || fatal "target Python cannot locate its PyQt5 SIP bindings"
fi
if ! bindings_dir=$(realpath -e -- "$bindings_dir"); then
    fatal "PyQt5 SIP bindings path does not exist: $bindings_dir"
fi
[[ -d $bindings_dir && ! -L $bindings_dir ]] \
    || fatal "PyQt5 SIP bindings path must be a real directory: $bindings_dir"
[[ -f $bindings_dir/QtCore/QtCoremod.sip \
    && -f $bindings_dir/QtGui/QtGuimod.sip \
    && -f $bindings_dir/QtWidgets/QtWidgetsmod.sip ]] \
    || fatal "PyQt5 SIP bindings path lacks QtCore, QtGui, or QtWidgets"

python_exec - <<'PY' || fatal "target Python cannot generate SIP ABI 12.11 bindings"
import pyqtbuild
import sipbuild
from sipbuild.module import get_source_version_range

oldest, newest = get_source_version_range(12)
if not oldest <= 11 <= newest:
    raise SystemExit(f"sipbuild supports SIP ABI 12.{oldest} through 12.{newest}")
PY

if [[ -z $python_include ]]; then
    python_version=$(
        python_exec - <<'PY'
import sys

print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
    )
    python_prefix=$(realpath -m -- "$(dirname -- "$python")/..")
    if [[ -f $python_prefix/include/python$python_version/Python.h ]]; then
        python_include="$python_prefix/include/python$python_version"
    elif [[ -n ${SICO_PYTHON_ROOT-} \
            && -f $SICO_PYTHON_ROOT/include/python$python_version/Python.h ]]; then
        python_include="$SICO_PYTHON_ROOT/include/python$python_version"
    else
        python_include=$(
            python_exec - <<'PY'
import sysconfig

print(sysconfig.get_path("include") or "")
PY
        )
    fi
fi
python_include=$(realpath -e -- "$python_include")
[[ -f $python_include/Python.h ]] \
    || fatal "target Python development headers are unavailable: $python_include"

readarray -t pyqt_details < <(
    python_exec - <<'PY'
from pathlib import Path

from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion
import PyQt5

print(QT_VERSION_STR)
print(qVersion())
print(PYQT_VERSION_STR)
print(Path(PyQt5.__file__).resolve().parent)
PY
) || fatal "target Python cannot import PyQt5.QtCore"
[[ ${#pyqt_details[@]} -eq 4 \
    && ${pyqt_details[0]} =~ ^5[.][0-9]+[.][0-9]+$ \
    && ${pyqt_details[1]} =~ ^5[.][0-9]+[.][0-9]+$ ]] \
    || fatal "target Python does not provide PyQt5"
qt5_runtime_supports "$qmake_qt_version" "${pyqt_details[1]}" || fatal \
    "production PyQt5 Qt runtime ${pyqt_details[1]} is not forward-compatible with qmake Qt $qmake_qt_version"
qt5_runtime_supports "${pyqt_details[0]}" "${pyqt_details[1]}" || fatal \
    "production PyQt5 Qt runtime ${pyqt_details[1]} is not forward-compatible with its ${pyqt_details[0]} build"
# The build SDK remains explicit for qmake and QTermWidget checks. PyQt5 is
# imported with the production Python environment so its own RPATH/runtime is
# the contract being validated, not an SDK library injected by this helper.
pyqt_package=$(realpath -e -- "${pyqt_details[3]}")
pyqt_owned_bindings=$(realpath -e -- "$pyqt_package/bindings") \
    || fatal "production PyQt5 does not provide SIP metadata: $pyqt_package/bindings"
[[ $bindings_dir == "$pyqt_owned_bindings" ]] || fatal \
    "PyQt5 SIP bindings do not belong to target Python: $bindings_dir"
pyqt_sip_module=$(
    python_exec - <<'PY'
from pathlib import Path
import PyQt5.sip

print(Path(PyQt5.sip.__file__).resolve())
PY
) || fatal "target Python cannot import its PyQt5.sip support module"
case $pyqt_sip_module in
    "$pyqt_package"/sip*.so) ;;
    *) fatal "PyQt5.sip support does not belong to target Python: $pyqt_sip_module" ;;
esac
for module in QtCore QtGui QtWidgets; do
    bindings_configuration="$bindings_dir/$module/$module.toml"
    [[ -f $bindings_configuration ]] \
        || fatal "PyQt5 SIP configuration is unavailable: $bindings_configuration"
    pyqt_qt_tag=${pyqt_details[0]//./_}
    if ! grep -Fq "module-tags = [\"Qt_${pyqt_qt_tag}\"" \
            "$bindings_configuration"; then
        fatal "PyQt5 SIP metadata does not match Qt ${pyqt_details[0]}: $bindings_configuration"
    fi
done
[[ $(realpath -e -- "$qtermwidget_runtime_qt") == "$qt_core_library" ]] \
    || fatal "QTermWidget does not resolve against the selected qmake Qt SDK"

build_parent=$(realpath -m -- "$(pwd -P)/.cad")
mkdir -p -- "$build_parent"
if [[ $# -eq 4 ]]; then
    build_root=$(realpath -m -- "$4")
    case "$build_root/" in
        "$build_parent/"*) ;;
        *) fatal "build root must be below CWD/.cad: $build_root" ;;
    esac
    [[ ! -e $build_root ]] || fatal "build root already exists: $build_root"
    mkdir -p -- "$build_root"
else
    build_root=$(mktemp -d -p "$build_parent" cad-ai-qtermwidget5-pyqt.XXXXXX)
fi

mkdir -p -- "$build_root/source"
tar -xzf "$source_archive" -C "$build_root/source" --strip-components=1
patch -d "$build_root/source" -p1 < "$screen_lines_patch"

bash "$script_dir/configure_qtermwidget_pyqt.sh" \
    "$build_root" "$sip_abi_marker" "$qt_root" "$qt_library_dir" \
    "$qmake" "$qmake_qt_version" "$qtermwidget_include" "$qtermwidget_library" \
    "$bindings_dir" "$python_include" "$compiler_cflags" "$compiler_cxxflags" \
    "$linker_flags" "$compiler_library_path" "$compiler_cc" "$compiler_cxx"

mkdir -p -- "$install_dir"
(
    cd "$build_root/source/pyqt"
    python_exec -m sipbuild.tools.install \
        --abi-version "$sip_abi_version" \
        --build-dir "$build_root/sip-build" \
        --target-dir "$install_dir" \
        --no-distinfo
)

generated_module="$build_root/sip-build/QTermWidget/sipQTermWidgetcmodule.cpp"
[[ -f $generated_module ]] \
    || fatal "SIP did not generate the QTermWidget module source"
grep -Eq \
    'sipExportModule\(&sipModuleAPI_QTermWidget, 12, 11, 0\)' \
    "$generated_module" \
    || fatal "generated QTermWidget module does not target SIP ABI $sip_abi_version"

shopt -s nullglob
extensions=("$install_dir"/QTermWidget*.so)
[[ ${#extensions[@]} -eq 1 && -f ${extensions[0]} ]] \
    || fatal "SIP install did not produce one QTermWidget extension in $install_dir"
if ! readelf -d "${extensions[0]}" | grep -Fq 'Shared library: [libqtermwidget5.so.1]'; then
    fatal "installed extension does not link to libqtermwidget5.so.1"
fi
LC_ALL=C grep -aFq "$sip_abi_marker" "${extensions[0]}" \
    || fatal "installed extension lacks the SIP ABI $sip_abi_version marker"
extension_dynamic=$(readelf -d "${extensions[0]}")
if ! grep -Fq 'Library rpath: [$ORIGIN/../lib]' <<<"$extension_dynamic" \
        || grep -Fq '(RUNPATH)' <<<"$extension_dynamic"; then
    fatal "installed extension must use private old-style RPATH: ${extensions[0]}"
fi

extension_dir=$(dirname -- "${extensions[0]}")
runtime_root=$(realpath -m -- "$extension_dir/..")
[[ -f $runtime_root/lib/libqtermwidget5.so.1 ]] || fatal \
    "real production import smoke requires $runtime_root/lib/libqtermwidget5.so.1"
for release_elf in "${extensions[0]}" "$runtime_root/lib/libqtermwidget5.so.1"; do
    if LC_ALL=C grep -aEq \
            '/(software|workarea|zone[.]disk|home)/|/[.]build-[^/]*(/|$)' \
            "$release_elf"; then
        fatal "release ELF contains a build-machine absolute path: $release_elf"
    fi
done
resource_root=$runtime_root/share/qtermwidget5
if [[ ! -f $resource_root/kb-layouts/default.keytab \
        || ! -f $resource_root/color-schemes/BreezeModified.colorscheme ]]; then
    resource_root=$qtermwidget_prefix/share/qtermwidget5
fi
[[ -f $resource_root/kb-layouts/default.keytab \
    && -f $resource_root/color-schemes/BreezeModified.colorscheme ]] \
    || fatal "QTermWidget smoke resources are unavailable below: $resource_root"
env -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH -u LD_PRELOAD -u LD_AUDIT \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH="$extension_dir" \
    LD_LIBRARY_PATH="${python_library_path:-}" \
    SICO_AI_QTERMWIDGET_DATA_PATH="$resource_root" \
    SICO_AI_SMOKE_EXTENSION_DIR="$extension_dir" \
    SICO_AI_SMOKE_RUNTIME_ROOT="$runtime_root" \
    QT_QPA_PLATFORM=offscreen "$python" -s - <<'PY' \
    || fatal "installed extension production PyQt5 smoke failed"
import os
from pathlib import Path

from PyQt5.QtCore import QT_VERSION_STR, qVersion
from PyQt5.QtWidgets import QApplication
import PyQt5
import QTermWidget as binding
from QTermWidget import QTermWidget

app = QApplication(["cad-ai-qtermwidget-build-smoke"])
terminal = QTermWidget(0)
terminal.setHistorySize(17)
assert terminal.screenLinesCount() > 0
assert terminal.keyBindings() == "default"
assert QT_VERSION_STR.startswith("5.")
assert qVersion().startswith("5.")
binding_path = Path(binding.__file__).resolve()
pyqt_path = Path(PyQt5.__file__).resolve()
extension_dir = Path(os.environ["SICO_AI_SMOKE_EXTENSION_DIR"]).resolve()
runtime_root = Path(os.environ["SICO_AI_SMOKE_RUNTIME_ROOT"]).resolve()
resource_root = Path(os.environ["SICO_AI_QTERMWIDGET_DATA_PATH"]).resolve()
assert binding_path.parent == extension_dir
assert runtime_root not in pyqt_path.parents
assert (resource_root / "kb-layouts/default.keytab").is_file()
assert (resource_root / "color-schemes/BreezeModified.colorscheme").is_file()
terminal.close()
terminal.deleteLater()
app.processEvents()
PY

echo "QTermWidget 1.4.0 PyQt5 extension installed: ${extensions[0]}"
echo "Target Python PyQt5: ${pyqt_details[2]} (built with Qt ${pyqt_details[0]}, runtime ${pyqt_details[1]})"
echo "Build files retained at: $build_root"
