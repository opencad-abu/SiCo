#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/sico-build-environment.sh"

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
qtermwidget_commit=58981da1625810aa0a3dcc605bacba73f580ccdd
lxqt_commit=1304079edbe62c8c9e528de8ee0cf1a1119724dc

usage() {
    echo "usage: $0 INSTALL_PREFIX [BUILD_ROOT]" >&2
    echo "Build QTermWidget 1.4.0 from local git archives with system Qt 5.9+." >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
    usage
    exit 2
fi

install_prefix=$(realpath -m -- "$1")
if [[ -z "$install_prefix" || "$install_prefix" == / ]]; then
    echo "refusing unsafe install prefix: $install_prefix" >&2
    exit 2
fi

qtermwidget_repo=${SICO_AI_QTERMWIDGET_REPO-}
lxqt_repo=${SICO_AI_LXQT_BUILD_TOOLS_REPO-}
if [[ -z "$qtermwidget_repo" ]]; then
    echo "SICO_AI_QTERMWIDGET_REPO must point to the qtermwidget 1.4.0 source clone" >&2
    exit 2
fi
if [[ -z "$lxqt_repo" ]]; then
    echo "SICO_AI_LXQT_BUILD_TOOLS_REPO must point to the lxqt-build-tools 0.13.0 source clone" >&2
    exit 2
fi
qt5_dir=${SICO_AI_QT5_DIR-/usr/lib64/cmake/Qt5}
qt5_cmake_root=$(dirname -- "$qt5_dir")
qt5_core_dir="$qt5_cmake_root/Qt5Core"
qt5_gui_dir="$qt5_cmake_root/Qt5Gui"
qt5_widgets_dir="$qt5_cmake_root/Qt5Widgets"
qt5_linguist_tools_dir="$qt5_cmake_root/Qt5LinguistTools"
jobs=${SICO_AI_BUILD_JOBS-8}
patch_file="$script_dir/../patches/qtermwidget-1.4.0-relocatable-data.patch"
compat_patch_file="$script_dir/../patches/qtermwidget-1.4.0-qt59-compat.patch"
translations_patch_file="$script_dir/../patches/qtermwidget-1.4.0-optional-translations.patch"
shutdown_patch_file="$script_dir/../patches/qtermwidget-1.4.0-session-shutdown.patch"
lxqt_compat_patch_file="$script_dir/../patches/lxqt-build-tools-0.13.0-qt59-compat.patch"
relocate_qt5_cmake_script="$script_dir/prepare_relocated_qt5_cmake.sh"
prepare_rhel7_qt_sdk_script="$script_dir/prepare_rhel7_qt_sdk.sh"
toolchain_args=()
if [[ -n "${SICO_AI_CMAKE_TOOLCHAIN_FILE-}" ]]; then
    toolchain_file=$(realpath -e -- "$SICO_AI_CMAKE_TOOLCHAIN_FILE")
    if [[ ! -f "$toolchain_file" ]]; then
        echo "CMake toolchain is not a file: $toolchain_file" >&2
        exit 1
    fi
    toolchain_args=(-DCMAKE_TOOLCHAIN_FILE:FILEPATH="$toolchain_file")
fi
cmake_registry_args=(
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF
    -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY:BOOL=OFF
)

for repository in "$qtermwidget_repo" "$lxqt_repo"; do
    if ! git -C "$repository" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "not a local git repository: $repository" >&2
        exit 1
    fi
done
if [[ $(git -C "$qtermwidget_repo" rev-parse 'refs/tags/1.4.0^{commit}') != "$qtermwidget_commit" ]]; then
    echo "QTermWidget tag 1.4.0 does not match the pinned commit" >&2
    exit 1
fi
if [[ $(git -C "$lxqt_repo" rev-parse 'refs/tags/0.13.0^{commit}') != "$lxqt_commit" ]]; then
    echo "lxqt-build-tools tag 0.13.0 does not match the pinned commit" >&2
    exit 1
fi
for qt5_config in \
    "$qt5_dir/Qt5Config.cmake" \
    "$qt5_core_dir/Qt5CoreConfig.cmake" \
    "$qt5_gui_dir/Qt5GuiConfig.cmake" \
    "$qt5_widgets_dir/Qt5WidgetsConfig.cmake"; do
    if [[ ! -f "$qt5_config" ]]; then
        echo "system Qt5 package component is unavailable: $qt5_config" >&2
        exit 1
    fi
done
qtermwidget_translation_args=()
if [[ ! -f "$qt5_linguist_tools_dir/Qt5LinguistToolsConfig.cmake" ]]; then
    qtermwidget_translation_args=(-DQTERMWIDGET_BUILD_TRANSLATIONS=OFF)
fi
for patch_file_to_check in "$patch_file" "$compat_patch_file" "$translations_patch_file" \
        "$shutdown_patch_file" "$lxqt_compat_patch_file"; do
    if [[ ! -f "$patch_file_to_check" ]]; then
        echo "required compatibility patch is unavailable: $patch_file_to_check" >&2
        exit 1
    fi
done
for helper in "$relocate_qt5_cmake_script" "$prepare_rhel7_qt_sdk_script"; do
    if [[ ! -x "$helper" ]]; then
        echo "required Qt5 build helper is unavailable: $helper" >&2
        exit 1
    fi
done

build_parent=$(realpath -m -- "$(pwd -P)/.cad")
mkdir -p -- "$build_parent"
if [[ $# -eq 2 ]]; then
    build_root=$(realpath -m -- "$2")
    case "$build_root/" in
        "$build_parent/"*) ;;
        *)
            echo "build root must be below CWD/.cad: $build_root" >&2
            exit 2
            ;;
    esac
    if [[ -e "$build_root" ]]; then
        echo "build root already exists: $build_root" >&2
        exit 2
    fi
    mkdir -p -- "$build_root"
else
    build_root=$(mktemp -d -p "$build_parent" cad-ai-qtermwidget5.XXXXXX)
fi

qt5_root=${SICO_AI_QT5_ROOT-$(realpath -e -- "$qt5_dir/../../../..")}
if [[ ! -f "$qt5_root/usr/lib64/libQt5Gui.so.5" ]]; then
    gui_rpm=${SICO_AI_RHEL7_QTBASE_GUI_RPM-}
    x11_devel_rpm=${SICO_AI_RHEL7_LIBX11_DEVEL_RPM-}
    x11_proto_devel_rpm=${SICO_AI_RHEL7_XORG_X11_PROTO_DEVEL_RPM-}
    gcrypt_rpm=${SICO_AI_RHEL7_LIBGCRYPT_RPM-}
    gpg_error_rpm=${SICO_AI_RHEL7_LIBGPG_ERROR_RPM-}
    if [[ -z "$gui_rpm" || -z "$x11_devel_rpm" || -z "$x11_proto_devel_rpm" \
            || -z "$gcrypt_rpm" || -z "$gpg_error_rpm" ]]; then
        echo "RHEL 7 Qt SDK is incomplete; set SICO_AI_RHEL7_QTBASE_GUI_RPM, SICO_AI_RHEL7_LIBX11_DEVEL_RPM, SICO_AI_RHEL7_XORG_X11_PROTO_DEVEL_RPM, SICO_AI_RHEL7_LIBGCRYPT_RPM, and SICO_AI_RHEL7_LIBGPG_ERROR_RPM" >&2
        exit 1
    fi
    qt5_root=$("$prepare_rhel7_qt_sdk_script" "$qt5_root" "$gui_rpm" \
        "$x11_devel_rpm" "$x11_proto_devel_rpm" "$gcrypt_rpm" "$gpg_error_rpm" \
        "$build_root/qt5-sdk")
    qt5_dir="$qt5_root/usr/lib64/cmake/Qt5"
    qt5_cmake_root=$(dirname -- "$qt5_dir")
    qt5_core_dir="$qt5_cmake_root/Qt5Core"
    qt5_gui_dir="$qt5_cmake_root/Qt5Gui"
    qt5_widgets_dir="$qt5_cmake_root/Qt5Widgets"
    qt5_linguist_tools_dir="$qt5_cmake_root/Qt5LinguistTools"
fi

# The RHEL 7 Qt SDK was extracted from RPMs and its CMake package still names
# /usr paths. Build a private overlay instead of modifying the shared SDK.
if grep -Fq 'set(imported_location "/usr/lib64/' "$qt5_core_dir/Qt5CoreConfig.cmake"; then
    qt5_dir=$("$relocate_qt5_cmake_script" "$qt5_root" "$build_root/qt5-cmake-overlay")
    qt5_cmake_root=$(dirname -- "$qt5_dir")
    qt5_core_dir="$qt5_cmake_root/Qt5Core"
    qt5_gui_dir="$qt5_cmake_root/Qt5Gui"
    qt5_widgets_dir="$qt5_cmake_root/Qt5Widgets"
    qt5_linguist_tools_dir="$qt5_cmake_root/Qt5LinguistTools"
fi

mkdir -p -- "$build_root/lxqt-src" "$build_root/qtermwidget-src"
git -C "$lxqt_repo" archive "$lxqt_commit" | tar -x -C "$build_root/lxqt-src"
git -C "$qtermwidget_repo" archive "$qtermwidget_commit" | tar -x -C "$build_root/qtermwidget-src"
patch -d "$build_root/qtermwidget-src" -p1 < "$patch_file"
patch -d "$build_root/qtermwidget-src" -p1 < "$compat_patch_file"
patch -d "$build_root/qtermwidget-src" -p1 < "$translations_patch_file"
patch -d "$build_root/qtermwidget-src" -p1 < "$shutdown_patch_file"
patch -d "$build_root/lxqt-src" -p1 < "$lxqt_compat_patch_file"

# A malformed unified-diff hunk can exit successfully after applying only its
# leading changes. Reject such a source tree before machine paths reach ELF.
qtermwidget_tools="$build_root/qtermwidget-src/lib/tools.cpp"
qtermwidget_cmake="$build_root/qtermwidget-src/CMakeLists.txt"
if ! grep -Fq 'qgetenv("SICO_AI_QTERMWIDGET_DATA_PATH")' "$qtermwidget_tools" \
        || ! grep -Fq 'get_qtermwidget_data_dir(QLatin1String("kb-layouts"))' \
            "$qtermwidget_tools" \
        || ! grep -Fq 'get_qtermwidget_data_dir(QLatin1String("color-schemes"))' \
            "$qtermwidget_tools" \
        || grep -Eq 'QLatin1String\((KB_LAYOUT_DIR|COLORSCHEMES_DIR)\)' \
            "$qtermwidget_tools" \
        || grep -Eq '"(KB_LAYOUT_DIR|COLORSCHEMES_DIR|TRANSLATIONS_DIR)=' \
            "$qtermwidget_cmake"; then
    echo "QTermWidget relocatable-data patch was only partially applied" >&2
    exit 1
fi

cmake -S "$build_root/lxqt-src" -B "$build_root/lxqt-build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_STANDARD=11 \
    -DCMAKE_CXX_STANDARD_REQUIRED=ON \
    -DCMAKE_INSTALL_PREFIX="$build_root/lxqt-install" \
    "${toolchain_args[@]}" \
    "${cmake_registry_args[@]}" \
    -DQt5Core_DIR:PATH="$qt5_core_dir"
cmake --build "$build_root/lxqt-build" --parallel "$jobs"
cmake --install "$build_root/lxqt-build"

cmake -S "$build_root/qtermwidget-src" -B "$build_root/qtermwidget-build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_STANDARD=11 \
    -DCMAKE_CXX_STANDARD_REQUIRED=ON \
    -DCMAKE_INSTALL_PREFIX="$install_prefix" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_PREFIX_PATH="$build_root/lxqt-install" \
    "${toolchain_args[@]}" \
    "${cmake_registry_args[@]}" \
    "${qtermwidget_translation_args[@]}" \
    -DQt5Core_DIR:PATH="$qt5_core_dir" \
    -DQt5Gui_DIR:PATH="$qt5_gui_dir" \
    -DQt5Widgets_DIR:PATH="$qt5_widgets_dir" \
    -DQt5LinguistTools_DIR:PATH="$qt5_linguist_tools_dir" \
    -DBUILD_EXAMPLE=OFF \
    -DUPDATE_TRANSLATIONS=OFF \
    -DQTERMWIDGET_USE_UTEMPTER=OFF \
    -DUSE_UTF8PROC=OFF
cmake --build "$build_root/qtermwidget-build" --parallel "$jobs"
cmake --install "$build_root/qtermwidget-build"

license_dir="$install_prefix/share/licenses/qtermwidget5"
install -d -m 0755 -- "$license_dir"
install -m 0644 -- \
    "$build_root/qtermwidget-src/LICENSE" \
    "$build_root/qtermwidget-src/LICENSE.BSD-3-clause" \
    "$build_root/qtermwidget-src/LICENSE.LGPL2+" \
    "$license_dir/"

library="$install_prefix/lib/libqtermwidget5.so.1.4.0"
if [[ ! -f "$library" ]]; then
    echo "QTermWidget install did not produce $library" >&2
    exit 1
fi
if LC_ALL=C grep -aFq \
        'the terminal process is still running, trying to stop it by SIGHUP' "$library"; then
    echo "QTermWidget still contains the obsolete destructor shutdown path" >&2
    exit 1
fi
system_library_path=${SICO_AI_SYSTEM_LD_LIBRARY_PATH-${SICO_CODEX_SYSTEM_LD_LIBRARY_PATH-}}
if env LD_LIBRARY_PATH="$system_library_path" ldd "$library" | grep -q 'not found'; then
    env LD_LIBRARY_PATH="$system_library_path" ldd "$library" >&2
    exit 1
fi

echo "QTermWidget 1.4.0 installed at $install_prefix"
echo "Build files retained at $build_root"
echo "Qt5 runtime root: $qt5_root"
echo "Qt5 CMake package: $qt5_dir"
echo "Configure the terminal with -DSICO_AI_QTERMWIDGET_ROOT=$install_prefix"
