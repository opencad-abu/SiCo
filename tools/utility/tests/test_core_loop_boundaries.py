"""Core execution cannot acquire application dependencies, including deferred imports."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULE = next(row for row in load_manifest()['rules'] if row['id'] == 'core-loop-no-service-runtime')


def test_core_composition_sources_obey_boundary():
    assert not check_rule(RULE, root=ROOT)


@pytest.mark.parametrize('module', [
    'sico.service.python_backend', 'sico.storage.journal', 'sico.transport.broker_scope',
    'sico.codex.backend', 'sico.providers.openai', 'cadai.process_monitor',
    'cadai.pdk_preparation', 'cadai.circuit_guidance', 'PyQt5', 'subprocess',
])
def test_core_rejects_deferred_runtime_imports(module):
    assert violations('def run():\n    import ' + module, RULE, module='sico.core.loop')


@pytest.mark.parametrize('mode', ['ok', 'cancel', 'disconnect', 'bad_receipt'])
def test_real_core_runs_without_storage_transport_service_or_eda(tmp_path, mode):
    probe = Path(__file__).with_name('core_loop_probe.py')
    result = subprocess.run([sys.executable, '-B', '-S', str(probe), str(ROOT), mode],
                            cwd=tmp_path, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
