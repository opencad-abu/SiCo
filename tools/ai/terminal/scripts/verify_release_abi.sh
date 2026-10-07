#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/sico-build-environment.sh"

usage() {
    echo "usage: $0 rhel7|rhel8 RUNTIME_PREFIX [COMMON_PREFIX [RESOURCE_PREFIX]]" >&2
}

fatal() {
    echo "terminal runtime verification: $*" >&2
    exit 1
}

sip_abi_version=12.11
sip_abi_marker="CAD_AI_QTERMWIDGET_SIP_ABI=$sip_abi_version"

if [[ $# -lt 2 || $# -gt 4 ]]; then
    usage
    exit 2
fi

platform=$1
case $platform in
    rhel7)
        release_label='RHEL 7'
        glibc_limit=2.17
        glibcxx_limit=3.4.19
        cxxabi_limit=1.3.7
        ;;
    rhel8)
        release_label='RHEL 8'
        glibc_limit=2.28
        glibcxx_limit=3.4.25
        cxxabi_limit=1.3.11
        ;;
    *)
        usage
        exit 2
        ;;
esac

runtime_prefix=$(realpath -e -- "$2")
common_prefix=${3:-$2}
common_prefix=$(realpath -e -- "$common_prefix")
resource_prefix=${4:-$common_prefix}
resource_prefix=$(realpath -e -- "$resource_prefix")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
"$script_dir/verify_ripgrep_runtime.sh" "$common_prefix"
if [[ $runtime_prefix != "$common_prefix" ]]; then
    case "$runtime_prefix/" in
        "$common_prefix/"*) ;;
        *) fatal "runtime prefix must be below the common release prefix: $runtime_prefix" ;;
    esac
fi
launcher="$common_prefix/bin/sico-ai-terminal"
pinyin_wrapper="$common_prefix/bin/sico-ai-pinyin"
pyqt_launcher="$common_prefix/bin/sico-ai-terminal-pyqt"
assistant_launcher="$common_prefix/bin/aiassistant"
codex_runtime="$common_prefix/runtime/codex/x86_64-unknown-linux-musl/bin/codex"
codex_code_host="$common_prefix/runtime/codex/x86_64-unknown-linux-musl/bin/codex-code-mode-host"
rg_runtime="$common_prefix/runtime/ripgrep/x86_64-unknown-linux-musl/bin/rg"
codex_manifest="$common_prefix/runtime/codex/x86_64-unknown-linux-musl/MANIFEST.txt"
codex_enabled=0
if [[ -e $codex_runtime || -e $codex_manifest ]]; then
    codex_enabled=1
fi
binding="$runtime_prefix/python/QTermWidget.abi3.so"
library="$runtime_prefix/lib/libqtermwidget5.so.1"

[[ -f $assistant_launcher && -x $assistant_launcher && ! -L $assistant_launcher ]] \
    || fatal "release AI Assistant launcher is unavailable: $assistant_launcher"
if (( codex_enabled )); then
    [[ -f $codex_runtime && -x $codex_runtime && ! -L $codex_runtime ]] \
        || fatal "bundled Codex runtime is unavailable: $codex_runtime"
    [[ -f $codex_code_host && -x $codex_code_host && ! -L $codex_code_host ]] \
        || fatal "bundled Codex code-mode host is unavailable: $codex_code_host"
    [[ -f $codex_manifest && ! -L $codex_manifest ]] \
        || fatal "bundled Codex manifest is unavailable: $codex_manifest"
fi
[[ -f $launcher && -x $launcher && ! -L $launcher ]] \
    || fatal "release dispatcher is unavailable: $launcher"
[[ -f $pinyin_wrapper && -x $pinyin_wrapper && ! -L $pinyin_wrapper ]] \
    || fatal "release pinyin wrapper is unavailable: $pinyin_wrapper"
[[ -f $pyqt_launcher && -x $pyqt_launcher && ! -L $pyqt_launcher ]] \
    || fatal "release PyQt5 launcher is unavailable: $pyqt_launcher"
