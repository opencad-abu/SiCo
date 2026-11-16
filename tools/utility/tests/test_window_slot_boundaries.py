"""Window command owners stay explicit after dynamic slot retirement."""

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

RULES = {rule['id']: rule for rule in load_manifest()['rules']}
WINDOW = RULES['assistant-window-no-command-aliases']
CONSUMERS = RULES['assistant-window-canonical-consumers']
ADAPTERS = RULES['assistant-window-retired-adapter-commands']


@pytest.mark.parametrize('rule', [WINDOW, CONSUMERS, ADAPTERS],
                         ids=['window', 'consumers', 'adapters'])
def test_current_sources_use_command_owners(rule):
    assert not check_rule(rule)


@pytest.mark.parametrize('source', [
    'setattr(window_type, name, callback)',
    'self.submit = self.actions.submit',
    'def submit(self):\n    return self.actions.submit()',
    'def __getattr__(self, name):\n    return getattr(self.renderer, name)',
    'def connect_commands(window_type):\n    return window_type',
])
def test_window_cannot_restore_callable_aliases(source):
    assert violations(source, WINDOW)


@pytest.mark.parametrize('source', [
    'window.submit()',
    'self.window.activate_session(session_id)',
    "getattr(window, 'update_host_notices')(state)",
    'alias = window\ncommand = alias.run_command\ncommand(name)',
    'window.display.anchorClicked.connect(window.open_object_link)',
    'lambda: window.resume_queue()',
])
def test_consumers_cannot_use_retired_slots(source):
    assert violations(source, CONSUMERS)


@pytest.mark.parametrize('source', [
    'window.actions.submit()',
    'window.renderer.receive(event)',
    'window.details.scroll_to_bottom()',
    'window.page.activate_session(session_id)',
    'window.router_actions.query_router_receipt()',
    'window.lifecycle.request()',
    'lambda: window.renderer.update_session(state)',
])
def test_explicit_component_calls_remain_available(source):
    assert not violations(source, CONSUMERS)


@pytest.mark.parametrize('name', ['show_latest', 'begin_close', 'close_grace_expired'])
def test_adapter_forwarders_cannot_return(name):
    assert violations(f'def {name}(self):\n    pass', ADAPTERS)
