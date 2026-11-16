"""Validate session-owned attachment and exported-artifact addresses."""

import os
import re
from pathlib import Path

_ATTACHMENT_NAME = re.compile(r'[a-f0-9]{32}\.(?:json|txt)')
_EXPORT_NAME = re.compile(r'schematic-export-[a-f0-9]{24}\.txt')
_SHA256 = re.compile(r'[a-f0-9]{64}')
_HELP = (
    'Read attachments/<session>/<32 lowercase hex>.json or .txt, or '
    'assistant-spool/schematic-export-<24 lowercase hex>.txt, with its '
    '64-hex sha256; use the artifact.path returned by a tool result; '
    'copilot://data links are not files; tool-data keys such as '
    'd_<sequence>_tool are UI references, not files; measurement results '
    'use query_maestro_results'
)


def validate(path, sha256, session_id):
    if not isinstance(path, str) or not isinstance(sha256, str):
        raise ValueError('Invalid artifact reference')
    relative = Path(path)
    if (relative.parent != Path('attachments') / session_id
            or not _ATTACHMENT_NAME.fullmatch(relative.name)
            or not _SHA256.fullmatch(sha256)):
        raise ValueError(_HELP)
    return relative


def validate_session_export(path, sha256, session_directory):
    """Return one absolute exported schematic text path for the current session.

    Snapshot exports are written to the session-owned ``assistant-spool``.
    They are deliberately narrower than arbitrary spool files: only the
    checksummed schematic text export may be paged through ``read_artifact``.
    """
    if not isinstance(path, str) or not isinstance(sha256, str):
        raise ValueError('Invalid artifact reference')
    candidate = Path(path)
    session = Path(session_directory).absolute()
    spool = session / 'assistant-spool'
    if not candidate.is_absolute():
        base = session if candidate.parts[:1] == ('assistant-spool',) else spool
        candidate = base / candidate
    try:
        same_directory = os.path.samefile(candidate.parent, spool)
    except (OSError, ValueError):
        same_directory = False
    if (not same_directory or not _EXPORT_NAME.fullmatch(candidate.name)
            or not _SHA256.fullmatch(sha256)):
        raise ValueError(_HELP)
    return candidate


__all__ = ['validate', 'validate_session_export']