[[ -f $binding && ! -L $binding ]] \
    || fatal "QTermWidget binding is unavailable: $binding"
[[ -f $library && ! -L $library ]] \
    || fatal "QTermWidget library is unavailable: $library"
for resource in \
        share/qtermwidget5/kb-layouts/default.keytab \
        share/qtermwidget5/color-schemes/BreezeModified.colorscheme \
        share/licenses/qtermwidget5/LICENSE \
        share/licenses/qtermwidget5/LICENSE.BSD-3-clause \
        share/licenses/qtermwidget5/LICENSE.LGPL2+; do
    [[ -s $resource_prefix/$resource ]] \
        || fatal "QTermWidget resource is unavailable: $resource_prefix/$resource"
done

is_allowed_runtime_binary() {
    local candidate=$1

    [[ $candidate == "$rg_runtime" ]] && return 0
    if [[ $candidate == "$binding" || $candidate == "$library" ]]; then
        return 0
    fi
    if (( codex_enabled )); then
        [[ $candidate == "$codex_runtime" || $candidate == "$codex_code_host" ]] \
            && return 0
    fi
    if [[ $runtime_prefix != "$common_prefix" ]]; then
        case $candidate in
            "$common_prefix"/runtime/rhel7/python/QTermWidget.abi3.so|\
            "$common_prefix"/runtime/rhel7/lib/libqtermwidget5.so.1|\
            "$common_prefix"/runtime/rhel8/python/QTermWidget.abi3.so|\
            "$common_prefix"/runtime/rhel8/lib/libqtermwidget5.so.1)
                return 0
                ;;
        esac
    fi
    return 1
}

