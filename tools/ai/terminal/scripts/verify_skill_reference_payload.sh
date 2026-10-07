#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/sico-build-environment.sh"
unset CDPATH

usage() {
    echo "usage: $0 REFERENCE_ROOT [SQLITE3_SHA256 JSON_GZ_SHA256]" >&2
}

fatal() {
    echo "SKILL reference payload: $*" >&2
    exit 1
}

if [[ $# -ne 1 && $# -ne 3 ]]; then
    usage
    exit 2
fi

reference_input=$1
if [[ ! -d $reference_input || -L $reference_input ]]; then
    fatal "reference root must be a real directory: $reference_input"
fi
reference_root=$(realpath -e -- "$reference_input") \
    || fatal "reference root is unavailable: $reference_input"

database="$reference_root/skill_api.sqlite3"
fallback="$reference_root/skill_api.json.gz"
for payload in "$database" "$fallback"; do
    if [[ ! -f $payload || -L $payload || ! -s $payload ]]; then
        fatal "required file is missing, empty, or not regular: $payload"
    fi
done

if [[ $# -eq 3 ]]; then
    expected_database_sha256=$2
    expected_fallback_sha256=$3
    for expected in "$expected_database_sha256" "$expected_fallback_sha256"; do
        [[ $expected =~ ^[0-9a-f]{64}$ ]] \
            || fatal "expected SHA256 must contain 64 lowercase hexadecimal characters"
    done

    actual_database_sha256=$(sha256sum "$database" | awk '{print $1}')
    actual_fallback_sha256=$(sha256sum "$fallback" | awk '{print $1}')
    [[ $actual_database_sha256 == "$expected_database_sha256" ]] \
        || fatal "checksum mismatch: skill_api.sqlite3"
    [[ $actual_fallback_sha256 == "$expected_fallback_sha256" ]] \
        || fatal "checksum mismatch: skill_api.json.gz"
fi

select_validation_python() {
    local candidate
    local -a candidates
    if [[ -n ${SICO_AI_REFERENCE_VALIDATION_PYTHON-} ]]; then
        candidates=("$SICO_AI_REFERENCE_VALIDATION_PYTHON")
    else
        candidates=(/usr/bin/python3 python3)
    fi
    for candidate in "${candidates[@]}"; do
        [[ -n $candidate ]] || continue
        if command -v -- "$candidate" >/dev/null 2>&1 \
                && run_validation_python \
                    "$candidate" -c 'import gzip, json, sqlite3, zlib' \
                    >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

run_validation_python() {
    env -u PYTHONHOME -u PYTHONPATH -u LD_PRELOAD -u LD_AUDIT \
        PYTHONNOUSERSITE=1 \
        LD_LIBRARY_PATH="${SICO_AI_REFERENCE_VALIDATION_LD_LIBRARY_PATH-}" \
        "$@"
}

validation_python=$(select_validation_python) \
    || fatal "a Python interpreter with sqlite3 and zlib is required for validation"
if ! run_validation_python "$validation_python" - "$database" "$fallback" <<'PY'
import gzip
import json
import os
import sqlite3
import sys
import zlib
from pathlib import Path

MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_FUNCTIONS = 100_000
REQUIRED_FIELDS = (
    "reference_id",
    "name",
    "usage",
    "doc_set",
    "document",
    "doc_title",
    "product_version",
    "body",
)


def validate_metadata(metadata):
    if not isinstance(metadata, dict):
        raise ValueError("metadata has the wrong type")
    function_count = metadata.get("function_count")
    document_count = metadata.get("document_count")
    copyrights = metadata.get("copyrights")
    if isinstance(function_count, bool) or not isinstance(function_count, int):
        raise ValueError("function_count is not an integer")
    if isinstance(document_count, bool) or not isinstance(document_count, int):
        raise ValueError("document_count is not an integer")
    if function_count < 0 or not 0 <= document_count <= function_count:
        raise ValueError("reference counts are inconsistent")
    if not isinstance(copyrights, list) or not all(
        isinstance(value, str) for value in copyrights
    ):
        raise ValueError("copyrights is not a string list")
    return {
        "function_count": function_count,
        "document_count": document_count,
        "copyrights": copyrights,
    }


def normalize_records(records, source):
    if not isinstance(records, list) or len(records) > MAX_FUNCTIONS:
        raise ValueError("{} functions list is invalid".format(source))
    normalized = {}
    for record in records:
        if not isinstance(record, dict) or not all(
            isinstance(record.get(field), str) for field in REQUIRED_FIELDS
        ):
            raise ValueError("{} contains an invalid function record".format(source))
        selected = {field: record[field] for field in REQUIRED_FIELDS}
        reference_id = selected["reference_id"].casefold()
        if reference_id in normalized:
            raise ValueError("{} contains duplicate reference_id values".format(source))
        normalized[reference_id] = selected
    return normalized


try:
    database_path, fallback_path = sys.argv[1:]
    if os.path.getsize(fallback_path) > MAX_COMPRESSED_BYTES:
        raise ValueError("compressed JSON exceeds the size limit")
    with gzip.open(fallback_path, "rb") as handle:
        encoded = handle.read(MAX_EXPANDED_BYTES + 1)
    if len(encoded) > MAX_EXPANDED_BYTES:
        raise ValueError("compressed JSON expands beyond the size limit")
    payload = json.loads(encoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON payload has the wrong type")
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported schema version")
    json_records = normalize_records(payload.get("functions"), "JSON")
    json_metadata = validate_metadata(payload.get("metadata"))
    if json_metadata["function_count"] != len(json_records):
        raise ValueError("JSON function count does not match its records")

    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite quick_check failed")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("unsupported SQLite schema version")
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(functions)")
        }
        missing = set(REQUIRED_FIELDS) - columns
        if missing:
            raise ValueError("SQLite functions table is missing required columns")
        selected_fields = ", ".join(REQUIRED_FIELDS)
        database_records = normalize_records(
            [dict(row) for row in connection.execute(
                "SELECT {} FROM functions".format(selected_fields)
            )],
            "SQLite",
        )
        raw_metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    database_metadata = validate_metadata(
        {
            "function_count": int(raw_metadata["function_count"]),
            "document_count": int(raw_metadata["document_count"]),
            "copyrights": json.loads(raw_metadata["copyrights"]),
        }
    )
    if database_metadata["function_count"] != len(database_records):
        raise ValueError("SQLite function count does not match its records")
    if database_metadata != json_metadata or database_records != json_records:
        raise ValueError("compressed JSON differs from SQLite")
except (
    EOFError,
    KeyError,
    OSError,
    TypeError,
    UnicodeDecodeError,
    ValueError,
    json.JSONDecodeError,
    sqlite3.Error,
    zlib.error,
) as error:
    print("invalid SKILL reference payload: {}".format(error), file=sys.stderr)
    raise SystemExit(1)
PY
then
    fatal "compressed JSON is invalid or differs from SQLite: $fallback"
fi
