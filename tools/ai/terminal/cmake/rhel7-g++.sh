#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/../scripts/sico-build-environment.sh"

gcc_root=${SICO_AI_RHEL7_GCC_ROOT-/software/pkgs/gcc/v4.8.5-rhel7}
exec env LD_LIBRARY_PATH="$gcc_root/lib64" "$gcc_root/bin/g++" "$@"