verify_release_layout() {
    local entry relative forbidden package

    entry=$(find "$common_prefix" -type l -print -quit)
    [[ -z $entry ]] || fatal "release contains a symbolic link: $entry"

    while IFS= read -r -d '' entry; do
        relative=${entry#"$common_prefix/bin/"}
        case $relative in
            aiassistant|sico-ai-pinyin|sico-ai-terminal|sico-ai-terminal-pyqt) ;;
            *) fatal "release contains an unexpected AI executable: $entry" ;;
        esac
    done < <(find "$common_prefix/bin" -mindepth 1 -maxdepth 1 -print0)

    while IFS= read -r -d '' entry; do
        relative=${entry#"$common_prefix/python/"}
        case $relative in
            sico-ai|cadai|cad_ai_terminal) ;;
            QTermWidget.abi3.so)
                [[ $runtime_prefix == "$common_prefix" ]] \
                    || fatal "release contains a common QTermWidget binding: $entry"
                ;;
            *) fatal "release contains an unexpected Python runtime payload: $entry" ;;
        esac
    done < <(find "$common_prefix/python" -mindepth 1 -maxdepth 1 -print0)

    for package in cadai cad_ai_terminal; do
        [[ -d $common_prefix/python/$package ]] \
            || fatal "release Python source package is unavailable: $package"
        while IFS= read -r -d '' entry; do
            if [[ -d $entry || ( -f $entry && $entry == *.py ) ]]; then
                continue
            fi
            fatal "release Python package contains a non-source payload: $entry"
        done < <(find "$common_prefix/python/$package" -mindepth 1 -print0)
    done

    if [[ $runtime_prefix == "$common_prefix" ]]; then
        if [[ -d $common_prefix/runtime ]]; then
            while IFS= read -r -d '' entry; do
                relative=${entry#"$common_prefix/runtime/"}
                case $relative in
                    ripgrep|ripgrep/x86_64-unknown-linux-musl|\
                    ripgrep/x86_64-unknown-linux-musl/bin|\
                    ripgrep/x86_64-unknown-linux-musl/bin/rg|\
                    ripgrep/x86_64-unknown-linux-musl/MANIFEST.txt) ;;
                    codex|codex/x86_64-unknown-linux-musl|\
                    codex/x86_64-unknown-linux-musl/bin|\
                    codex/x86_64-unknown-linux-musl/bin/codex|\
                    codex/x86_64-unknown-linux-musl/bin/codex-code-mode-host|\
                    codex/x86_64-unknown-linux-musl/MANIFEST.txt)
                        (( codex_enabled )) || fatal "release contains an unexpected common runtime payload: $entry"
                        ;;
                    *) fatal "release contains an unexpected common runtime payload: $entry" ;;
                esac
            done < <(find "$common_prefix/runtime" -mindepth 1 -print0)
        else
            [[ ! -e $common_prefix/runtime ]] \
                || fatal "release contains a runtime directory without Codex"
        fi
        while IFS= read -r -d '' entry; do
            [[ $entry == "$library" ]] \
                || fatal "release contains an unexpected private library: $entry"
        done < <(find "$common_prefix/lib" -mindepth 1 -print0)
    else
        [[ $runtime_prefix == "$common_prefix/runtime/$platform" ]] \
            || fatal "runtime prefix must be common/runtime/$platform"
        [[ ! -e $common_prefix/lib ]] \
            || fatal "release contains a common private library directory"
        while IFS= read -r -d '' entry; do
            relative=${entry#"$common_prefix/runtime/"}
            case $relative in
                rhel7|rhel8|rhel7/python|rhel7/lib|rhel8/python|rhel8/lib) ;;
                ripgrep|ripgrep/x86_64-unknown-linux-musl|\
                ripgrep/x86_64-unknown-linux-musl/bin|\
                ripgrep/x86_64-unknown-linux-musl/bin/rg|\
                ripgrep/x86_64-unknown-linux-musl/MANIFEST.txt) ;;
                codex|codex/x86_64-unknown-linux-musl|\
                codex/x86_64-unknown-linux-musl/bin|\
                codex/x86_64-unknown-linux-musl/bin/codex|\
                codex/x86_64-unknown-linux-musl/bin/codex-code-mode-host|\
                codex/x86_64-unknown-linux-musl/MANIFEST.txt)
                    (( codex_enabled )) || fatal "release contains an unexpected common runtime payload: $entry"
                    ;;
                rhel7/python/QTermWidget.abi3.so|\
                rhel7/lib/libqtermwidget5.so.1|\
                rhel8/python/QTermWidget.abi3.so|\
                rhel8/lib/libqtermwidget5.so.1) ;;
                *) fatal "release contains an unexpected platform runtime payload: $entry" ;;
            esac
        done < <(find "$common_prefix/runtime" -mindepth 1 -print0)
    fi

    forbidden=$(find "$common_prefix" \
        \( -iname 'PyQt5*' \
            -o -name site-packages -o -name dist-packages \
            -o -name lib-dynload -o -name '__pycache__' \
            -o -name '*.pyc' -o -name '*.pyo' \
            -o -name plugins -o -name mkspecs \
            -o -type d -name 'Qt*' \
            -o -name 'libQt5*.so*' -o -name 'libQt5*.a' \
            -o -name 'libQt5*.prl' -o -name 'libicu*.so*' \
            -o -name 'libicu*.a' -o -name 'libpython*.so*' \
            -o -name 'libpython*.a' -o -name qt.conf \
            -o -iname 'qt5-*' -o -iname libicu \
            -o -iname '*.whl' -o -iname '*.zip' \
            -o -path '*/bin/python' -o -path '*/bin/python[0-9]*' \
            -o -type d -name 'python[0-9]*' \
            -o -name 'python[0-9]*.zip' \) -print -quit)
    [[ -z $forbidden ]] \
        || fatal "release bundles a production-owned Python/Qt artifact: $forbidden"

    while IFS= read -r -d '' entry; do
        if ! is_allowed_runtime_binary "$entry"; then
            fatal "release contains an unexpected shared library: $entry"
        fi
    done < <(find "$common_prefix" -type f \
        \( -name '*.so' -o -name '*.so.*' \) -print0)

    while IFS= read -r -d '' entry; do
        is_allowed_runtime_binary "$entry" && continue
        if readelf -h "$entry" >/dev/null 2>&1; then
            fatal "release contains an unexpected ELF binary: $entry"
        fi
    done < <(find "$common_prefix" -type f -print0)

    while IFS= read -r -d '' entry; do
        is_allowed_runtime_binary "$entry" && continue
        case $entry in
            "$common_prefix"/bin/aiassistant|\
            "$common_prefix"/bin/sico-ai-pinyin|\
            "$common_prefix"/bin/sico-ai-terminal|\
            "$common_prefix"/bin/sico-ai-terminal-pyqt|\
            "$common_prefix"/python/sico-ai) ;;
            *) fatal "release contains an unexpected executable file: $entry" ;;
        esac
    done < <(find "$common_prefix" -type f -perm /111 -print0)
}

