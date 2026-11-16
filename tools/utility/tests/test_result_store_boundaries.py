"""Result archive reads cannot load application tool implementations."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row['id']: row for row in load_manifest()['rules']}


@pytest.mark.parametrize('name', ['result-store-no-application-runtime', 'workbench-no-result-tool-runtime'])
def test_result_sources_obey_boundaries(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize('source', [
    'def read():\n    from cadai.result_tools import ResultStore',
    'from cadai.result_store import ResultStore as Writer\nclass Reader(Writer): pass',
    'import cadai.result_store as store\ndef read():\n    return store.ResultStore("/tmp")',
])
def test_workbench_cannot_reintroduce_tool_or_writer_dependency(source):
    assert violations(source, RULES['workbench-no-result-tool-runtime'], module='sico.storage.workbench')


@pytest.mark.parametrize('module', ['cadai.result_tools', 'cadai.skill_result', 'cadai.circuit_schema',
                                    'sico.service', 'sico.codex', 'subprocess', 'socket'])
def test_store_cannot_import_application_runtime_even_lazily(module):
    assert violations('def read():\n    import ' + module,
                      RULES['result-store-no-application-runtime'], module='cadai.result_store')


def test_material_reads_without_tools_runtime_environment_or_writes(tmp_path):
    probe = Path(__file__).with_name('result_store_probe.py')
    result = subprocess.run([sys.executable, '-B', '-s', str(probe), str(ROOT)], cwd=tmp_path,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
