"""Launch configuration must not acquire resources merely by being imported."""

from __future__ import annotations

import os
import subprocess
import sys


def test_import_keeps_launch_side_effects_deferred(tmp_path):
    environment = dict(os.environ)
    environment.update({
        "CAD_CODEX_HOME": str(tmp_path / "private-home"),
        "CAD_TEMP_DIR": str(tmp_path / "private-temp"),
        "CAD_PYTHON": str(tmp_path / "missing-python"),
        "SICO_LEGACY_0114": "invalid-until-called",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    probe = '''
import importlib
import sys

def no_launch_side_effect(event, arguments):
    if event in {"subprocess.Popen", "socket.bind", "socket.connect", "os.mkdir", "os.chmod", "os.symlink", "os.remove"}:
        raise AssertionError("unexpected import-time side effect: " + event)

sys.addaudithook(no_launch_side_effect)
for module in ("launch", "launch_executable", "launch_policy", "codex_launch",
               "claude_launch", "launch_environment", "launch_bootstrap"):
    importlib.import_module("cadai." + module)
assert "cadai.responses_wrapper" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-s", "-c", probe], env=environment,
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "private-home").exists()
    assert not (tmp_path / "private-temp").exists()
