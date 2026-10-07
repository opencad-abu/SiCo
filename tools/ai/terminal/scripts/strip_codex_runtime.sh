#!/usr/bin/env bash
# Reduce the pinned Codex runtime in a release stage to its shipped identity.
#
# The importer and CMake carry the pinned upstream binaries. The shipped release
# carries the stripped variants recorded as release_* in codex-runtime.lock, so
# the packaging flow runs this step between "cmake --install" and the release
# verification. Only the recorded upstream and shipped identities are accepted.
set -euo pipefail
unset CDPATH
umask 022

usage() {
    echo "usage: $0 PREFIX" >&2
}

fatal() {
    echo "Codex runtime strip: $*" >&2
    exit 1
}

if [[ $# -ne 1 ]]; then
    usage
    exit 2
fi

target=x86_64-unknown-linux-musl
prefix=$(realpath -e -- "$1") || fatal "release prefix is unavailable: $1"
runtime="$prefix/runtime/codex/$target"
manifest="$runtime/MANIFEST.txt"
[[ -f $manifest && ! -L $manifest ]] \
    || fatal "shipped Codex manifest is unavailable: $manifest"

manifest_value() {
    local key=$1
    awk -F= -v key="$key" '
        $1 == key { sub(/^[^=]*=/, ""); print; count += 1 }
        END { if (count != 1) exit 1 }
    ' "$manifest" || fatal "manifest key is missing or duplicated: $key"
}

for command in awk chmod mktemp mv realpath sha256sum stat strip; do
    command -v "$command" >/dev/null 2>&1 \
        || fatal "required command is unavailable: $command"
done

# Only this invocation reproduces the shipped identity recorded in the manifest.
strip_flags=(
    --strip-all
    --remove-section=.gnu_debuglink
    --remove-section=.gnu_debugdata
    --remove-section=.debug_gdb_scripts
)

workspace=$(mktemp -d -p "$runtime" '.codex-strip.XXXXXX') \
    || fatal "cannot create a strip workspace below $runtime"
trap 'rm -rf -- "$workspace"' EXIT

strip_artifact() {
    local name=$1 upstream_size=$2 upstream_sha=$3 shipped_size=$4 shipped_sha=$5
    local binary="$runtime/bin/$name" staged="$workspace/$name" size sha
    [[ -f $binary && ! -L $binary ]] || fatal "runtime binary is unavailable: $binary"
    size=$(stat -c %s -- "$binary")
    sha=$(sha256sum "$binary" | awk '{print $1}')
    if [[ $size == "$shipped_size" && $sha == "$shipped_sha" ]]; then
        echo "already stripped: $name"
        return 0
    fi
    [[ $size == "$upstream_size" && $sha == "$upstream_sha" ]] \
        || fatal "$name is neither the pinned upstream binary nor the shipped one"
    strip "${strip_flags[@]}" -o "$staged" "$binary" || fatal "cannot strip $name"
    chmod 0755 -- "$staged"
    size=$(stat -c %s -- "$staged")
    sha=$(sha256sum "$staged" | awk '{print $1}')
    [[ $size == "$shipped_size" && $sha == "$shipped_sha" ]] \
        || fatal "$name does not reproduce the recorded shipped identity"
    mv -T -- "$staged" "$binary" || fatal "cannot publish the stripped $name"
    echo "stripped $name: size $size SHA256 $sha"
}

strip_artifact codex \
    "$(manifest_value binary_size)" \
    "$(manifest_value binary_sha256)" \
    "$(manifest_value release_binary_size)" \
    "$(manifest_value release_binary_sha256)"
strip_artifact codex-code-mode-host \
    "$(manifest_value code_host_binary_size)" \
    "$(manifest_value code_host_binary_sha256)" \
    "$(manifest_value release_code_host_binary_size)" \
    "$(manifest_value release_code_host_binary_sha256)"

echo "Codex runtime strip: $runtime now carries the shipped identity"
