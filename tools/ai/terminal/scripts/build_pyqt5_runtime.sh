#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/sico-build-environment.sh"
unset CDPATH

# PyQt5 is built from source because the site wheel bundles a different Qt
# runtime from the Qt SDK used by the terminal.  The resulting files are
# intended for a private ai/python tree, never for the shared Python site.
pyqt5_sha256=fda45743ebb4a27b4b1a51c6d8ef455c4c1b5d610c90d2934c7802b5c1557c52

usage() {
    echo >&2 'usage: build_pyqt5_runtime.sh PyQt5-5.15.11.tar.gz QT_ROOT INSTALL_DIR [BUILD_ROOT]'
    echo >&2
    echo >&2 'Build QtCore, QtGui, and QtWidgets for the production Python.'
    echo >&2 'Required environment:'
    echo >&2 '  SICO_PYTHON                  production Python 3 executable'
    echo >&2 '  SICO_PYTHON_ROOT             production Python prefix (uses bin/python3)'
    echo >&2 '  SICO_AI_PYQT_QMAKE           qmake from the matching Qt 5 installation'
    echo >&2 '  SICO_AI_PYQT_QT_ROOT         matching Qt prefix (defaults to QT_ROOT argument)'
    echo >&2
    echo >&2 'Optional: SICO_AI_PYTHON_LIBRARY_PATH or SICO_SYSTEM_LD_LIBRARY_PATH'
    echo >&2 'provides libpython and other site-runtime libraries during the build.'
    echo >&2 'The optional fourth argument and all default build files are kept below CWD/.cad.'
}

fatal() {
    echo "PyQt5 runtime build: $*" >&2
    exit 1
}

if [[ $# -lt 3 || $# -gt 4 ]]; then
    usage
    exit 2
fi

source_archive=$(realpath -e -- "$1")
configured_qt_root=$(realpath -e -- "$2")
install_root=$(realpath -m -- "$3")
python=${SICO_PYTHON-}
if [[ -z $python && -n ${SICO_PYTHON_ROOT-} ]]; then
    python="$SICO_PYTHON_ROOT/bin/python3"
fi
qmake=${SICO_AI_PYQT_QMAKE-${SICO_AI_PYQT5_QMAKE-}}
if [[ -z $qmake ]]; then
    if [[ -x "$configured_qt_root/usr/bin/qmake-qt5" ]]; then
        qmake="$configured_qt_root/usr/bin/qmake-qt5"
    elif [[ -x "$configured_qt_root/usr/lib64/qt5/bin/qmake" ]]; then
        qmake="$configured_qt_root/usr/lib64/qt5/bin/qmake"
    elif [[ -x "$configured_qt_root/usr/bin/qmake" ]]; then
        qmake="$configured_qt_root/usr/bin/qmake"
    fi
fi
qt_root=${SICO_AI_PYQT_QT_ROOT-$configured_qt_root}

[[ $(basename -- "$source_archive") == PyQt5-5.15.11.tar.gz ]] \
    || fatal "source archive must be named PyQt5-5.15.11.tar.gz"
[[ $(sha256sum "$source_archive" | awk '{print $1}') == "$pyqt5_sha256" ]] \
    || fatal "source archive SHA256 mismatch: $source_archive"
[[ -n $python ]] || fatal "SICO_PYTHON or SICO_PYTHON_ROOT is required"
[[ -n $qmake ]] || fatal "SICO_AI_PYQT_QMAKE or a qmake below QT_ROOT is required"
python=$(realpath -e -- "$python")
qmake=$(realpath -e -- "$qmake")
qt_root=$(realpath -e -- "$qt_root")
[[ -x $python ]] || fatal "target Python is not executable: $python"
[[ -x $qmake ]] || fatal "target qmake is not executable: $qmake"
[[ $install_root != / ]] || fatal "refusing unsafe install directory"
if [[ -e $install_root && ! -d $install_root ]]; then
    fatal "install directory is not a directory: $install_root"
fi

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
    env PYTHONNOUSERSITE=1 LD_LIBRARY_PATH="${python_library_path:-}" \
        "$python" "$@"
}

python_site_dirs=$(
    python_exec - <<'PY'
import sysconfig

paths = {sysconfig.get_path(name) for name in ("purelib", "platlib")}
print("\n".join(path for path in paths if path))
PY
)
while IFS= read -r python_site_dir; do
    [[ -n $python_site_dir ]] || continue
    python_site_dir=$(realpath -m -- "$python_site_dir")
    case "$install_root/" in
        "$python_site_dir/"*)
            fatal "install directory must be outside production site-packages: $install_root"
            ;;
    esac
done <<<"$python_site_dirs"

python_exec - <<'PY' || fatal "target Python lacks sipbuild and pyqtbuild"
import pyqtbuild
import sipbuild
PY

