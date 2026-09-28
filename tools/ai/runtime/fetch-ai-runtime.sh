#!/usr/bin/env bash
# Download the SiCo AI runtimes (OpenAI Codex CLI + ripgrep) from the GitHub release.
set -euo pipefail

VERSION=${SICO_AI_RUNTIME_VERSION:-v0.0.1-202610}
BASE=${SICO_AI_RUNTIME_BASE_URL:-https://github.com/opencad-abu/SiCo/releases/download/${VERSION}}
ROOT=$(cd -- "$(dirname -- "$0")" && pwd)
ASSETS=(
  codex-0.156.1-x86_64-unknown-linux-musl.tar.gz
  ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz
)

tmp=$(mktemp -d)
trap 'rm -r "$tmp"' EXIT
for asset in "${ASSETS[@]}"; do
  curl -fL --retry 3 "${BASE}/${asset}" -o "${tmp}/${asset}"
done
curl -fL --retry 3 "${BASE}/SHA256SUMS" -o "${tmp}/SHA256SUMS"
(cd "${tmp}" && sha256sum -c SHA256SUMS)
for asset in "${ASSETS[@]}"; do
  tar -xzf "${tmp}/${asset}" -C "${ROOT}"
  echo "installed ${asset}"
done
