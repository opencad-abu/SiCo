"""No foreign PID, reused identity or forged cwd can become an owned observation."""

import os
from pathlib import Path

import pytest

from sico.process_identity import observe_descendant


def process(root, pid, parent, start, *, children='', namespace='pid:[42]', ns_pid=3):
    path = root / str(pid)
    (path / 'task' / str(pid)).mkdir(parents=True)
    (path / 'task' / str(pid) / 'children').write_text(children)
    (path / 'stat').write_text(f'{pid} (name (with) spaces) ' + ' '.join(
        ['S', str(parent), str(pid)] + ['0'] * 16 + [str(start)]))
    (path / 'status').write_text(f'NSpid:\t{pid}\t{ns_pid}\n')
    (path / 'ns').mkdir()
    (path / 'ns/pid').symlink_to(namespace)
    (path / 'exe').symlink_to(os.path.realpath('/bin/bash'))
    (path / 'cwd').symlink_to('/verified/work')
    return path


@pytest.fixture
def tree(tmp_path):
    process(tmp_path, 10, 1, 100, children='20')
    process(tmp_path, 20, 10, 200, children='30', ns_pid=2)
    shell = process(tmp_path, 30, 20, 300)
    return tmp_path, shell


def observe(root, **kw):
    return observe_descendant(10, kw.pop('owner_start', 100), 3, 'pid:[42]', '/bin/bash', proc_root=root)


def test_ancestry_and_namespace_resolve_host_pid_without_trusting_shell_cwd(tree):
    root, _ = tree
    result = observe(root)
    assert (result.pid, result.start, result.parent_pid, result.cwd) == (30, 300, 20, '/verified/work')


@pytest.mark.parametrize('change', ['foreign', 'owner-reused', 'ambiguous', 'namespace', 'executable', 'deleted-cwd'])
def test_absent_ambiguous_or_changed_evidence_cannot_qualify(tree, change):
    root, shell = tree
    if change == 'foreign':
        fields = (shell / 'stat').read_text().replace(' S 20 ', ' S 99 ')
        (shell / 'stat').write_text(fields)
        process(root, 99, 1, 900)
    elif change == 'owner-reused':
        with pytest.raises(ValueError):
            observe(root, owner_start=99)
        return
    elif change == 'ambiguous':
        process(root, 31, 20, 310)
        (root / '20/task/20/children').write_text('30 31')
    else:
        target, value = {'namespace': ('ns/pid', 'pid:[43]'), 'executable': ('exe', '/other'),
                         'deleted-cwd': ('cwd', '/removed (deleted)')}[change]
        (shell / target).unlink()
        (shell / target).symlink_to(value)
    with pytest.raises(ValueError):
        observe(root)


def test_reuse_between_candidate_scan_and_final_verification_is_rejected(tree, monkeypatch):
    root, shell = tree
    original = Path.read_text
    calls = []
    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == shell / 'stat':
            calls.append(True)
            if len(calls) >= 4:
                return value.replace(' 300', ' 301')
        return value
    monkeypatch.setattr(Path, 'read_text', changed)
    with pytest.raises(ValueError, match='changed'):
        observe(root)


def test_unreadable_kernel_evidence_propagates_instead_of_becoming_retirement(tree, monkeypatch):
    root, shell = tree
    original = os.readlink
    def denied(path):
        if path == shell / 'cwd':
            raise PermissionError('identity unavailable')
        return original(path)
    monkeypatch.setattr(os, 'readlink', denied)
    with pytest.raises(PermissionError):
        observe(root)
