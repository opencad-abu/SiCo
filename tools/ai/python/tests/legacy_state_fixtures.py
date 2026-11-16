"""Construct pre-v0.0.2 records only; never relax admission during migration tests.

The record encoders have not changed. These scoped fixture constructors use their
original .cad location so byte-preservation and absolute-path tests stay meaningful.
The production writer prohibition is restored before audit/prepare/activate runs.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from unittest.mock import patch

import sicostate
from sico.storage import roots
from sico.storage.journal import SessionJournal as CurrentJournal


@contextmanager
def legacy_writer():
    current = sicostate.project_root

    def historical_root(launch, *, create=False):
        root = Path(launch).absolute() / ".cad"
        if root.is_dir() and not (root.parent / ".sico").exists():
            return root
        return current(launch, create=create)

    with patch.object(sicostate, "project_root", historical_root), \
            patch.object(roots, "project_root", historical_root):
        yield


def legacy_fixture(function):
    @wraps(function)
    def construct(*args, **kwargs):
        with legacy_writer():
            return function(*args, **kwargs)
    return construct


class HistoricalJournal(CurrentJournal):
    def __init__(self, *args, **kwargs):
        with legacy_writer():
            super().__init__(*args, **kwargs)


def save_release(directory, address, operation_id, sequence):
    """Encode the pre-v0.0.2 single-runtime receipt; current writers use addressed files."""
    from sico.storage.project_files import write_record
    from sico.storage.session_release import NAME

    write_record(directory / NAME, dict(version=1, address=address.record(),
                 operation_id=operation_id, sequence=sequence))
