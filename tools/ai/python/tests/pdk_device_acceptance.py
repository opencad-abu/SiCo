"""Development-only recorder for real device acceptance and v2 evidence compilation.

The operation runner supplies actual per-device reports. This module fixes the
tested package before any operation and reselects it after all operations. It
does not execute tools, infer success, or upgrade historical unbound reports.
"""

import argparse
from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import tempfile

from cadai.pdk_normalize import now
from cadai.pdk_standard.acceptance_run import begin, finish, evidence
from cadai.pdk_standard.jsonio import encode, fail
from cadai.pdk_standard.receipts import LIMIT, OPERATIONS, load


def save(path, value):
    """Durably retain private evidence, including a snapshot before execution."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encode(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_evidence(path, runs):
    value = evidence(runs)
    if len(encode(value)) > LIMIT:
        fail('Acceptance evidence exceeds its bound')
    save(path, value)


@contextmanager
def recording(directory, devices, *, select_package, source_validator):
    """Yield the tested package and raw results; emit evidence only after recheck.

    Each device result uses operation -> {ok: bool, ...raw evidence}. A missing,
    false or non-boolean outcome is failed. Exceptions preserve the raw report
    without creating completed evidence. Every run needs a new directory.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, mode=0o700)
    package = select_package()
    snapshot = begin(package, devices, source_validator)
    path = directory / 'report.json'
    report = {'snapshot': deepcopy(snapshot), 'started_at': now(),
              'devices': {device: {} for device in snapshot['devices']}}
    save(path, report)
    try:
        yield package, report
        if set(report['devices']) != set(snapshot['devices']):
            fail('Acceptance results changed the captured device set')
        results = {device: {operation: 'passed' if
                   isinstance(row.get(operation), dict) and row[operation].get('ok') is True
                   else 'failed' for operation in OPERATIONS}
                   for device, row in report['devices'].items()}
        run = finish(select_package(), snapshot, results, at=now(), run_ref=str(path),
                     evidence_ref=str(path), source_validator=source_validator)
        report['finished_at'] = run['at']
        report['complete'] = [device for device, outcomes in results.items()
                              if all(value == 'passed' for value in outcomes.values())]
        report['incomplete'] = sorted(set(results) - set(report['complete']))
    except BaseException as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        # A caller cannot edit the retained original identity through the report.
        report['snapshot'] = snapshot
        save(path, report)
    write_evidence(directory / 'evidence.json', [run])


def compile_evidence(paths, output):
    """Combine explicitly selected bound runs without reading a current package."""
    runs = [run for path in paths for run in load(path)['runs']]
    write_evidence(output, runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('evidence', type=Path, nargs='+', help='v2 run evidence.json paths')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; retain old evidence and choose a new path')
    compile_evidence(args.evidence, args.output)


if __name__ == '__main__':
    main()
