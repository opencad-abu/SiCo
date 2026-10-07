#!/usr/bin/env bash
set -euo pipefail
unset CDPATH
umask 022

fatal() {
    echo "restore source bundles: $*" >&2
    exit 1
}

if [[ $# -ne 1 ]]; then
    echo "usage: $0 OUTPUT_DIRECTORY" >&2
    exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
bundle_root="$script_dir/../third_party/source-bundles"
ai_root=$(realpath -e -- "$script_dir/../..") \
    || fatal "AI runtime root is unavailable"
manifest=$(realpath -e -- "$script_dir/../../../SOURCE-RUNTIME-MANIFEST.txt") \
    || fatal "source runtime manifest is unavailable"
output=$(realpath -m -- "$1")
if [[ $output == / || -e $output ]]; then
    echo "output directory already exists or is unsafe: $output" >&2
    exit 2
fi
parent=$(dirname -- "$output")
mkdir -p -- "$parent"
parent=$(realpath -e -- "$parent")
output="$parent/$(basename -- "$output")"
temporary=$(mktemp -d -p "$parent" '.cad-ai-source-deps.XXXXXX')
trap 'rm -rf -- "$temporary"' EXIT

manifest_value() {
    local key=$1
    awk -F= -v key="$key" '
        $1 == key { sub(/^[^=]*=/, ""); print; count += 1 }
        END { if (count != 1) exit 1 }
    ' "$manifest" || fatal "manifest key is missing or duplicated: $key"
}

format=$(manifest_value format)
[[ $format == cad-tools.source-runtime.v6 || $format == cad-tools.source-runtime.v7 ]] \
    || fatal "unsupported manifest format: $format"
qtermwidget_commit=$(manifest_value qtermwidget_commit)
lxqt_commit=$(manifest_value lxqt_build_tools_commit)
[[ $qtermwidget_commit == 58981da1625810aa0a3dcc605bacba73f580ccdd ]] \
    || fatal "manifest has an unexpected QTermWidget commit"
[[ $lxqt_commit == 1304079edbe62c8c9e528de8ee0cf1a1119724dc ]] \
    || fatal "manifest has an unexpected lxqt-build-tools commit"
"$script_dir/verify_skill_reference_payload.sh" \
    "$script_dir/../../reference/cadence-skill" \
    "$(manifest_value skill_api_sqlite3_sha256)" \
    "$(manifest_value skill_api_json_gz_sha256)"

verify_runtime_file() {
    local relative=$1
    local manifest_key=$2
    local label=$3
    local payload="$ai_root/$relative"
    local expected_sha256
    local actual_sha256

    [[ -f $payload && ! -L $payload ]] \
        || fatal "runtime payload is unavailable: $label"
    expected_sha256=$(manifest_value "$manifest_key")
    [[ $expected_sha256 =~ ^[0-9a-f]{64}$ ]] \
        || fatal "manifest has an invalid runtime checksum: $label"
    actual_sha256=$(sha256sum "$payload" | awk '{print $1}')
    [[ $actual_sha256 == "$expected_sha256" ]] \
        || fatal "runtime payload checksum mismatch: $label"
}

verify_runtime_file bin/sico-ai-terminal terminal_dispatcher_sha256 \
    'terminal dispatcher'
verify_runtime_file bin/sico-ai-pinyin pinyin_wrapper_sha256 \
    'pinyin wrapper'
verify_runtime_file bin/sico-ai-terminal-pyqt pyqt_launcher_sha256 \
    'PyQt5 launcher'
verify_runtime_file bin/aiassistant assistant_launcher_sha256 \
    'AI Assistant launcher'
[[ -x $ai_root/bin/aiassistant ]] \
    || fatal 'runtime payload is not executable: AI Assistant launcher'
for platform in rhel7 rhel8; do
    verify_runtime_file \
        "runtime/$platform/python/QTermWidget.abi3.so" \
        "${platform}_qtermwidget_binding_sha256" \
        "$platform QTermWidget binding"
    verify_runtime_file \
        "runtime/$platform/lib/libqtermwidget5.so.1" \
        "${platform}_qtermwidget_library_sha256" \
        "$platform QTermWidget library"
done
if [[ $format == cad-tools.source-runtime.v7 ]]; then
    verify_runtime_file \
        runtime/codex/x86_64-unknown-linux-musl/bin/codex \
        codex_runtime_sha256 'bundled Codex runtime'
    [[ -f $ai_root/runtime/codex/x86_64-unknown-linux-musl/MANIFEST.txt \
            && ! -L $ai_root/runtime/codex/x86_64-unknown-linux-musl/MANIFEST.txt ]] \
        || fatal 'bundled Codex manifest is unavailable'
    cmp -s -- "$ai_root/runtime/codex/x86_64-unknown-linux-musl/MANIFEST.txt" \
        "$ai_root/terminal/codex-runtime.lock" \
        || fatal 'bundled Codex manifest does not match the committed pin'
fi

verify_bundle() {
    local bundle=$1
    local tag=$2
    local expected_commit=$3
    local expected_sha256=$4
    local label=$5
    local actual_sha256
    local resolved
    local verify_repository="$temporary/verify-$label"
    local -a heads

    [[ -s $bundle && ! -L $bundle ]] || fatal "source bundle is unavailable: $label"
    [[ $expected_sha256 =~ ^[0-9a-f]{64}$ ]] \
        || fatal "manifest has an invalid bundle checksum: $label"
    actual_sha256=$(sha256sum "$bundle" | awk '{print $1}')
    [[ $actual_sha256 == "$expected_sha256" ]] \
        || fatal "source bundle checksum mismatch: $label"
    mapfile -t heads < <(git bundle list-heads "$bundle")
    if [[ ${#heads[@]} -ne 1 || ${heads[0]} != *" refs/tags/$tag" ]]; then
        fatal "source bundle does not contain exactly the pinned tag: $label $tag"
    fi
    git init --bare --quiet "$verify_repository"
    git -C "$verify_repository" bundle verify "$bundle" >/dev/null
    git -C "$verify_repository" fetch --quiet "$bundle" \
        "refs/tags/$tag:refs/tags/$tag"
    resolved=$(git -C "$verify_repository" rev-parse "refs/tags/$tag^{commit}")
    [[ $resolved == "$expected_commit" ]] \
        || fatal "source bundle tag does not match pinned commit: $label $tag"
    rm -rf -- "$verify_repository"
}

verify_bundle "$bundle_root/qtermwidget-1.4.0.bundle" 1.4.0 \
    "$qtermwidget_commit" "$(manifest_value qtermwidget_bundle_sha256)" qtermwidget
verify_bundle "$bundle_root/lxqt-build-tools-0.13.0.bundle" 0.13.0 \
    "$lxqt_commit" "$(manifest_value lxqt_build_tools_bundle_sha256)" lxqt-build-tools

git clone --quiet "$bundle_root/qtermwidget-1.4.0.bundle" "$temporary/qtermwidget"
git -C "$temporary/qtermwidget" checkout --quiet --detach \
    "$qtermwidget_commit"
[[ $(git -C "$temporary/qtermwidget" rev-parse 'refs/tags/1.4.0^{commit}') \
    == "$qtermwidget_commit" ]] || fatal "restored QTermWidget tag is invalid"
git clone --quiet "$bundle_root/lxqt-build-tools-0.13.0.bundle" \
    "$temporary/lxqt-build-tools"
git -C "$temporary/lxqt-build-tools" checkout --quiet --detach \
    "$lxqt_commit"
[[ $(git -C "$temporary/lxqt-build-tools" rev-parse 'refs/tags/0.13.0^{commit}') \
    == "$lxqt_commit" ]] || fatal "restored lxqt-build-tools tag is invalid"

chmod 0755 -- "$temporary"
mv -T -n -- "$temporary" "$output"
[[ ! -e $temporary ]] || fatal "output directory appeared while restoring: $output"
trap - EXIT
echo "restored pinned source repositories at $output"
