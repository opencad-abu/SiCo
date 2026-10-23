"""Validate a session-owned attachment address without opening the attachment."""

from pathlib import Path
import re


def validate(path, sha256, session_id):
    if not isinstance(path, str) or not isinstance(sha256, str):
        raise ValueError('Invalid artifact reference')
    relative = Path(path)
    if (relative.parent != Path('attachments') / session_id
            or not re.fullmatch(r'[a-f0-9]{32}\.(?:json|txt)', relative.name)
            or not re.fullmatch(r'[a-f0-9]{64}', sha256)):
        raise ValueError('Read only attachments/' + session_id
                         + '/<32 lowercase hex>.json or .txt with its 64-hex sha256; '
                         'measurement results use query_maestro_results')
    return relative
