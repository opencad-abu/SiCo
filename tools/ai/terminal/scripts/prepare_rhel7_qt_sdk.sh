#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 7 ]]; then
    echo "usage: $0 RHEL7_QT_ROOT QTBASE_GUI_RPM LIBX11_DEVEL_RPM XORG_X11_PROTO_DEVEL_RPM LIBGCRYPT_RPM LIBGPG_ERROR_RPM SDK_ROOT" >&2
    exit 2
fi

qt_root=$(realpath -e -- "$1")
gui_rpm=$(realpath -e -- "$2")
x11_devel_rpm=$(realpath -e -- "$3")
x11_proto_devel_rpm=$(realpath -e -- "$4")
gcrypt_rpm=$(realpath -e -- "$5")
gpg_error_rpm=$(realpath -e -- "$6")
sdk_root=$(realpath -m -- "$7")

if [[ ! -f "$qt_root/usr/lib64/cmake/Qt5/Qt5Config.cmake" ]]; then
    echo "RHEL 7 Qt CMake package is unavailable: $qt_root" >&2
    exit 1
fi
if [[ -e "$sdk_root" ]]; then
    echo "refusing to overwrite RHEL 7 Qt SDK overlay: $sdk_root" >&2
    exit 2
fi

rpm_identity=$(rpm -qp --qf '%{NAME} %{VERSION} %{ARCH}' "$gui_rpm")
if [[ $rpm_identity != 'qt5-qtbase-gui 5.9.7 x86_64' ]]; then
    echo "expected a RHEL 7 qt5-qtbase-gui 5.9.7 x86_64 RPM: $gui_rpm" >&2
    exit 1
fi
x11_devel_identity=$(rpm -qp --qf '%{NAME} %{VERSION} %{ARCH}' "$x11_devel_rpm")
if [[ $x11_devel_identity != 'libX11-devel 1.6.7 x86_64' ]]; then
    echo "expected a RHEL 7 libX11-devel 1.6.7 x86_64 RPM: $x11_devel_rpm" >&2
    exit 1
fi
x11_proto_identity=$(rpm -qp --qf '%{NAME} %{VERSION} %{ARCH}' "$x11_proto_devel_rpm")
if [[ $x11_proto_identity != 'xorg-x11-proto-devel 2018.4 noarch' ]]; then
    echo "expected a RHEL 7 xorg-x11-proto-devel 2018.4 noarch RPM: $x11_proto_devel_rpm" >&2
    exit 1
fi
gcrypt_identity=$(rpm -qp --qf '%{NAME} %{VERSION} %{ARCH}' "$gcrypt_rpm")
if [[ $gcrypt_identity != 'libgcrypt 1.5.3 x86_64' ]]; then
    echo "expected a RHEL 7 libgcrypt 1.5.3 x86_64 RPM: $gcrypt_rpm" >&2
    exit 1
fi
gpg_error_identity=$(rpm -qp --qf '%{NAME} %{VERSION} %{ARCH}' "$gpg_error_rpm")
if [[ $gpg_error_identity != 'libgpg-error 1.12 x86_64' ]]; then
    echo "expected a RHEL 7 libgpg-error 1.12 x86_64 RPM: $gpg_error_rpm" >&2
    exit 1
fi

mkdir -p -- "$sdk_root"
cp -a -- "$qt_root/." "$sdk_root/"
rpm2cpio "$gui_rpm" | cpio -idmu --quiet -D "$sdk_root"
rpm2cpio "$x11_devel_rpm" | cpio -idmu --quiet -D "$sdk_root"
rpm2cpio "$x11_proto_devel_rpm" | cpio -idmu --quiet -D "$sdk_root"
rpm2cpio "$gcrypt_rpm" | cpio -idmu --quiet -D "$sdk_root"
rpm2cpio "$gpg_error_rpm" | cpio -idmu --quiet -D "$sdk_root"

for required in \
        usr/include/X11/X.h \
        usr/include/X11/Xlib.h \
        usr/lib64/libgcrypt.so.11 \
        lib64/libgpg-error.so.0 \
        usr/lib64/libQt5Gui.so.5.9.7 \
        usr/lib64/libQt5Widgets.so.5.9.7 \
        usr/lib64/libQt5XcbQpa.so.5.9.7 \
        usr/lib64/qt5/plugins/platforms/libqxcb.so \
        usr/lib64/qt5/plugins/platforms/libqoffscreen.so \
        usr/lib64/qt5/plugins/platforminputcontexts/libcomposeplatforminputcontextplugin.so \
        usr/lib64/qt5/plugins/platforminputcontexts/libibusplatforminputcontextplugin.so; do
    if [[ ! -f "$sdk_root/$required" ]]; then
        echo "RHEL 7 SDK overlay did not provide required file: $required" >&2
        exit 1
    fi
done

printf '%s\n' "$sdk_root"