if (( codex_enabled )); then
codex_manifest_value() {
    local key=$1
    awk -F= -v key="$key" '
        $1 == key { sub(/^[^=]*=/, ""); print; count += 1 }
        END { if (count != 1) exit 1 }
    ' "$codex_manifest"
}

[[ $(codex_manifest_value format) == cad-ai.codex-runtime.v1 ]] \
    || fatal "bundled Codex manifest has an unsupported format"
[[ $(codex_manifest_value target) == x86_64-unknown-linux-musl ]] \
    || fatal "bundled Codex manifest has an unsupported target"
codex_sha256=$(codex_manifest_value release_binary_sha256) \
    || fatal "bundled Codex manifest lacks release_binary_sha256"
[[ $codex_sha256 =~ ^[0-9a-f]{64}$ ]] \
    || fatal "bundled Codex manifest has an invalid release_binary_sha256"
actual_codex_sha256=$(sha256sum "$codex_runtime" | awk '{print $1}')
[[ $actual_codex_sha256 == "$codex_sha256" ]] \
    || fatal "bundled Codex executable checksum does not match its manifest"
codex_size=$(codex_manifest_value release_binary_size) \
    || fatal "bundled Codex manifest lacks release_binary_size"
[[ $codex_size =~ ^[1-9][0-9]*$ ]] \
    || fatal "bundled Codex manifest has an invalid release_binary_size"
[[ $(stat -c %s -- "$codex_runtime") == "$codex_size" ]] \
    || fatal "bundled Codex executable size does not match its manifest"
code_host_sha256=$(codex_manifest_value release_code_host_binary_sha256) \
    || fatal "bundled Codex manifest lacks release_code_host_binary_sha256"
[[ $code_host_sha256 =~ ^[0-9a-f]{64}$ ]] \
    || fatal "bundled Codex manifest has an invalid release_code_host_binary_sha256"
[[ $(sha256sum "$codex_code_host" | awk '{print $1}') == "$code_host_sha256" ]] \
    || fatal "bundled Codex code-mode host checksum does not match its manifest"
code_host_size=$(codex_manifest_value release_code_host_binary_size) \
    || fatal "bundled Codex manifest lacks release_code_host_binary_size"
[[ $code_host_size =~ ^[1-9][0-9]*$ ]] \
    || fatal "bundled Codex manifest has an invalid release_code_host_binary_size"
[[ $(stat -c %s -- "$codex_code_host") == "$code_host_size" ]] \
    || fatal "bundled Codex code-mode host size does not match its manifest"
codex_header=$(readelf -h "$codex_runtime" 2>/dev/null) \
    || fatal "bundled Codex executable is not an ELF file"
grep -Eq 'Class:[[:space:]]+ELF64' <<<"$codex_header" \
    || fatal "bundled Codex executable is not ELF64"
grep -Eq 'Machine:[[:space:]]+Advanced Micro Devices X86-64' <<<"$codex_header" \
    || fatal "bundled Codex executable is not x86-64"
