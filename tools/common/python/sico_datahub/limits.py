"""Public DataHub size and shape limits shared with the wire shim."""

from sico_datahub_wire import (
    DEFAULT_DEADLINE_MS,
    MAX_DEADLINE_MS,
    MAX_DEPTH,
    MAX_ENVELOPE_BYTES,
    MAX_ID,
    MAX_PAYLOAD_BYTES,
    MAX_SEQUENCE,
    MAX_TEXT,
    check_text,
    check_value,
)

DEFAULT_PAGE_LIMIT = 50

__all__ = [
    "DEFAULT_DEADLINE_MS",
    "DEFAULT_PAGE_LIMIT",
    "MAX_DEADLINE_MS",
    "MAX_DEPTH",
    "MAX_ENVELOPE_BYTES",
    "MAX_ID",
    "MAX_PAYLOAD_BYTES",
    "MAX_SEQUENCE",
    "MAX_TEXT",
    "check_text",
    "check_value",
]
