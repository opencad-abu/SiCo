"""Source entry failures, isolation and mixed collection must remain observable."""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.source_tests import ROOT, configuration, environment, run, selection
from utility.tests.gate_fixtures import commit, write


def test_environment_discards_site_secrets_optins_and_foreign_roots(monkeypatch):
    for key in ('SICO_API_KEY', 'OPENAI_API_KEY', 'CAD_RUN_SKILL_SOURCE_PROBES',
                'PYTHONPATH', 'PYTEST_ADDOPTS', 'CAD_PYTHON', 'SICO_HOME'):
        monkeypatch.setenv(key, 'must-not-propagate')
    _, paths = configuration(ROOT)
    env = environment(ROOT, paths, group={'kind': 'ordinary'})
    for key in ('SICO_API_KEY', 'OPENAI_API_KEY', 'CAD_RUN_SKILL_SOURCE_PROBES',
                'PYTEST_ADDOPTS', 'CAD_PYTHON'):
        assert key not in env
    assert 'must-not-propagate' not in env.values()
    assert env['SICO_HOME'] == str(ROOT)
    assert env['PYTHONPATH'].split(os.pathsep)[0] == str(ROOT / 'tools')
    assert env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'


def test_eda_cannot_run_from_inherited_optin_and_runtime_needs_explicit_binary(monkeypatch):
    spec, paths = configuration(ROOT)
    monkeypatch.setenv('CAD_RUN_SKILL_SOURCE_PROBES', '1')
    with pytest.raises(ValueError, match='--allow-eda'):
        selection(ROOT, spec, ['eda'], False)
    monkeypatch.setenv('CAD_TEST_COPILOT_CODEX_CLI', '/ambient/codex')
    with pytest.raises(ValueError, match='--codex'):
        environment(ROOT, paths, group={'kind': 'runtime'})


def test_circuit_eda_probe_cannot_start_with_only_an_ambient_executable(tmp_path):
    sentinel = tmp_path / 'called'
    executable = tmp_path / 'dbAccess'
    executable.write_text('#!/bin/sh\ntouch "' + str(sentinel) + '"\nexit 1\n')
    executable.chmod(0o700)
    spec, paths = configuration(ROOT)
    path = 'tools/ai/python/tests/test_agent_circuit_errors_native.py'
    assert path in spec['groups']['eda']['paths']
    assert all(path not in group['paths'] for group in spec['groups'].values()
               if group['kind'] != 'eda')
    env = environment(ROOT, paths, group={'kind': 'ordinary'})
    env['PATH'] = str(tmp_path) + os.pathsep + env.get('PATH', '')
    result = subprocess.run([sys.executable, '-B', '-s', '-m', 'pytest', '-q',
                             '-p', 'no:cacheprovider', path], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and '1 skipped' in result.stdout
    assert not sentinel.exists()


@pytest.mark.parametrize('order', ['utility-first', 'ai-first'])
def test_test_support_is_independent_of_conftest_collection_order(tmp_path, order):
    paths = ['tools/utility/tests/test_a_philosophy_compat.py',
             'tools/ai/python/tests/test_agent_storage.py', 'deploy/tests/test_python_native_scope.py']
    if order == 'ai-first':
        paths.reverse()
    _, sources = configuration(ROOT)
    env = environment(ROOT, sources, group={'kind': 'ordinary'})
    result = subprocess.run([sys.executable, '-B', '-s', '-m', 'pytest', '--collect-only',
                             '-q', '-p', 'no:cacheprovider', '--rootdir=' + str(ROOT), *paths],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'tests collected' in result.stdout


@pytest.fixture
def source_repo(repo):
    write(repo, 'src/probe.py', 'def value(): return 7\n')
    write(repo, 'paths.json', json.dumps({'search_paths': ['src']}))
    write(repo, 'tools/utility/source_test_report.py',
          (ROOT / 'tools/utility/source_test_report.py').read_text())
    group = dict(kind='ordinary', purpose='Isolated behavior probe', paths=['test_probe.py'])
    write(repo, 'tools/utility/source_tests.json', json.dumps(dict(
        schema_version=1, source_paths='paths.json', not_covered={'formal_runtime': 'not qualified'},
        default_groups=['probe'], groups={'probe': group})))
    return repo


@pytest.mark.parametrize('body,status,exitstatus', [
    ('assert probe.value() == 7', 'passed', 0),
    ('assert probe.value() == 8', 'failed', 1),
    ('pytest.skip("explicit unavailable dependency")', 'failed', 0),
])
def test_entry_reports_real_outcomes_and_source_imports(source_repo, tmp_path, body, status, exitstatus):
    root = source_repo
    write(root, 'test_probe.py', 'import pytest\nimport probe\ndef test_value():\n    ' + body + '\n')
    commit(root, '.')
    report = run(root, ['probe'], tmp_path / '.cad/reports')
    group = report['groups']['probe']
    assert report['status'] == status and group['returncode'] == exitstatus
    assert report['formal_runtime_qualified'] is False
    loaded = json.loads(Path(group['imports']['path']).read_text())
    assert any(m['module'] == 'probe' and m['path'] == 'src/probe.py' for m in loaded['modules'])
    if 'skip' in body:
        assert group['counts']['skipped'] == 1 and not report['passed']
        assert next(iter(group['cases'].values()))['reason'] == 'explicit unavailable dependency'


def test_failing_collection_cannot_reuse_a_previous_success_receipt(source_repo, tmp_path):
    root = source_repo
    write(root, 'test_probe.py', 'import module_that_does_not_exist\n')
    commit(root, '.')
    output = tmp_path / '.cad/reports'
    output.mkdir(parents=True)
    (output / 'summary.json').write_text('{"passed":true}')
    report = run(root, ['probe'], output)
    assert report['status'] == 'failed' and not report['passed']
    assert report['groups']['probe']['returncode'] == 2
    assert json.loads((output / 'summary.json').read_text()) == report


def test_source_change_during_run_is_rejected(source_repo, tmp_path):
    write(source_repo, 'test_probe.py',
          'from pathlib import Path\ndef test_mutation():\n'
          '    Path("src/probe.py").write_text("changed = True\\n")\n')
    commit(source_repo, '.')
    report = run(source_repo, ['probe'], tmp_path / '.cad/reports')
    assert report['status'] == 'failed'
    assert 'Source/configuration changed during tests' in report['errors']


def test_failed_preflight_replaces_receipt(monkeypatch, tmp_path):
    from utility import source_tests
    monkeypatch.setattr(source_tests, 'ROOT', ROOT)
    (tmp_path / 'summary.json').write_text('{"passed":true}')
    assert source_tests.main(['--group', 'unknown', '--output', str(tmp_path)]) == 2
    assert json.loads((tmp_path / 'summary.json').read_text())['status'] == 'error'


def test_registered_groups_use_existing_inputs_and_do_not_hide_optin_suites():
    spec, _ = configuration(ROOT)
    selection(ROOT, spec, list(spec['groups']), True)
    assert spec['default_groups'] == ['governance', 'contracts', 'gui']
    expected = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'tools/utility/tests').glob('test_*.py')}
    declared = {p for group in spec['groups'].values() for p in group['paths'] if p.startswith('tools/utility/tests/')}
    assert declared == expected
    for p in (ROOT / 'tools/utility/tests').glob('*.py'):
        assert not any(isinstance(n, ast.ImportFrom) and n.module == 'conftest'
                       for n in ast.walk(ast.parse(p.read_text())))


