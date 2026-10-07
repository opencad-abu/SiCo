#!/usr/bin/env bash
set -euo pipefail

# The former source distribution contract was retired on 2026-09-11.
cat >&2 <<'EOF'
CAD runtime-only policy: this legacy source release entry point is disabled.
Use deploy/package_runtime_release.py with an inventoried, qualified native
runtime stage. Project docs, AI skill/prompt/reference assets, SKILL sources,
Python sources/bytecode and source bundles must not enter a release archive.
See deploy/RUNTIME_RELEASE_POLICY.md for compilation and qualification rules.
EOF
exit 2