grep -Eq 'Type:[[:space:]]+DYN([[:space:]]|$)' <<<"$codex_header" \
    || fatal "bundled Codex executable is not static PIE"
codex_program_headers=$(readelf -l "$codex_runtime") \
    || fatal "cannot inspect bundled Codex program headers"
if grep -Eq '(^|[[:space:]])INTERP([[:space:]]|$)|Requesting program interpreter' \
        <<<"$codex_program_headers"; then
    fatal "bundled Codex executable has a dynamic program interpreter"
fi
codex_dynamic=$(readelf -d "$codex_runtime" 2>&1 || true)
if grep -Fq '(NEEDED)' <<<"$codex_dynamic"; then
    fatal "bundled Codex executable has a dynamic library dependency"
fi
codex_versions=$(readelf --version-info "$codex_runtime" 2>&1 || true)
if grep -Eq 'GLIBC(_|XX_)|CXXABI_' <<<"$codex_versions"; then
    fatal "bundled Codex executable has a glibc or libstdc++ symbol dependency"
fi
codex_probe_home=$(mktemp -d)
trap 'rm -rf -- "$codex_probe_home"' EXIT
codex_version=$(env -i HOME="$codex_probe_home" PATH=/usr/bin:/bin \
    "$codex_runtime" --version 2>"$codex_probe_home/stderr") \
    || fatal "bundled Codex executable version probe failed"
[[ $codex_version == "codex-cli $(codex_manifest_value version)" ]] \
    || fatal "bundled Codex executable reports an unexpected version: $codex_version"
rm -rf -- "$codex_probe_home"
trap - EXIT
fi

verify_release_layout

version_at_most() {
    local actual=$1
    local limit=$2
    [[ $(printf '%s\n%s\n' "$actual" "$limit" | sort -V | tail -n 1) == "$limit" ]]
}

max_symbol_version() {
    local file=$1
    local expression=$2
    local prefix=$3
    local versions
    versions=$(readelf --version-info "$file" \
        | grep -oE "$expression" \
        | sed "s/^${prefix}//" \
        | sort -Vu || true)
    [[ -z $versions ]] || tail -n 1 <<<"$versions"
}

check_limit() {
    local file=$1
    local name=$2
    local expression=$3
    local prefix=$4
    local limit=$5
    local actual
    actual=$(max_symbol_version "$file" "$expression" "$prefix")
    if [[ -n $actual ]] && ! version_at_most "$actual" "$limit"; then
        fatal "$file requires $name $actual, above $release_label limit $limit"
    fi
    printf '  %-8s %s\n' "$name" "${actual:-none}"
}

for file in "$binding" "$library"; do
    elf_header=$(readelf -h "$file" 2>/dev/null) \
        || fatal "release ELF is unavailable: $file"
    grep -Eq 'Class:[[:space:]]+ELF64' <<<"$elf_header" \
        || fatal "release ELF is not 64-bit: $file"
    grep -Eq 'Machine:[[:space:]]+Advanced Micro Devices X86-64' \
        <<<"$elf_header" \
        || fatal "release ELF is not x86-64: $file"
    echo "$file"
    check_limit "$file" GLIBC 'GLIBC_[0-9]+([.][0-9]+)*' GLIBC_ "$glibc_limit"
    check_limit "$file" GLIBCXX 'GLIBCXX_[0-9]+([.][0-9]+)*' GLIBCXX_ "$glibcxx_limit"
    check_limit "$file" CXXABI 'CXXABI_[0-9]+([.][0-9]+)*' CXXABI_ "$cxxabi_limit"
done

binding_dynamic=$(readelf -d "$binding")
grep -Fq 'Shared library: [libqtermwidget5.so.1]' <<<"$binding_dynamic" \
    || fatal "QTermWidget binding does not link to libqtermwidget5.so.1"
