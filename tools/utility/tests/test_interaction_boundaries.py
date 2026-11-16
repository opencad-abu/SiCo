"""Mutation checks keep the waiting projection and interaction capabilities owned."""

import pytest
from utility.boundary_rules import violations
from utility.check_private_import_boundaries import load_manifest

RULES = {r['id']: r for r in load_manifest()['rules'] if r['id'].startswith('interaction-')}


@pytest.mark.parametrize('source', [
    "task['waiting_audits'] = []",
    "del task['waiting_audits']",
    "task.pop('waiting_audits', None)",
    "task.setdefault('waiting_audits', []).append(key)",
    "task.update(waiting_audits=[])",
    "task.update({'waiting_audits': []})",
    "task.__setitem__('waiting_audits', [])",
    "alias = task\nalias['waiting_audits'] += [key]",
    "drop = task.pop\ndrop('waiting_audits')",
    "task['waiting_audits'].clear()",
    "task['waiting_audits'][0] = key",
])
def test_projection_cannot_be_written_outside_owner(source):
    assert violations(source, RULES['interaction-waiting-projection-owner'])


@pytest.mark.parametrize('source', [
    "task.get('waiting_audits', [])", "snapshot = dict(task)",
    "shown[key] = task[key]", "task.pop('activity', None)",
])
def test_readers_and_unrelated_fields_remain_available(source):
    assert not violations(source, RULES['interaction-waiting-projection-owner'])


@pytest.mark.parametrize('source', [
    "self.loop = loop", "self.backend = backend", "runtime.state.task",
    "read = getattr(audit, 'workbench')\nread.loop",
    "from sico.codex.backend import CodexBackend as Owner",
])
def test_handlers_cannot_regain_whole_backend(source):
    assert violations(source, RULES['interaction-narrow-capabilities'])


@pytest.mark.parametrize('source', [
    "audit.requests._pending.clear()", "owner = backend.inputs\nowner._seen = {}",
    "getattr(self._requests, '_connection')", "inputs._closed.add(key)",
])
def test_private_owner_state_cannot_be_changed_through_alias(source):
    assert violations(source, RULES['interaction-private-owner-state'])
