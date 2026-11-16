"""C3 command owners stay explicit after Controller alias retirement."""

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

RULES = {r['id']: r for r in load_manifest()['rules']}
OWNER = RULES['session-controller-no-command-aliases']
CONSUMERS = RULES['session-controller-canonical-consumers']


@pytest.mark.parametrize('rule', [OWNER, CONSUMERS], ids=['controller', 'consumers'])
def test_current_sources_follow_component_ownership(rule):
    assert not check_rule(rule)


@pytest.mark.parametrize('source', [
    'setattr(self, name, getattr(component, name))',
    'self.answer_audit = self.backend_commands.answer_audit',
    'def read_updates(self):\n    return self.updates.read_updates()',
    'def __getattr__(self, name):\n    return getattr(self.bindings, name)',
])
def test_controller_cannot_restore_forwarding_aliases(source):
    assert violations(source, OWNER)


@pytest.mark.parametrize('source', [
    'controller.attach_targets(bridge)',
    'def read():\n    return controller.read_updates()',
    "getattr(controller, 'steer')(text)",
    'alias = controller\ncommand = alias.answer_audit\ncommand(reply)',
    'owner._state_snapshot(sequence)',
    'destination.attach_targets(bridge)',
])
def test_consumers_cannot_retrieve_retired_commands(source):
    assert violations(source, CONSUMERS)


@pytest.mark.parametrize('source', [
    'controller.bindings.attach_targets(bridge)',
    'controller.backend_commands.answer_audit(reply)',
    'controller.updates.read_updates()',
    'controller.thread_commands.thread_status()',
    'controller.reconciliation.query_interrupted(call)',
    'controller.router.router_receipt()',
    'controller.submit(text)',
])
def test_component_commands_and_controller_lifecycle_remain_available(source):
    assert not violations(source, CONSUMERS)