qmake_qt_version=$($qmake -query QT_VERSION)
[[ $qmake_qt_version =~ ^5[.][0-9]+[.][0-9]+$ ]] \
    || fatal "qmake does not select Qt 5: $qmake_qt_version"

find_qt_library_dir() {
    local root=$1
    local candidate
    for candidate in \
        "$root/usr/lib64" "$root/usr/lib" "$root/lib64" "$root/lib"; do
        if [[ -f $candidate/libQt5Core.so.5 || -f $candidate/libQt5Core.so ]]; then
            realpath -e -- "$candidate"
            return 0
        fi
    done
    return 1
}

qt_library_dir=$(find_qt_library_dir "$qt_root") \
    || fatal "cannot locate Qt5 libraries below: $qt_root"
qt_core_library="$qt_library_dir/libQt5Core.so.5"
[[ -f $qt_core_library ]] || qt_core_library="$qt_library_dir/libQt5Core.so"
qt_core_library=$(realpath -e -- "$qt_core_library")

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
    build_root=$(mktemp -d -p "$build_parent" cad-ai-pyqt5.XXXXXX)
fi

mkdir -p -- "$build_root/source"
tar -xzf "$source_archive" -C "$build_root/source" --strip-components=1

qt_data_dir="$qt_root/usr/lib64/qt5"
[[ -d $qt_data_dir ]] || qt_data_dir="$qt_root/usr/lib/qt5"
[[ -d $qt_data_dir ]] || qt_data_dir="$qt_root/lib64/qt5"
[[ -d $qt_data_dir ]] || qt_data_dir="$qt_root/share/qt5"
qt_host_bin_dir=
for candidate in \
    "$qt_root/usr/lib64/qt5/bin" "$qt_root/usr/lib/qt5/bin" \
    "$qt_root/lib64/qt5/bin" "$qt_root/lib/qt5/bin"; do
    if [[ -x $candidate/moc && -x $candidate/uic && -x $candidate/rcc ]]; then
        qt_host_bin_dir=$(realpath -e -- "$candidate")
        break
    fi
done
[[ -n $qt_host_bin_dir ]] || fatal "cannot locate Qt host tools below: $qt_root"
qt_header_dir="$qt_root/usr/include/qt5"
[[ -d $qt_header_dir ]] || qt_header_dir="$qt_root/include/qt5"
[[ -d $qt_header_dir ]] || qt_header_dir="$qt_root/include"
qt_plugin_dir="$qt_data_dir/plugins"
qt_translation_dir="$qt_data_dir/translations"
qt_conf="$build_root/qt.conf"
cat >"$qt_conf" <<EOF
[Paths]
Prefix=$qt_root
HostPrefix=$qt_root
HostBinaries=$qt_host_bin_dir
HostData=$qt_data_dir
HostLibraries=$qt_library_dir
Data=$qt_data_dir
Libraries=$qt_library_dir
Binaries=$qt_host_bin_dir
Headers=$qt_header_dir
Plugins=$qt_plugin_dir
Translations=$qt_translation_dir
ArchData=$qt_data_dir
EOF

# SIP invokes qmake itself.  A wrapper keeps every invocation on the selected
# relocated SDK, including module capability probes.
qmake_wrapper="$build_root/qmake-relocated"
qmake_literal=$(printf '%q' "$qmake")
qt_conf_literal=$(printf '%q' "$qt_conf")
cat >"$qmake_wrapper" <<EOF
#!/usr/bin/env bash
set -euo pipefail
for argument in "\$@"; do
    if [[ \$argument == -qtconf ]]; then
        exec $qmake_literal "\$@"
    fi
done
exec $qmake_literal "\$@" -qtconf $qt_conf_literal
EOF
chmod 0755 -- "$qmake_wrapper"
[[ $($qmake_wrapper -query QT_VERSION) == "$qmake_qt_version" ]] \
    || fatal "relocated qmake changed Qt version"
[[ $(realpath -e -- "$($qmake_wrapper -query QT_INSTALL_LIBS)") == "$qt_library_dir" ]] \
    || fatal "qt.conf did not select the target Qt library directory"

pyproject="$build_root/source/pyproject.toml"
cat >>"$pyproject" <<EOF

[tool.sip.project]
py-include-dir = "$(python_exec - <<'PY'
import sysconfig
print(sysconfig.get_path("include") or "")
PY
)"

[tool.sip.builder]
qmake = "$qmake_wrapper"
qmake-settings = ["QMAKE_LFLAGS += -Wl,--disable-new-dtags", "QMAKE_LFLAGS_RPATH = -Wl,-rpath,", "QMAKE_RPATHDIR += '\$ORIGIN/../../lib'"]
EOF

