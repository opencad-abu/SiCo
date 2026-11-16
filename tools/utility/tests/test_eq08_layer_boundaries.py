"""The closed EQ-08 layer decisions reject direct and deferred regressions."""

from pathlib import Path

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {r['id']: r for r in load_manifest()['rules'] if r['id'].startswith('eq08-')}


@pytest.mark.parametrize('name', RULES)
def test_reviewed_sources_obey_layer_direction(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize('layer,target', [
    ('core', 'sico.storage.journal'), ('core', 'sico.transport.broker_scope'),
    ('core', 'sico.service.python_backend'), ('core', 'sico.providers.config'),
    ('storage', 'sico.service.background_config'), ('storage', 'sico.codex.settings'),
    ('storage', 'sico.transport.framing'), ('codex', 'sico.service.audit_questions'),
    ('codex', 'sico.service.audit_records'), ('codex', 'sico_ui.window'),
])
@pytest.mark.parametrize('deferred', [False, True])
def test_new_module_cannot_reverse_a_closed_layer(layer, target, deferred):
    source = 'from ' + target + ' import value'
    if deferred:
        source = 'def operation():\n    ' + source
    assert violations(source, RULES[f'eq08-{layer}-one-way'], module=f'sico.{layer}.new')


def test_json_consumer_cannot_load_framing_via_a_deferred_import():
    assert violations('def decode():\n    from ..transport.framing import strict_json',
                      RULES['eq08-codex-json-owner'], module='sico.codex.native_history')


@pytest.mark.parametrize('source', [
    'def internal_token(value): pass',
    'def command(value): return tokens.internal_token(value)',
    'def resolve():\n    from .controller import SessionController',
])
def test_worker_controller_adapter_cannot_return(source):
    assert violations(source, RULES['eq08-worker-token-only'], module='sico.service.worker')
