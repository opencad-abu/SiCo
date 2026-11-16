"""Bash rc helpers avoid vendor loaders while user commands receive exact paths."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cadenv import preserve_virtuoso_environment
from sico.service.terminal_environment import shell_command, shell_environment
from sico.service.terminal_startup import prepare_shell


def captured(tmp_path, **loaders):
    env = dict(PATH='/usr/bin:/bin', HOME=str(tmp_path), SHELL='/bin/bash', **loaders)
    preserve_virtuoso_environment(env)
    env.update(LD_LIBRARY_PATH='/gui/lib', PYTHONPATH='/gui/python', SICO_AI_TOKEN='secret')
    return env


@pytest.mark.parametrize('loaders', [dict(LD_LIBRARY_PATH='/eda/lib', PYTHONPATH='/eda/python'),
                                    dict(LD_LIBRARY_PATH='/eda/lib', PYTHONPATH=''),
                                    dict(PYTHONPATH='/eda/python')])
def test_startup_helpers_are_clean_and_captured_paths_return_before_input(tmp_path, loaders):
    source = captured(tmp_path, **loaders)
    child = tmp_path / 'observe.py'
    child.write_text('import json,os,sys\nprint(json.dumps({k:v for k,v in os.environ.items() '
                     'if k in ("LD_LIBRARY_PATH","PYTHONPATH","MARKER","SICO_AI_TOKEN") '
                     'or k.startswith("SICO_TERMINAL_RESTORE_")}))\n')
    rc = tmp_path / '.bashrc'
    rc.write_text(f'{sys.executable} -B -s {child}\nexport MARKER=loaded\n'
                  'f(){ printf function; }; alias show="printf alias"\n')
    (tmp_path / '.bash_profile').write_text('printf login-only-must-not-run\n')
    original = dict(source)
    argv, target = prepare_shell(source, inherited=source)
    assert source == original and not any(value in argv for value in loaders.values() if value)
    result = subprocess.run(argv, env=dict(source, **target),
        input=f'{sys.executable} -B -s {child}\nf; show\nexit\n', text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert json.loads(lines[0]) == {}
    assert json.loads(lines[1]) == dict(loaders, MARKER='loaded')
    assert lines[2] == 'functionalias'
    assert 'secret' not in result.stdout
    assert 'login-only-must-not-run' not in result.stdout


def test_no_loader_and_explicit_command_keep_existing_launch_semantics(tmp_path):
    source = dict(PATH='/bin', HOME=str(tmp_path), SHELL='/bin/bash')
    assert prepare_shell(source, inherited=source) == (shell_command(source), shell_environment(source))
    source = captured(tmp_path, LD_LIBRARY_PATH='/eda/lib')
    explicit = ['/bin/bash', '--noprofile', '--norc']
    assert prepare_shell(source, command=explicit, inherited=source) == (
        shell_command(source, command=explicit), shell_environment(source))


def test_other_shell_does_not_receive_bash_rc(tmp_path):
    source = captured(tmp_path, LD_LIBRARY_PATH='/eda/lib')
    source['SHELL'] = '/bin/tcsh'
    argv, env = prepare_shell(source, inherited=source)
    assert argv[-1] == '/bin/tcsh' and env['LD_LIBRARY_PATH'] == '/eda/lib'
    assert not any(name.startswith('SICO_TERMINAL_RESTORE_') for name in env)


def test_restoration_namespace_cannot_be_injected(tmp_path):
    source = captured(tmp_path, LD_LIBRARY_PATH='/eda/lib')
    source['SICO_TERMINAL_RESTORE_PYTHONPATH'] = '/unreviewed'
    with pytest.raises(ValueError, match='reserved'):
        prepare_shell(source)