mkdir -p -- "$install_root"
(
    cd "$build_root/source"
    python_exec -m sipbuild.tools.install \
        --build-dir "$build_root/sip-build" \
        --target-dir "$install_root" \
        --no-distinfo \
        --no-tools \
        --no-designer-plugin \
        --no-qml-plugin \
        --no-dbus-python \
        --confirm-license \
        --enable QtCore \
        --enable QtGui \
        --enable QtWidgets
)

pyqt_package="$install_root/PyQt5"
[[ -f $pyqt_package/__init__.py ]] || fatal "PyQt5 package was not installed"
for module in QtCore QtGui QtWidgets; do
    module_file="$pyqt_package/$module.abi3.so"
    [[ -f $module_file ]] || fatal "missing PyQt5 module: ${module_file:-unknown}"
    dynamic=$(readelf -d "$module_file")
    grep -Fq 'Library rpath: [$ORIGIN/../../lib]' <<<"$dynamic" \
        || fatal "PyQt5 module lacks the private runtime RPATH: ${module_file:-unknown}"
    qt_module_name=${module#Qt}
    grep -Fq "Shared library: [libQt5${qt_module_name}.so.5]" <<<"$dynamic" \
        || fatal "PyQt5 module does not link to Qt${qt_module_name}: ${module_file:-unknown}"
done
shopt -s nullglob
source_pyqt_package=$(
    python_exec - <<'PY'
import sysconfig
from pathlib import Path

for name in ("platlib", "purelib"):
    root = sysconfig.get_path(name)
    if root:
        package = Path(root) / "PyQt5"
        if package.is_dir():
            print(package)
            break
else:
    raise SystemExit("PyQt5 package is not installed in the target Python")
PY
) || fatal "cannot locate PyQt5 in the target Python"
source_pyqt_package=$(realpath -e -- "$source_pyqt_package")
source_sip_modules=("$source_pyqt_package"/sip*.so)
[[ ${#source_sip_modules[@]} -eq 1 ]] \
    || fatal "target Python must provide exactly one PyQt5 SIP support module"
[[ -f $source_pyqt_package/sip.pyi ]] \
    || fatal "target Python PyQt5 SIP support stub is missing: $source_pyqt_package/sip.pyi"
cp -- "${source_sip_modules[0]}" "$pyqt_package/"
cp -- "$source_pyqt_package/sip.pyi" "$pyqt_package/"
sip_modules=("$pyqt_package"/sip*.so)
[[ ${#sip_modules[@]} -eq 1 ]] || fatal "PyQt5 SIP support module is missing or ambiguous"

runtime_smoke=$(
    env PYTHONNOUSERSITE=1 \
        PYTHONPATH="$install_root" \
        LD_LIBRARY_PATH="$qt_library_dir${python_library_path:+:$python_library_path}" \
        QT_PLUGIN_PATH="$qt_plugin_dir" \
        QT_QPA_PLATFORM_PLUGIN_PATH="$qt_plugin_dir/platforms" \
        QT_QPA_PLATFORM=offscreen QT_SELECT= QT_DIR= \
        "$python" - <<'PY'
import PyQt5.sip as sip
from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion
from PyQt5.QtWidgets import QApplication, QLabel

app = QApplication([])
label = QLabel("PyQt5 runtime smoke")
assert label.text()
sip_abi = sip.SIP_ABI_VERSION
sip_abi_str = f"{sip_abi >> 16}.{(sip_abi >> 8) & 0xff}.{sip_abi & 0xff}"
print(f"{PYQT_VERSION_STR} {QT_VERSION_STR} {qVersion()} {sip_abi_str}")
app.quit()
PY
) || fatal "private PyQt5 runtime smoke failed"
read -r pyqt_version runtime_qt_version runtime_qt_version_again sip_abi_version <<<"$runtime_smoke"
[[ $pyqt_version == 5.15.11 ]] || fatal "unexpected PyQt5 version: $pyqt_version"
[[ $runtime_qt_version == "$qmake_qt_version" \
    && $runtime_qt_version_again == "$qmake_qt_version" ]] \
    || fatal "private PyQt5 runtime selected an unexpected Qt: $runtime_smoke"
[[ $sip_abi_version =~ ^12[.]([0-9]+)[.]([0-9]+)$ ]] \
    || fatal "unexpected PyQt5 SIP ABI version: $sip_abi_version"
(( 10#${BASH_REMATCH[1]} >= 15 && 10#${BASH_REMATCH[1]} <= 17 )) \
    || fatal "unsupported PyQt5 SIP ABI version: $sip_abi_version"

echo "PyQt5 5.15.11 runtime installed below: $pyqt_package"
echo "Target Qt: $qmake_qt_version"
echo "PyQt5 SIP ABI: $sip_abi_version"
echo "Build files retained at: $build_root"
