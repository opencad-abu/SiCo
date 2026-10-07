#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
umask 022

usage() {
    echo "usage: $0 OUTPUT_DIRECTORY [PINNED_CODEX [PINNED_CODE_MODE_HOST]]" >&2
}

fatal() {
    echo "Codex runtime import: $*" >&2
    exit 1
}

if [[ $# -lt 1 || $# -gt 3 ]]; then
    usage
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
manifest=$(realpath -e -- "$script_dir/../codex-runtime.lock") \
    || fatal "pinned runtime manifest is unavailable"

manifest_value() {
    local key=$1
    awk -F= -v key="$key" '
        $1 == key { sub(/^[^=]*=/, ""); print; count += 1 }
        END { if (count != 1) exit 1 }
    ' "$manifest" || fatal "manifest key is missing or duplicated: $key"
}

format=$(manifest_value format)
version=$(manifest_value version)
cli_version=$(manifest_value cli_version)
target=$(manifest_value target)

[[ $format == cad-ai.codex-runtime.v1 ]] \
    || fatal "unsupported manifest format: $format"
[[ $version =~ ^[0-9]+[.][0-9]+[.][0-9]+$ ]] \
    || fatal "manifest has an invalid version"
[[ $target == x86_64-unknown-linux-musl ]] \
    || fatal "manifest has an unsupported target: $target"

for command in awk curl env readelf realpath sha256sum stat tar; do
    command -v "$command" >/dev/null 2>&1 \
        || fatal "required command is unavailable: $command"
done

output=$(realpath -m -- "$1")
[[ $output != / && ! -e $output ]] \
    || fatal "output directory already exists or is unsafe: $output"
output_parent=$(dirname -- "$output")
mkdir -p -- "$output_parent"
output_parent=$(realpath -e -- "$output_parent")
output="$output_parent/$(basename -- "$output")"

temporary=$(mktemp -d -p "$output_parent" '.cad-codex-runtime.XXXXXX')
download=''
trap 'rm -rf -- "$temporary"; [[ -z $download ]] || rm -f -- "$download"' EXIT

verify_pinned_archive() {
    local candidate=$1
    [[ -f $candidate && ! -L $candidate ]] || return 1
    [[ $(stat -c %s -- "$candidate") == "$archive_size" ]] || return 1
    [[ $(sha256sum "$candidate" | awk '{print $1}') == "$archive_sha256" ]]
}

download_pinned_archive() {
    local cache_root=$1
    local cached_archive=$2
    local actual actual_size

    download=$(mktemp -p "$cache_root" '.codex-download.XXXXXX')
    curl --fail --location --retry 3 --output "$download" "$archive_url" \
        || fatal "cannot download pinned Codex archive"
    actual_size=$(stat -c %s -- "$download")
    [[ $actual_size == "$archive_size" ]] \
        || fatal "downloaded archive size mismatch"
    actual=$(sha256sum "$download" | awk '{print $1}')
    [[ $actual == "$archive_sha256" ]] \
        || fatal "downloaded archive SHA256 mismatch"
    mv -T -- "$download" "$cached_archive" \
        || fatal "cannot update Codex archive cache"
    download=''
}

payload_root="$temporary/payload"
install -d -m 0755 -- "$payload_root/$target/bin"

for artifact in codex codex-code-mode-host; do
prefix=''
source_payload=${2:-}
if [[ $artifact == codex-code-mode-host ]]; then
    prefix=code_host_
    source_payload=${3:-}
fi
archive_url=$(manifest_value "${prefix}archive_url")
archive_member=$(manifest_value "${prefix}archive_member")
archive_size=$(manifest_value "${prefix}archive_size")
archive_sha256=$(manifest_value "${prefix}archive_sha256")
binary_size=$(manifest_value "${prefix}binary_size")
binary_sha256=$(manifest_value "${prefix}binary_sha256")
[[ $archive_member == "$artifact-$target" ]] \
    || fatal "manifest has an unexpected archive member for $artifact"
[[ $archive_size =~ ^[1-9][0-9]*$ && $binary_size =~ ^[1-9][0-9]*$ ]] \
    || fatal "manifest has an invalid payload size for $artifact"
[[ $archive_sha256 =~ ^[0-9a-f]{64}$ && $binary_sha256 =~ ^[0-9a-f]{64}$ ]] \
    || fatal "manifest has an invalid SHA256 for $artifact"
if [[ -z $source_payload ]]; then
    cache_root=$(realpath -m -- "$(pwd -P)/.cad/codex-runtime-cache")
    mkdir -p -- "$cache_root"
    cache_root=$(realpath -e -- "$cache_root")
    cached_archive="$cache_root/$artifact-$target-$version.tar.gz"
    if [[ -e $cached_archive ]]; then
        [[ -f $cached_archive && ! -L $cached_archive ]] \
            || fatal "cached archive is not a regular file: $cached_archive"
        if ! verify_pinned_archive "$cached_archive"; then
            download_pinned_archive "$cache_root" "$cached_archive"
        fi
    else
        download_pinned_archive "$cache_root" "$cached_archive"
    fi
    source_payload=$cached_archive
fi

source_payload=$(realpath -e -- "$source_payload") \
    || fatal "input payload is unavailable"
[[ -f $source_payload && ! -L $source_payload ]] \
    || fatal "input payload must be a regular file"
source_sha256=$(sha256sum "$source_payload" | awk '{print $1}')
source_size=$(stat -c %s -- "$source_payload")

codex="$payload_root/$target/bin/$artifact"
if [[ $source_sha256 == "$archive_sha256" && $source_size == "$archive_size" ]]; then
    mapfile -t archive_members < <(tar -tzf "$source_payload") \
        || fatal "pinned Codex archive cannot be listed"
    if [[ ${#archive_members[@]} -ne 1 \
            || ${archive_members[0]} != "$archive_member" ]]; then
        fatal "pinned Codex archive does not contain exactly $archive_member"
    fi
    extract_root="$temporary/extract-$artifact"
    install -d -m 0755 -- "$extract_root"
    tar --no-same-owner --no-same-permissions \
        -xzf "$source_payload" -C "$extract_root" -- "$archive_member" \
        || fatal "cannot extract pinned Codex archive"
    extracted="$extract_root/$archive_member"
    [[ -f $extracted && ! -L $extracted ]] \
        || fatal "archive member is not a regular file"
    install -m 0755 -- "$extracted" "$codex"
elif [[ $source_sha256 == "$binary_sha256" && $source_size == "$binary_size" ]]; then
    install -m 0755 -- "$source_payload" "$codex"
else
    fatal "input is neither the pinned archive nor the pinned executable"
fi

actual=$(sha256sum "$codex" | awk '{print $1}')
[[ $actual == "$binary_sha256" ]] || fatal "Codex executable SHA256 mismatch"
actual_size=$(stat -c %s -- "$codex")
[[ $actual_size == "$binary_size" ]] || fatal "Codex executable size mismatch"

elf_header=$(readelf -h "$codex" 2>/dev/null) \
    || fatal "Codex executable is not an ELF file"
grep -Eq 'Class:[[:space:]]+ELF64' <<<"$elf_header" \
    || fatal "Codex executable is not ELF64"
grep -Eq 'Machine:[[:space:]]+Advanced Micro Devices X86-64' <<<"$elf_header" \
    || fatal "Codex executable is not x86-64"
grep -Eq 'Type:[[:space:]]+DYN([[:space:]]|$)' <<<"$elf_header" \
    || fatal "Codex executable is not static PIE"
program_headers=$(readelf -l "$codex") \
    || fatal "cannot inspect Codex program headers"
if grep -Eq '(^|[[:space:]])INTERP([[:space:]]|$)|Requesting program interpreter' \
        <<<"$program_headers"; then
    fatal "Codex executable has a dynamic program interpreter"
fi
dynamic=$(readelf -d "$codex" 2>&1 || true)
if grep -Fq '(NEEDED)' <<<"$dynamic"; then
    fatal "Codex executable has a dynamic library dependency"
fi
versions=$(readelf --version-info "$codex" 2>&1 || true)
if grep -Eq 'GLIBC(_|XX_)|CXXABI_' <<<"$versions"; then
    fatal "Codex executable has a glibc or libstdc++ symbol dependency"
fi
done

probe_home="$temporary/home"
install -d -m 0700 -- "$probe_home"
reported=$(env -i HOME="$probe_home" PATH=/usr/bin:/bin \
    "$payload_root/$target/bin/codex" --version 2>"$probe_home/stderr") \
    || fatal "Codex executable version probe failed"
[[ $reported == "$cli_version" ]] \
    || fatal "Codex executable reports '$reported', expected '$cli_version'"

install -m 0644 -- "$manifest" "$payload_root/$target/MANIFEST.txt"
chmod 0755 -- "$payload_root" "$payload_root/$target" "$payload_root/$target/bin"
mv -T -n -- "$payload_root" "$output" \
    || fatal "output directory appeared during import: $output"
[[ ! -e $payload_root ]] \
    || fatal "output directory appeared during import: $output"

trap - EXIT
rm -rf -- "$temporary"
echo "imported $cli_version ($target) into $output"
echo "  codex SHA256 $(manifest_value binary_sha256)"
echo "  code-mode host SHA256 $(manifest_value code_host_binary_sha256)"
