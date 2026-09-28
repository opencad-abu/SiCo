from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "runtime_mode", ("oneshot", "persistent_idle", "persistent_busy")
)
@pytest.mark.parametrize("exit_kind", ("sigterm", "sigint", "quit", "exception"))
def test_gui_exit_drains_workers_before_return(
    tmp_path: Path, exit_kind: str, runtime_mode: str, protected_worker_context
) -> None:
    pytest.importorskip("PyQt5.QtCore")
    from project_worker_fake import fake_ocean

    fake_ocean(tmp_path)
    script = r"""
import os, signal, sys
from pathlib import Path
from PyQt5.QtCore import QCoreApplication, QTimer
from mtsnetlistor.gui.controller import MtsController
from mtsnetlistor.gui.lifecycle import managed_gui_shutdown
from mtsnetlistor.process import run_isolated
import mtsnetlistor.gui.controller as module

root = Path(sys.argv[1])
marker = root / "worker.pid"
mode = sys.argv[2]
runtime_mode = sys.argv[3]
app = QCoreApplication([])

def generate(*args, **kwargs):
    if runtime_mode != "oneshot":
        task = root / "task.ocn"
        task.write_text(("FAKE_HANG" if runtime_mode == "persistent_busy" else "t") + "\nexit()\n")
        result = run_isolated([str(root/'ocean'), '-replay', str(task)], cwd=root, environment=os.environ, cancel=kwargs['cancel_event'])
        marker.write_text(str(result.pid))
        return result
    code = "import os,time; open(%r, 'w').write(str(os.getpid())); time.sleep(60)" % str(marker)
    return run_isolated([sys.executable, '-c', code], cwd=root, environment=os.environ, cancel=kwargs['cancel_event'])

module.generate = generate
from mtsnetlistor.model import SourceDesign, NetlistRequest
cds = root / 'cds.lib'
cds.write_text('')
controller = MtsController(persistent_source=runtime_mode != 'oneshot')
controller.generate(NetlistRequest(SourceDesign(cds, 'test', 'cell')))

class Window:
    def close(self):
        controller.close(wait=True)
        app.quit()

triggered = False
timer = QTimer()
def trigger():
    global triggered
    if runtime_mode == 'persistent_busy' and (root/'entered').exists():
        marker.write_text(str(controller._project_worker.process.pid))
    if not marker.exists() or triggered:
        return
    triggered = True
    if mode in ('sigterm', 'sigint'):
        os.kill(os.getpid(), signal.SIGTERM if mode == 'sigterm' else signal.SIGINT)
    else:
        app.quit()
timer.timeout.connect(trigger)
timer.start(20)
previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
try:
    with managed_gui_shutdown(app, Window()):
        app.exec_()
        if mode == 'exception':
            raise RuntimeError('injected event loop failure')
except RuntimeError as exc:
    assert mode == 'exception' and str(exc) == 'injected event loop failure'
pid = int(marker.read_text())
stat = Path('/proc/%d/stat' % pid)
assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
assert all(signal.getsignal(sig) == handler for sig, handler in previous.items())
helper = root/'helper.pid'
if helper.exists():
    stat = Path('/proc/%s/stat' % helper.read_text())
    assert not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z'
print('GUI_WORKERS_DRAINED')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), exit_kind, runtime_mode],
        capture_output=True,
        text=True,
        timeout=15,
    )
    try:
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "GUI_WORKERS_DRAINED" in completed.stdout
    finally:
        marker = tmp_path / "worker.pid"
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
