#!/usr/bin/env bash
set -euo pipefail

fatal() { echo "ripgrep runtime verification: $*" >&2; exit 1; }
[[ $# -ge 1 && $# -le 2 ]] || fatal "usage: $0 AI_ROOT [PINNED_MANIFEST]"
root=$(realpath -e -- "$1")
runtime="$root/runtime/ripgrep/x86_64-unknown-linux-musl"
binary="$runtime/bin/rg"
manifest="$runtime/MANIFEST.txt"
[[ -f $binary && -x $binary && ! -L $binary ]] \
    || fatal "bundled rg executable is unavailable: $binary"
[[ -f $manifest && ! -L $manifest ]] || fatal "bundled rg manifest is unavailable"
if [[ $# -eq 2 ]]; then
    cmp -s -- "$manifest" "$2" || fatal "ripgrep manifest does not match the committed pin"
fi
value() {
    awk -F= -v key="$1" '
        $1 == key { sub(/^[^=]*=/, ""); print; count += 1 }
        END { if (count != 1) exit 1 }
    ' "$manifest"
}
[[ $(value format) == cad-ai.ripgrep-runtime.v1 ]] || fatal "unsupported manifest format"
[[ $(value target) == x86_64-unknown-linux-musl ]] || fatal "unsupported runtime target"
expected_sha=$(value binary_sha256)
expected_size=$(value binary_size)
[[ $expected_sha =~ ^[0-9a-f]{64}$ && $expected_size =~ ^[1-9][0-9]*$ ]] \
    || fatal "invalid pinned size or checksum"
[[ $(stat -c %s -- "$binary") == "$expected_size" ]] || fatal "rg size mismatch"
[[ $(sha256sum "$binary" | awk '{print $1}') == "$expected_sha" ]] || fatal "rg checksum mismatch"
header=$(readelf -h "$binary") || fatal "rg is not ELF"
grep -Eq 'Class:[[:space:]]+ELF64' <<<"$header" || fatal "rg is not ELF64"
grep -Eq 'Machine:[[:space:]]+Advanced Micro Devices X86-64' <<<"$header" || fatal "rg is not x86-64"
grep -Eq 'Type:[[:space:]]+DYN([[:space:]]|$)' <<<"$header" || fatal "rg is not static PIE"
programs=$(readelf -l "$binary")
if grep -Eq '(^|[[:space:]])INTERP([[:space:]]|$)|Requesting program interpreter' <<<"$programs"; then
    fatal "rg has a dynamic program interpreter"
fi
dynamic=$(readelf -d "$binary")
if grep -Fq '(NEEDED)' <<<"$dynamic"; then fatal "rg has dynamic dependencies"; fi
versions=$(readelf --version-info "$binary")
if grep -Eq 'GLIBC(_|XX_)|CXXABI_' <<<"$versions"; then fatal "rg has glibc/libstdc++ dependencies"; fi
reported=$(env -i PATH=/usr/bin:/bin "$binary" --version) || fatal "rg version probe failed"
[[ ${reported%%$'\n'*} == "$(value cli_version)" ]] || fatal "rg version mismatch"
for notice in ripgrep-LICENSE-MIT.txt ripgrep-UNLICENSE.txt ripgrep-NOTICE.md \
        ripgrep-PCRE2-LICENCE.txt ripgrep-musl-COPYRIGHT.txt; do
    [[ -s $root/LICENSES/$notice && ! -L $root/LICENSES/$notice ]] \
        || fatal "missing ripgrep notice: $notice"
done
echo "bundled ripgrep $(value version) verified (static x86-64)"