def test_partial_skips_remain_visible_without_claiming_strict_success(source_repo, tmp_path):
    write(source_repo, 'test_probe.py', 'import pytest\ndef test_ok(): pass\n'
          'def test_absent(): pytest.skip("needs original target runtime")\n')
    commit(source_repo, '.')
    report = run(source_repo, ['probe'], tmp_path / '.cad/reports')
    assert report['status'] == 'passed_with_skips' and not report['passed']
    assert report['groups']['probe']['counts'] == dict(passed=1, failure=0, error=0, skipped=1)


def test_foreign_project_import_cannot_qualify_source_tests(source_repo, tmp_path):
    foreign = tmp_path.parent / (tmp_path.name + '-foreign')
    write(foreign, 'probe.py', 'def value(): return 7\n')
    write(source_repo, 'test_probe.py',
          f'import sys\nsys.path.insert(0, {str(foreign)!r})\nimport probe\n'
          'def test_value(): assert probe.value() == 7\n')
    commit(source_repo, '.')
    report = run(source_repo, ['probe'], tmp_path / '.cad/reports')
    assert report['status'] == 'failed'
    assert any('outside source root: probe' in error
               for error in report['groups']['probe']['errors'])


def test_interruption_cannot_leave_previous_success_receipt(source_repo, tmp_path, monkeypatch):
    from utility import source_tests
    write(source_repo, 'test_probe.py', 'def test_ok(): pass\n')
    commit(source_repo, '.')
    output = tmp_path / '.cad/reports'
    output.mkdir(parents=True)
    (output / 'summary.json').write_text('{"passed":true}')
    original = source_tests.subprocess.run

    def interrupted(command, *args, **kwargs):
        if 'pytest' in command:
            raise KeyboardInterrupt()
        return original(command, *args, **kwargs)

    monkeypatch.setattr(source_tests.subprocess, 'run', interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(source_repo, ['probe'], output)
    receipt = json.loads((output / 'summary.json').read_text())
    assert receipt['status'] == 'running' and not receipt['passed']
