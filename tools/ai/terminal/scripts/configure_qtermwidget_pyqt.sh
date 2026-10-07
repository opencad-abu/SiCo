#!/usr/bin/env bash
# Configure one private extracted SIP project. No compile, install, or environment discovery.
set -euo pipefail
unset CDPATH

fatal() {
    echo "QTermWidget PyQt5 build: $*" >&2
    exit 1
}

[[ $# -eq 16 ]] || fatal "private SIP configuration requires 16 resolved arguments"
build_root=${1}
sip_abi_marker=${2}
qt_root=${3}
qt_library_dir=${4}
qmake=${5}
qmake_qt_version=${6}
qtermwidget_include=${7}
qtermwidget_library=${8}
bindings_dir=${9}
python_include=${10}
compiler_cflags=${11}
compiler_cxxflags=${12}
linker_flags=${13}
compiler_library_path=${14}
compiler_cc=${15}
compiler_cxx=${16}

# Keep the production SIP ABI contract visible in the final ELF so release
# verification does not depend on disassembling sipExportModule().
binding_sip="$build_root/source/pyqt/sip/qtermwidget.sip"
cat >>"$binding_sip" <<EOF

%ModuleCode
extern "C" __attribute__((used, visibility("default")))
const char cadAiQTermWidgetSipAbi[] = "$sip_abi_marker";
%End
EOF

# Feed the relocated paths to qmake through a private qt.conf.  A wrapper is
# used because SIP invokes qmake itself and does not expose a way to append
# -qtconf to every invocation.
qt_data_dir="$qt_root/lib64/qt5"
if [[ ! -d $qt_data_dir ]]; then
    qt_data_dir="$qt_root/lib/qt5"
fi
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
qt_header_dir="$qt_root/include/qt5"
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
qmake_relocated_version=$($qmake_wrapper -query QT_VERSION)
[[ $qmake_relocated_version == "$qmake_qt_version" ]] || fatal \
    "relocated qmake changed Qt version: $qmake_relocated_version"
qmake_relocated_library_dir=$($qmake_wrapper -query QT_INSTALL_LIBS)
[[ $(realpath -e -- "$qmake_relocated_library_dir") == "$qt_library_dir" ]] || fatal \
    "qt.conf did not select the relocated Qt libraries: $qmake_relocated_library_dir"

pyproject="$build_root/source/pyqt/pyproject.toml"
python_project="$build_root/source/pyqt/project.py"
sed -i \
    "s|self.libraries.append('qtermwidget5')|self.include_dirs.append(r'$qtermwidget_include')\n        self.library_dirs.append(r'$qtermwidget_library')\n        self.libraries.append('qtermwidget5')|" \
    "$python_project"
grep -Fq "self.include_dirs.append(r'$qtermwidget_include')" "$python_project" \
    || fatal "cannot configure the upstream PyQt project"
cat >>"$pyproject" <<EOF

[tool.sip.project]
sip-include-dirs = ["$bindings_dir"]
py-include-dir = "$python_include"

[tool.sip.builder]
qmake = "$qmake_wrapper"
EOF
qmake_settings=(
    'QMAKE_LFLAGS += -Wl,--disable-new-dtags'
    'QMAKE_LFLAGS_RPATH = -Wl,-rpath,'
    "QMAKE_RPATHDIR += '\$ORIGIN/../lib'"
)
[[ -z $compiler_cflags ]] || qmake_settings+=("QMAKE_CFLAGS += $compiler_cflags")
[[ -z $compiler_cxxflags ]] || qmake_settings+=("QMAKE_CXXFLAGS += $compiler_cxxflags")
[[ -z $linker_flags ]] || qmake_settings+=("QMAKE_LFLAGS += $linker_flags")
compiler_wrapper_dir=
if [[ -n $compiler_library_path ]]; then
    compiler_wrapper_dir="$build_root/compiler-wrappers"
    mkdir -p -- "$compiler_wrapper_dir"
fi
if [[ -n $compiler_cc ]]; then
    qmake_cc=$compiler_cc
    if [[ -n $compiler_wrapper_dir ]]; then
        qmake_cc="$compiler_wrapper_dir/cc"
        compiler_cc_literal=$(printf '%q' "$compiler_cc")
        compiler_library_path_literal=$(printf '%q' "$compiler_library_path")
        cat >"$qmake_cc" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export LD_LIBRARY_PATH=$compiler_library_path_literal\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
exec $compiler_cc_literal "\$@"
EOF
        chmod 0755 -- "$qmake_cc"
    fi
    qmake_settings+=(
        "QMAKE_CC = $qmake_cc"
        "QMAKE_LINK_C = $qmake_cc"
        "QMAKE_LINK_C_SHLIB = $qmake_cc"
    )
fi
if [[ -n $compiler_cxx ]]; then
    qmake_cxx=$compiler_cxx
    if [[ -n $compiler_wrapper_dir ]]; then
        qmake_cxx="$compiler_wrapper_dir/cxx"
        compiler_cxx_literal=$(printf '%q' "$compiler_cxx")
        compiler_library_path_literal=$(printf '%q' "$compiler_library_path")
        cat >"$qmake_cxx" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export LD_LIBRARY_PATH=$compiler_library_path_literal\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}
exec $compiler_cxx_literal "\$@"
EOF
        chmod 0755 -- "$qmake_cxx"
    fi
    qmake_settings+=(
        "QMAKE_CXX = $qmake_cxx"
        "QMAKE_LINK = $qmake_cxx"
        "QMAKE_LINK_SHLIB = $qmake_cxx"
    )
fi
{
    printf 'qmake-settings = ['
    separator=
    for setting in "${qmake_settings[@]}"; do
        escaped_setting=${setting//\\/\\\\}
        escaped_setting=${escaped_setting//\"/\\\"}
        printf '%s"%s"' "$separator" "$escaped_setting"
        separator=', '
    done
    printf ']\n'
} >>"$pyproject"
