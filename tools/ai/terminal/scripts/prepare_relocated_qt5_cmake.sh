#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "usage: $0 QT_ROOT OVERLAY_CMAKE_ROOT" >&2
    exit 2
fi

qt_root=$(realpath -e -- "$1")
overlay_root=$(realpath -m -- "$2")
source_root="$qt_root/usr/lib64/cmake"

if [[ ! -f "$source_root/Qt5/Qt5Config.cmake" ]]; then
    echo "Qt5 CMake package is unavailable: $source_root/Qt5/Qt5Config.cmake" >&2
    exit 1
fi
if [[ -e "$overlay_root" ]]; then
    echo "refusing to overwrite Qt5 CMake overlay: $overlay_root" >&2
    exit 2
fi

mkdir -p -- "$overlay_root"
cp -a -- "$source_root/." "$overlay_root/"

escaped_qt_root=${qt_root//\\/\\\\}
escaped_qt_root=${escaped_qt_root//|/\\|}
escaped_qt_root=${escaped_qt_root//&/\\&}
while IFS= read -r -d '' cmake_file; do
    sed -i "s|/usr/|$escaped_qt_root/usr/|g" "$cmake_file"
done < <(find "$overlay_root" -type f -name '*.cmake' -print0)

printf '%s\n' "$overlay_root/Qt5"
