"""Content-addressed result artifacts: publication and explicit read-only access."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from sicostate import absolute, project_directory, validate_directory
from .result_contract import SCHEMA, REPORT_SCHEMA, ResultError, canonical, digest

MAX_BYTES = 8 * 1024 * 1024
ARTIFACT_SCHEMAS = {'results': SCHEMA, 'report': REPORT_SCHEMA,
                    'waveform': 'cad.maestro.waveform.v1',
                    'wave_measurements': 'cad.waveform.measurements.v1',
                    'pair_measurements': 'cad.waveform.pair-measurements.v1',
                    'wave_specs': 'cad.waveform.spec-report.v1',
                    'ac_waveform': 'cad.maestro.ac-waveform.v1',
                    'ac_measurements': 'cad.ac.measurements.v1',
                    'ac_response': 'cad.ac.response-report.v1'}


def _reference(ref):
    match = re.fullmatch(r'(' + '|'.join(ARTIFACT_SCHEMAS) + r')_([0-9a-f]{64})', ref)
    if not match: raise ResultError('invalid result reference')
    return match


def _read(path, match):
    if path.is_symlink() or not path.is_file(): raise ResultError('result artifact missing or invalid')
    if path.stat().st_size > MAX_BYTES: raise ResultError('result artifact exceeds limit')
    try:
        value = json.loads(path.read_bytes())
        if digest(value) != match[2]: raise ResultError('result artifact digest changed')
        schema = ARTIFACT_SCHEMAS[match[1]]
        if not isinstance(value, dict) or value.get('schema') != schema:
            raise ResultError('unsupported artifact schema')
    except (ValueError, TypeError, OverflowError) as exc:
        raise ResultError('invalid result artifact') from exc
    return value


def read_archived_result(state_root, ref):
    """Read from the caller's already selected state root, without creating state.

    History/migration callers own path admission. This reader never selects a
    workspace from the environment, remaps history or exposes a write method.
    """
    if not absolute(state_root).is_dir():
        raise ResultError('result workspace is unavailable')
    match = _reference(ref)
    directory = Path(state_root)
    for name in ('ai', 'measurement-results'):
        directory = directory / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Result archive directory is unavailable')
    return _read(directory / (ref + '.json'), match)


class ResultStore:
    """Only server-generated, content-addressed artifacts in this workspace."""
    def __init__(self, workspace):
        if workspace is None: raise ResultError('result tools require an explicit workspace')
        self.workspace = absolute(workspace)
        if not self.workspace.is_dir(): raise ResultError('result workspace is unavailable')

    def directory(self, *, create=True):
        try:
            path = project_directory(self.workspace, 'ai/measurement-results', create=create)
            if path.parent.is_symlink() or not path.parent.is_dir():
                raise ValueError('invalid result ancestor')
            validate_directory(path)
        except (OSError, ValueError) as exc:
            raise ResultError('result state root is unavailable') from exc
        return path

    def put(self, prefix, value):
        if prefix not in ARTIFACT_SCHEMAS or value.get('schema') != ARTIFACT_SCHEMAS[prefix]:
            raise ResultError('unsupported artifact schema/prefix')
        data = canonical(value)
        if len(data) > MAX_BYTES: raise ResultError('result artifact exceeds 8 MiB')
        ref = prefix + '_' + digest(value)
        directory = self.directory(create=True); path = directory / (ref + '.json')
        fd, temporary = tempfile.mkstemp(prefix='.publish-', dir=directory)
        try:
            with os.fdopen(fd, 'wb') as f: f.write(data)
            try: os.link(temporary, path)
            except FileExistsError:
                self.get(ref)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return ref, {'path': str(path), 'sha256': digest(value), 'bytes': len(data)}

    def get(self, ref):
        match = _reference(ref)
        return _read(self.directory(create=False) / (ref + '.json'), match)
