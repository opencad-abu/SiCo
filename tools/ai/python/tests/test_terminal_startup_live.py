"""Opt-in real QTermWidget startup with isolated rc and selected site loaders."""

import json
import os
from pathlib import Path
import shlex
import time

import pytest
from PyQt5.QtWidgets import QApplication

from sico.service.terminal_environment import shell_environment
from sico_ui.terminal_tool import SiTerminalWindow, load_qtermwidget


def wait_until(application, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Real terminal did not reach the expected boundary')


def test_real_binding_initializes_system_tools_then_restores_site_loaders(tmp_path):
    runtime = os.environ.get('SICO_TEST_QTERMWIDGET_RUNTIME')
    selected = os.environ.get('SICO_TEST_TERMINAL_LOADERS_JSON')
    if not runtime or not selected:
        pytest.skip('Explicit real QTermWidget runtime and selected loader input required')
    source = json.loads(Path(selected).read_text())
    allowed = {'SHELL', 'SICO_VIRTUOSO_LD_LIBRARY_PATH', 'SICO_VIRTUOSO_LD_LIBRARY_PATH_SET',
               'SICO_VIRTUOSO_PYTHONPATH', 'SICO_VIRTUOSO_PYTHONPATH_SET'}
    assert set(source) <= allowed, 'Only selected loader provenance may enter this probe'
    source.update(PATH='/usr/bin:/bin', HOME=str(tmp_path), LANG='C.UTF-8',
                  SHELL='/bin/bash', SICO_AI_TOKEN='synthetic-terminal-token')
    expected = shell_environment(source)
    assert expected.get('LD_LIBRARY_PATH') or expected.get('PYTHONPATH')
    (tmp_path / '.bash_profile').write_text('printf LOGIN_ONLY_MUST_NOT_RUN\n')
    (tmp_path / '.bashrc').write_text(
        'stty -echo\n'
        '/usr/bin/python3 -B -s -c \'print("SYSTEM_PYTHON_OK")\'\n'
        '/usr/bin/flatpak --installations >/dev/null && printf "SYSTEM_FLATPAK_OK\\n"\n'
        'PS1="SICO_INPUT_READY> "\n')
    application = QApplication.instance() or QApplication([])
    widget = load_qtermwidget(Path(runtime))
    window = SiTerminalWindow(widget, workspace=tmp_path, environment=source)
    seen, ended = [], []
    window.terminal.receivedData.connect(seen.append)
    window.terminal.finished.connect(lambda: ended.append(True))
    output = tmp_path / 'terminal-output.log'
    try:
        wait_until(application, lambda: 'SICO_INPUT_READY> ' in ''.join(seen))
        pid = window.terminal.getShellPID()
        assert pid > 0 and Path('/proc', str(pid)).exists()
        initial = ''.join(seen)
        assert 'SYSTEM_PYTHON_OK' in initial and 'SYSTEM_FLATPAK_OK' in initial
        assert 'encodings' not in initial and 'XZ_5.2' not in initial
        assert 'LOGIN_ONLY_MUST_NOT_RUN' not in initial
        values = tmp_path / 'selected-environment.bin'
        # Bash builtin avoids executing a system interpreter under EDA loaders.
        fields = ['"${%s+x}" "${%s-}"' % (name, name)
                  for name in ('LD_LIBRARY_PATH', 'PYTHONPATH', 'SICO_AI_TOKEN',
                               'SICO_TERMINAL_RESTORE_LD_LIBRARY_PATH',
                               'SICO_TERMINAL_RESTORE_PYTHONPATH')]
        window.terminal.sendText("builtin printf '%s\\0' " + ' '.join(fields) +
                                 ' >' + shlex.quote(str(values)) +
                                 '; printf "SICO_COMMAND_OK\\n"\n')
        wait_until(application, lambda: 'SICO_COMMAND_OK' in ''.join(seen))
        wanted = []
        for name in ('LD_LIBRARY_PATH', 'PYTHONPATH'):
            wanted.extend((b'x' if name in expected else b'', expected.get(name, '').encode()))
        wanted.extend([b''] * 6)
        assert values.read_bytes().split(b'\0') == [*wanted, b'']
        assert window.terminal.getShellPID() == pid
        window.terminal.sendText('exit\n')
        wait_until(application, lambda: bool(ended))
        wait_until(application, lambda: not Path('/proc', str(pid)).exists())
    finally:
        output.write_text(''.join(seen))
        window.close()
