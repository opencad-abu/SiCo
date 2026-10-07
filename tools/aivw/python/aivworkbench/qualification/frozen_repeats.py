"""Exclusive two-attempt execution journal for controller-owned frozen inputs.

This is a integrity/restart primitive, not an OS isolation or access authority.
An unfinished attempt is never retried automatically. The caller owns tool
execution, artifact authentication and private-data classification.
"""

import hashlib
import json
import os
from pathlib import Path
import re

from ..workspace import sha256_file, stable_digest


def _write(path, value):
    data = (json.dumps(value, sort_keys=True, allow_nan=False)+"\n").encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _directory(root):
    root = Path(root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('journal requires an absolute non-symlink directory')
    return root


def freeze_inputs(root, files, bindings):
    """Copy exact input bytes into a new exclusive journal; never re-select."""
    root = _directory(root)
    if not files or len(files) > 128 or not bindings:
        raise ValueError('frozen input files and bindings required')
    encoded = json.loads(json.dumps(bindings, allow_nan=False))
    copies = {}
    for name, path in files.items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name):
            raise ValueError('invalid frozen input filename')
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64*1024*1024:
            raise ValueError('invalid frozen input file')
        copies[name] = path.read_bytes()
    root.mkdir(mode=0o700)
    (root/'inputs').mkdir(mode=0o700)
    for name, content in copies.items():
        with (root/'inputs'/name).open('xb') as stream:
            stream.write(content)
    record = {'schema_version': 1, 'kind': 'frozen-two-repeat-inputs', 'repeat_count': 2,
              'files': {n: hashlib.sha256(b).hexdigest() for n, b in copies.items()},
              'bindings': encoded}
    record['freeze_digest'] = stable_digest(record)
    _write(root/'freeze.json', record)
    return record


def verify_freeze(root, expected_digest):
    root = _directory(root)
    path = root/'freeze.json'
    if path.is_symlink():
        raise ValueError('freeze record is a symlink')
    record = json.loads(path.read_text())
    body = dict(record)
    digest = body.pop('freeze_digest')
    if digest != expected_digest or stable_digest(body) != digest:
        raise ValueError('frozen record identity differs')
    if record['repeat_count'] != 2 or (root/'inputs').is_symlink():
        raise ValueError('frozen input boundary differs')
    for name, wanted in record['files'].items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name):
            raise ValueError('invalid frozen filename')
        path = root/'inputs'/name
        if path.is_symlink() or sha256_file(path) != wanted:
            raise ValueError('frozen input changed')
    return record


def begin_attempt(root, expected_digest, number, run_id):
    root = _directory(root)
    verify_freeze(root, expected_digest)
    if type(number) is not int or number not in (1, 2) or not isinstance(run_id, str) or not run_id:
        raise ValueError('exactly two named attempts are supported')
    if number == 2:
        first = _read_result(root, expected_digest, 1)
        if first['status'] != 'EXECUTED' or first['run_id'] == run_id:
            raise ValueError('second attempt needs a successful independent first execution')
    local = root/('attempt_%d' % number)
    # A crash before a result exists leaves this exclusive directory in place.
    # Reentry cannot claim an unobserved execution is safe to run again.
    local.mkdir(mode=0o700)
    _write(local/'started.json', {'freeze_digest': expected_digest, 'number': number, 'run_id': run_id})
    return local


def finish_attempt(root, expected_digest, number, *, status, measurements):
    root = _directory(root)
    verify_freeze(root, expected_digest)
    if type(number) is not int or number not in (1, 2):
        raise ValueError('invalid repeat number')
    local = root/('attempt_%d' % number)
    if local.is_symlink() or (local/'started.json').is_symlink():
        raise ValueError('attempt path is a symlink')
    started = json.loads((local/'started.json').read_text())
    if started['freeze_digest'] != expected_digest or started['number'] != number:
        raise ValueError('attempt binding differs')
    if status not in {'EXECUTED', 'FAILED', 'UNKNOWN_SIDE_EFFECT'} or not measurements:
        raise ValueError('execution outcome required')
    result = {**started, 'status': status, 'measurements': json.loads(json.dumps(measurements, allow_nan=False))}
    result['result_digest'] = stable_digest(result)
    _write(local/'result.json', result)
    return result


def _read_result(root, digest, number):
    local = root/('attempt_%d' % number)
    if any(p.is_symlink() for p in (local, local/'result.json', local/'started.json')):
        raise ValueError('attempt record is a symlink')
    try:
        result = json.loads((local/'result.json').read_text())
    except FileNotFoundError as exc:
        raise ValueError('unfinished attempt; execution state is unknown') from exc
    body = dict(result)
    wanted = body.pop('result_digest')
    start = json.loads((local/'started.json').read_text())
    if (stable_digest(body) != wanted or result['freeze_digest'] != digest or
            result['number'] != number or start != {k: result[k] for k in ('number', 'run_id', 'freeze_digest')}):
        raise ValueError('repeat result binding differs')
    return result


def compare_attempts(root, expected_digest):
    root = _directory(root)
    verify_freeze(root, expected_digest)
    first, second = [_read_result(root, expected_digest, i) for i in (1, 2)]
    if first['run_id'] == second['run_id']:
        raise ValueError('repeat run IDs must differ')
    if any(r['status'] != 'EXECUTED' for r in (first, second)):
        status = 'BLOCKED_REPEAT'
    else:
        status = 'PASS_REPEAT' if first['measurements'] == second['measurements'] else 'FAIL_REPEAT'
    return {'status': status, 'freeze_digest': expected_digest, 'repeat_count': 2,
            'run_ids': [r['run_id'] for r in (first, second)],
            'result_digests': [r['result_digest'] for r in (first, second)],
            'qualification': 'NOT_ESTABLISHED'}
