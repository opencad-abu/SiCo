"""Exercise no-command startup with the real event loop and headless project service."""

import json
import os
import subprocess
import textwrap
from pathlib import Path

from agent_service_launch_fixtures import stop_service
from sico.service.service_discovery import discover_project

PYTHON = '/software/pkgs/python/3.9.13/bin/python3'


def test_no_command_opens_home_without_model_or_ciw_wait(tmp_path):
    script = textwrap.dedent('''
        import sys
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication
        from sico.cli import main
        from sico_ui.home_window import HomeWindow
        from sico_ui.startup import StartupDialog
        app = QApplication([])
        seen = []
        ticks = [0]
        def tick():
            ticks[0] += 1
            windows = app.topLevelWidgets()
            assert not any(isinstance(w, StartupDialog) and w.isVisible() for w in windows)
            home = next((w for w in windows if isinstance(w, HomeWindow)), None)
            if home is not None and home.api is not None:
                assert home.isVisible()
                assert not home.api.opened_sessions()
                assert home.platform_menu.connect_action.isEnabled()
                seen.append(True)
                timer.stop()
                home.finish()
            elif ticks[0] > 250:
                timer.stop()
                app.quit()
        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(40)
        assert main(['--launch-dir', sys.argv[1]]) == 0
        assert seen, 'independent home failed to become ready'
        print('HOME_READY_NO_MODEL')
    ''')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', SICO_MODEL='incomplete-model',
                       SICO_PROVIDER_CONFIG=str(tmp_path / 'missing-model-config.json'))
    result = None
    try:
        result = subprocess.run([PYTHON, '-c', script, str(tmp_path)], env=environment,
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'HOME_READY_NO_MODEL' in result.stdout
        assert 'Execute in the target Virtuoso CIW' not in result.stdout + result.stderr
        assert not list((tmp_path / '.sico/ai/agent').glob('connect-*'))
    finally:
        found = discover_project(tmp_path)
        if found.service is not None:
            stop_service(found.service)


def test_shell_entry_preserves_eda_environment_before_python_sanitization(tmp_path):
    root = Path(__file__).resolve().parents[4]
    capture = tmp_path / 'capture.json'
    interpreter = tmp_path / 'capture-python'
    interpreter.write_text('#!' + PYTHON + '\nimport json,os\n'
                           'open(os.environ["CAPTURE"],"w").write(json.dumps(dict(os.environ)))\n')
    interpreter.chmod(0o700)
    environment = dict(os.environ, SICO_HOME=str(root), SICO_PYTHON=str(interpreter),
                       CAPTURE=str(capture), LD_LIBRARY_PATH='/eda/lib',
                       SICO_SYSTEM_LD_LIBRARY_PATH='/sico/lib')
    for key in tuple(environment):
        if key.startswith('SICO_VIRTUOSO_'):
            del environment[key]
    subprocess.run([str(root / 'bin/sico')], env=environment, check=True, timeout=5)
    captured = json.loads(capture.read_text())
    assert captured['SICO_VIRTUOSO_LD_LIBRARY_PATH'] == '/eda/lib'
    assert captured['LD_LIBRARY_PATH'] == '/sico/lib'