grep -Fq "Library rpath: [\$ORIGIN/../lib]" <<<"$binding_dynamic" \
    || fatal "QTermWidget binding lacks the private old-style RPATH"
if grep -Fq '(RUNPATH)' <<<"$binding_dynamic"; then
    fatal "QTermWidget binding must not use RUNPATH"
fi
LC_ALL=C grep -aFq "$sip_abi_marker" "$binding" \
    || fatal "QTermWidget binding does not declare required SIP ABI $sip_abi_version"
if LC_ALL=C grep -aFq \
        'the terminal process is still running, trying to stop it by SIGHUP' \
        "$library"; then
    fatal "QTermWidget library lacks the session-shutdown patch"
fi
for file in "$binding" "$library"; do
    if LC_ALL=C grep -aEq \
            '/(software|workarea|zone[.]disk|home)/|/[.]build-[^/]*(/|$)' \
            "$file"; then
        fatal "release contains a build-machine absolute path: $file"
    fi
done

python=${SICO_PYTHON-}
if [[ -z $python && -n ${SICO_PYTHON_ROOT-} ]]; then
    python="$SICO_PYTHON_ROOT/bin/python3"
fi
[[ -n $python ]] || fatal "SICO_PYTHON or SICO_PYTHON_ROOT is required for release validation"
python=$(realpath -e -- "$python")
[[ -x $python ]] || fatal "production Python is not executable: $python"
python_library_path=${SICO_AI_PYTHON_LIBRARY_PATH-${SICO_SYSTEM_LD_LIBRARY_PATH-}}
smoke=$(env -u PYTHONPATH -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH \
    -u LD_PRELOAD -u LD_AUDIT \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH="$runtime_prefix/python:$common_prefix/python" \
    LD_LIBRARY_PATH="$runtime_prefix/lib${python_library_path:+:$python_library_path}" \
    SICO_AI_QTERMWIDGET_DATA_PATH="$resource_prefix/share/qtermwidget5" \
    SICO_AI_QTERMWIDGET_DATA_PATH="$resource_prefix/share/qtermwidget5" \
    QT_QPA_PLATFORM=offscreen "$python" -s - <<'PY'
from pathlib import Path

from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion
from PyQt5.QtWidgets import QApplication
import PyQt5
import QTermWidget as binding
from QTermWidget import QTermWidget

app = QApplication(["cad-ai-release-smoke"])
terminal = QTermWidget(0)
terminal.setHistorySize(17)
assert terminal.screenLinesCount() > 0
assert terminal.keyBindings() == "default"
print(PYQT_VERSION_STR)
print(QT_VERSION_STR)
print(qVersion())
print(Path(PyQt5.__file__).resolve())
print(Path(binding.__file__).resolve())
terminal.close()
terminal.deleteLater()
app.processEvents()
PY
) || fatal "production PyQt5/QTermWidget import smoke failed"
mapfile -t smoke_lines <<<"$smoke"
[[ ${#smoke_lines[@]} -eq 5 ]] || fatal "production PyQt5 smoke returned invalid metadata"
[[ ${smoke_lines[0]} == 5.* && ${smoke_lines[1]} == 5.* \
    && ${smoke_lines[2]} == 5.* ]] \
    || fatal "production Python does not provide a Qt 5 PyQt5 runtime"
case ${smoke_lines[4]} in
    "$runtime_prefix"/python/QTermWidget.abi3.so) ;;
    *) fatal "production Python loaded an unexpected QTermWidget binding: ${smoke_lines[4]}" ;;
esac
case ${smoke_lines[3]} in
    "$runtime_prefix"/*|"$common_prefix"/*)
        fatal "release unexpectedly supplies PyQt5: ${smoke_lines[3]}"
        ;;
esac

echo "$release_label QTermWidget runtime checks passed"
echo "  PyQt5   ${smoke_lines[0]}"
echo "  Qt ABI  ${smoke_lines[1]}"
echo "  Qt run  ${smoke_lines[2]}"
