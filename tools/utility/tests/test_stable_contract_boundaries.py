"""C2 contracts must remain usable independently of runtime owners."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row['id']: row for row in load_manifest()['rules']}


@pytest.mark.parametrize('name', [
    'stable-identity-event-result-values', 'shared-events-canonical-consumers',
    'display-values-no-worker-import',
])
def test_contract_sources_and_consumers_follow_owned_boundaries(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize('module', [
    'sico.storage.journal', 'sico.service.events', 'sico.transport.broker',
    'sico.codex.backend', 'sico.providers.openai', 'sico_ui.window', 'PyQt5',
    'cadai.result_tools', 'cadai.skill_result', 'subprocess', 'socket',
])
def test_contract_rules_reject_runtime_imports_inside_functions(module):
    assert violations('def parse():\n    import ' + module,
                      RULES['stable-identity-event-result-values'], module='sico.core.session_events')


@pytest.mark.parametrize('module', ['sico.service.events', 'sico.storage.native_origin'])
def test_retired_event_paths_cannot_return(module):
    assert violations('def parse():\n    from ' + module + ' import EventCursor',
                      RULES['shared-events-canonical-consumers'], module='sico.service.replay')


def test_value_consumers_cannot_load_the_worker_for_a_constant():
    assert violations('from sico.service.event_stream import DISPLAY_CONTRACT',
                      RULES['display-values-no-worker-import'], module='sico_ui.event_batches')


def test_real_contracts_work_without_runtime_imports_or_external_io(tmp_path):
    result = subprocess.run(
        [sys.executable, '-B', '-S', str(Path(__file__).with_name('stable_contracts_probe.py')), str(ROOT)],
        cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
