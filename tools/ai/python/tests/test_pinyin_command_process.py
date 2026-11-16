"""Real command-wrapper status, descriptor and owner-death contracts."""

import json
import os
from pathlib import Path
import signal
import subprocess

import pytest

from test_pinyin_gui_process import ROOT, PYTHON, display, until, pytestmark


@pytest.mark.parametrize('mode,status', [('exit', 17), ('signal', 143)])
def test_lsf_wrapper_preserves_status_arguments_and_control_fd(tmp_path, mode, status):
    with display(tmp_path) as env:
        read_fd, write_fd = os.pipe()
        env['SICO_AI_CONTROL_FD'] = str(read_fd)
        code = '''
import json,os,signal,sys
from pathlib import Path
assert os.environ['QT_IM_MODULE']=='ibus'
assert Path(os.environ['IBUS_ADDRESS_FILE']).is_file()
assert os.read(int(os.environ['SICO_AI_CONTROL_FD']),4)==b'ping'
Path('received.json').write_text(json.dumps(sys.argv[2:]))
if sys.argv[1]=='signal':os.kill(os.getpid(),signal.SIGTERM)
sys.exit(17)
'''
        try:
            os.write(write_fd, b'ping')
            result = subprocess.run([str(ROOT / 'tools/ai/bin/sico-ai-pinyin'), '--',
                PYTHON, '-s', '-c', code, mode, '中文 空格', "'"], cwd=tmp_path,
                env=env, pass_fds=(read_fd,), capture_output=True, text=True, timeout=30)
            assert result.returncode == status, result.stderr
            assert json.loads((tmp_path / 'received.json').read_text()) == ['中文 空格', "'"]
            assert not list(tmp_path.glob('.sico/ai/runtime/sico-ai-pinyin.*'))
        finally:
            os.close(read_fd)
            os.close(write_fd)


@pytest.mark.parametrize('number', [signal.SIGTERM, signal.SIGKILL])
def test_lsf_wrapper_owner_death_reaps_command_and_bus(tmp_path, number):
    code = '''
import os,subprocess,time
from pathlib import Path
child=subprocess.Popen(['/bin/sleep','60'],start_new_session=True)
Path('child.pid').write_text(str(child.pid))
time.sleep(60)
'''
    with display(tmp_path) as env, (tmp_path / 'wrapper.log').open('w') as log:
        process = subprocess.Popen([str(ROOT / 'tools/ai/bin/sico-ai-pinyin'), '--',
            PYTHON, '-s', '-c', code], env=env, cwd=tmp_path, stdout=log, stderr=log)
        try:
            until(lambda: (tmp_path / 'child.pid').exists())
            child = int((tmp_path / 'child.pid').read_text())
            process.send_signal(number)
            assert process.wait(8) == (143 if number == signal.SIGTERM else -signal.SIGKILL)
            until(lambda: not Path('/proc', str(child)).exists(), 8)
            until(lambda: not list(tmp_path.glob('.sico/ai/runtime/sico-ai-pinyin.*')), 8)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(5)
