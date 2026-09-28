"""Restricted requests from the MTS GUI to its owning Virtuoso process."""

from __future__ import annotations

import sys
from typing import TextIO

from ..errors import RequestValidationError
from ..model import validate_oa_name


OPEN_VIEW_RECORD = "MTS_OPEN_VIEW"
OPENABLE_RESULT_VIEWS = frozenset({"symbol", "spectreText", "spiceText", "spectre"})


def encode_open_view_request(library: str, cell: str, view: str) -> str:
    """Encode one fixed-operation, line-oriented host request.

    The receiving SKILL launcher parses fields as data and never evaluates the
    line.  Restricting every field to an OA identifier keeps tabs, newlines,
    quoting, and executable SKILL outside the protocol by construction.
    """

    safe_library = validate_oa_name(library, "result library")
    safe_cell = validate_oa_name(cell, "result cell")
    safe_view = validate_oa_name(view, "result view")
    if safe_view not in OPENABLE_RESULT_VIEWS:
        raise RequestValidationError(
            f"unsupported MTS result view: {safe_view!r}"
        )
    return "\t".join((OPEN_VIEW_RECORD, safe_library, safe_cell, safe_view))


def emit_open_view_request(
    library: str,
    cell: str,
    view: str,
    *,
    stream: TextIO | None = None,
) -> str:
    """Write and flush one open-view request to the Virtuoso IPC stdout."""

    record = encode_open_view_request(library, cell, view)
    destination = sys.stdout if stream is None else stream
    destination.write(record + "\n")
    destination.flush()
    return record


__all__ = [
    "OPENABLE_RESULT_VIEWS",
    "OPEN_VIEW_RECORD",
    "emit_open_view_request",
    "encode_open_view_request",
]
